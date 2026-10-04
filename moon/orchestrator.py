"""
MoonAI Execution Orchestrator

Wires the 12 ``moon/`` modules into a single async execution pipeline:

    User prompt
        -> TaskState (SQLite persist)
        -> ModelRegistry + RateLimiter (select healthy model)
        -> LiteLLM proxy HTTP call (aiohttp)
        -> FailureClassifier (on error)
        -> CompletionGate (JSON auto-repair of the response)
        -> ReviewLoop (guardrails)
        -> TaskState (save & persist)

Exposes ``python -m moon "<prompt>"`` as a CLI.
"""
from __future__ import annotations

import asyncio
import json
import logging
import sys
import time
from typing import Any, Dict, List, Optional, Callable, Tuple

from aiohttp import ClientSession, ClientTimeout, TCPConnector, ThreadedResolver

if sys.platform == "win32":
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

from .task_state import TaskState, Todo, TaskStateManager
from .model_registry import ModelCapabilityRegistry
from .rate_limiter import RateLimiter
from .completion_gate import CompletionGate
from .failure_classifier import FailureClassifier, FailureRecoveryManager
from .handoff_manager import CapabilityHandoffManager, HandoffManager
from .review_loop import ReviewLoop
from .context_recovery import ModelStopDetector, ContextRecoveryManager, IdempotencyChecker
from .orchestrator_logging import OrchestrationLogger, LogLevel, EventType
from .health_checker import HealthChecker
from .semantic_cache import SemanticCache


import os
from dotenv import load_dotenv

load_dotenv()

# ---------------------------------------------------------------- config defaults
PROXY_URL = os.getenv("LITELLM_PROXY_URL", "http://127.0.0.1:4000")
PROXY_MASTER_KEY = os.getenv("LITELLM_MASTER_KEY", "sk-moon-router-2026-local")
PROXY_TIMEOUT = ClientTimeout(total=300, connect=30)
LOG_DIR = ".moon/logs"
TASK_STATE_DIR = ".moon/tasks"
CACHE_DIR = ".moon/cache"
HEALTH_DIR = ".moon/health"


logger = logging.getLogger("moon.orchestrator")


# ---------------------------------------------------------------- HTTP helpers
def _get_proxy_headers() -> Dict[str, str]:
    """Get HTTP headers for LiteLLM proxy calls."""
    headers = {"Content-Type": "application/json"}
    if PROXY_MASTER_KEY:
        headers["Authorization"] = f"Bearer {PROXY_MASTER_KEY}"
    return headers


async def _extract_content(response: Dict[str, Any]) -> str:
    """Best-effort extraction of text content from a LiteLLM completion response."""
    try:
        choice = response.get("choices", [{}])[0]
        msg = choice.get("message", {}) or {}
        content = msg.get("content")
        if isinstance(content, str) and content:
            return content
        tool_calls = msg.get("tool_calls")
        if isinstance(tool_calls, list) and tool_calls:
            return json.dumps(tool_calls)
        return ""
    except Exception:
        return ""


async def _call_proxy(
    session: ClientSession,
    group: str,
    messages: List[Dict[str, Any]],
    timeout: ClientTimeout = PROXY_TIMEOUT,
) -> Dict[str, Any]:
    """POST /v1/chat/completions to the LiteLLM proxy for *group*.

    Raises :class:`asyncio.CancelledError` on client cancellation and
    :class:`RuntimeError` (wrapped) on network / HTTP errors so the caller can
    classify and recover.
    """
    url = f"{PROXY_URL}/v1/chat/completions"
    body: Dict[str, Any] = {
        "model": group,
        "messages": messages,
        "max_tokens": 8192,
        "stream": False,
    }
    headers = _get_proxy_headers()
    async with session.post(url, json=body, headers=headers, timeout=timeout) as resp:
        if resp.status == 200:
            raw = await resp.text()
            return json.loads(raw)
        body_text = await resp.text()
        http_error = RuntimeError(
            f"proxy returned {resp.status} for {group}: {body_text[:500]}"
        )
        http_error.__cause__ = None
        http_error.__context__ = None
        raise http_error


async def _call_proxy_stream(
    session: ClientSession,
    group: str,
    messages: List[Dict[str, Any]],
    on_chunk_callback: Optional[Callable[[str], None]] = None,
    timeout: ClientTimeout = PROXY_TIMEOUT,
) -> Tuple[str, Optional[float]]:
    """POST /v1/chat/completions with stream=True payload to LiteLLM proxy for *group*.

    Reads SSE chunks asynchronously ('data: {...}'), measures TTFT on the first
    token chunk, invokes *on_chunk_callback* for live CLI rendering, and returns
    the fully assembled content string and measured TTFT in milliseconds.
    """
    url = f"{PROXY_URL}/v1/chat/completions"
    body: Dict[str, Any] = {
        "model": group,
        "messages": messages,
        "max_tokens": 8192,
        "stream": True,
    }
    headers = _get_proxy_headers()
    t0 = time.perf_counter()
    ttft_ms: Optional[float] = None
    chunks: List[str] = []

    async with session.post(url, json=body, headers=headers, timeout=timeout) as resp:
        if resp.status != 200:
            body_text = await resp.text()
            http_error = RuntimeError(
                f"proxy returned {resp.status} for {group} [stream]: {body_text[:500]}"
            )
            http_error.__cause__ = None
            http_error.__context__ = None
            raise http_error

        # Process SSE lines asynchronously
        async for line in resp.content:
            line_str = line.decode("utf-8").strip()
            if not line_str or not line_str.startswith("data: "):
                continue

            data_str = line_str[6:].strip()
            if data_str == "[DONE]":
                break

            try:
                payload = json.loads(data_str)
                choices = payload.get("choices", [])
                if not choices:
                    continue
                delta = choices[0].get("delta", {}) or {}
                content_chunk = delta.get("content") or ""

                if content_chunk:
                    if ttft_ms is None:
                        ttft_ms = (time.perf_counter() - t0) * 1000.0
                    chunks.append(content_chunk)
                    if on_chunk_callback:
                        try:
                            on_chunk_callback(content_chunk)
                        except Exception:
                            pass
            except Exception:
                continue

    full_content = "".join(chunks)
    return full_content, ttft_ms


async def _health_ping(
    session: ClientSession,
    model_name: str,
    provider: str,
    timeout: ClientTimeout = PROXY_TIMEOUT,
) -> Dict[str, Any]:
    """Send a 1-token ping to the LiteLLM proxy and measure TTFT + latency."""
    t0 = time.perf_counter()
    try:
        resp = await _call_proxy(
            session,
            model_name,
            [
                {"role": "user", "content": "Reply with exactly: ok"}
            ],
            timeout=timeout,
        )
    except Exception as exc:
        ttft = (time.perf_counter() - t0) * 1000
        return {
            "ttft": ttft if ttft < 15000 else None,
            "total_latency": ttft if ttft < 60000 else None,
            "success": False,
            "error": repr(exc),
        }
    ttft_ms = (time.perf_counter() - t0) * 1000

    # LiteLLM can return the first token in content; treat presence of content
    # as success unless there is obviously nothing usable.
    content = (resp.get("choices", [{}])[0].get("message") or {}).get("content")
    success = bool(content) and resp.get("finish_reason") != "length"

    return {
        "ttft": ttft_ms if success else None,
        "total_latency": ttft_ms,
        "success": success,
        "content": content[:32] if content else None,
    }


# ---------------------------------------------------------------- orchestrator
class MoonOrchestrator:
    """Top-level async execution pipeline for a single user prompt."""

    def __init__(
        self,
        prompt: str,
        model_registry: Optional[ModelCapabilityRegistry] = None,
        task_state_dir: str = TASK_STATE_DIR,
        cache_dir: str = CACHE_DIR,
        log_dir: str = LOG_DIR,
        stream: bool = False,
    ):
        self.prompt = prompt
        self.stream = stream

        # Components
        self.model_registry = model_registry or ModelCapabilityRegistry()
        self.rate_limiter = self.model_registry.rate_limiter
        self.task_manager = TaskStateManager(task_state_dir)
        self.semantic_cache = SemanticCache(cache_dir)
        self.health_checker = HealthChecker()
        self.failure_classifier = FailureClassifier(self.model_registry)
        self.failure_recovery = FailureRecoveryManager(
            self.model_registry,
            CapabilityHandoffManager(self.model_registry),
        )
        self.handoff_manager = HandoffManager(self.model_registry)
        self.review_loop = ReviewLoop(
            self.model_registry,
            CompletionGate(self.model_registry),
        )
        self.model_stop_detector = ModelStopDetector(self.model_registry)
        self.context_recovery = ContextRecoveryManager(
            self.model_registry,
            CapabilityHandoffManager(self.model_registry),
        )
        self.idempotency_checker = IdempotencyChecker()
        self.logger = OrchestrationLogger(log_directory=log_dir)

        # Execution state
        self.task: Optional[TaskState] = None
        self.session: Optional[ClientSession] = None
        self.report: Dict[str, Any] = {
            "task_id": None,
            "status": "pending",
            "selected_model": None,
            "steps": [],
            "failures": [],
            "final_content": None,
            "verification_passed": False,
        }
        self._stopped = False

    # ---------------------------------------------------------------- pipeline
    async def run(self) -> Dict[str, Any]:
        """Run the full pipeline for the prompt and return the execution report."""
        self.report["task_id"] = self.report.get("task_id") or f"task_{int(time.time())}_{id(self):x}"
        self.logger.log(EventType.TASK_START, task_id=self.report["task_id"],
                        message="Orchestration started", data={"prompt": self.prompt})

        self.task = self.task_manager.create_task(self.prompt)
        self.model_stop_detector = ModelStopDetector(self.model_registry)

        try:
            await self._plan_phase()
            await self._select_model_phase()
            await self._execute_phase()
            await self._verify_phase()
            await self._review_phase()
            await self._complete_phase()
        except asyncio.CancelledError:
            self._stopped = True
            self.logger.log(EventType.TASK_FAIL, task_id=self.report["task_id"],
                            message="Cancelled", level=LogLevel.WARN)
            self.report["status"] = "cancelled"
        except Exception as exc:  # noqa: BLE001
            self.logger.log(EventType.TASK_FAIL, task_id=self.report["task_id"],
                            message=f"Orchestration failed: {exc}", level=LogLevel.ERROR,
                            data={"error": str(exc)})
            self.report["status"] = "failed"
            self.report["failures"].append({"type": "orchestration_error",
                                            "message": str(exc)})
        finally:
            if self.session and not self.session.closed:
                await self.session.close()

        self.report["status"] = self.report["status"] or ("completed" if self.report["verification_passed"]
                                                           else "failed")
        return self.report

    # ---------------------------------------------------------------- phases
    async def _plan_phase(self):
        logger.info("phase=plan")
        self.task.plan = {
            "steps": [
                "Select a healthy model for the request",
                "Execute the request via the LiteLLM proxy",
                "Repair / verify the response",
                "Run the review loop",
                "Persist the task",
            ]
        }
        self.task.add_todo(title="Plan", description="Breakdown of execution steps")
        self.task.todos[-1].status = "COMPLETED"
        self.task.completed_work.append("Plan created")

    async def _select_model_phase(self):
        logger.info("phase=select-model")
        capability = self._infer_capability()
        model = self.model_registry.select_model_for_capability(capability)
        if model is None:
            model = self.model_registry.get_models_in_group("coding")[0]
        
        target_group = getattr(model, 'group', None) or capability or "coding"
        self.report["target_group"] = target_group
        self.report["selected_model"] = model.name
        self.task.active_model = model.name
        self.task.model_history.append({"model": model.name,
                                        "switched_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
                                        "reason": "initial selection"})
        self.logger.log(EventType.MODEL_SWITCH, task_id=self.report["task_id"],
                        message=f"Selected {model.name} (group: {target_group})",
                        data={"model": model.name, "capability": capability, "group": target_group})

    async def _execute_phase(self):
        logger.info("phase=execute")
        task = self.task
        session = ClientSession(connector=TCPConnector(resolver=ThreadedResolver()), timeout=PROXY_TIMEOUT)
        self.session = session
        messages: List[Dict[str, Any]] = [
            {"role": "system", "content": "You are a helpful assistant."},
            {"role": "user", "content": self.prompt},
        ]

        # Cache lookup
        cached = await asyncio.to_thread(
            self.semantic_cache.get, self.prompt, self.report["selected_model"]
        )
        if cached:
            logger.info("cache-hit")
            task.completed_work.append("Served from cache")
            self.report["final_content"] = cached.get("answer") or cached.get("response")
            return

        # Ping health for the selected model before routing
        await self._collect_health_pings(session)

        # Execute via proxy using group name
        target_group = self.report.get("target_group") or "coding"
        try:
            if self.stream:
                def _stream_on_token(token_chunk: str):
                    sys.stdout.write(token_chunk)
                    sys.stdout.flush()

                content, ttft_ms = await _call_proxy_stream(
                    session, target_group, messages, on_chunk_callback=_stream_on_token
                )
                if ttft_ms is not None and self.report.get("selected_model"):
                    nm = self.report["selected_model"]
                    prov = nm.split("/", 1)[0] if "/" in nm else "unknown"
                    self.health_checker.update_health(
                        model_name=nm,
                        provider=prov,
                        ttft=ttft_ms / 1000.0,
                        total_latency=ttft_ms / 1000.0,
                        success=True
                    )
            else:
                response = await _call_proxy(session, target_group, messages)
                content = await _extract_content(response)

            self.report["final_content"] = content

            # Check if returned content text contains a refusal (e.g. "cannot execute terminal commands")
            inferred_error = self.failure_classifier._infer_error_type(content)
            if inferred_error == "CAPABILITY_UNSUPPORTED":
                raise RuntimeError(f"Model capability refusal in response text: {content[:200]}")

            self.report["steps"].append({
                "step": "execute",
                "status": "ok",
                "model": self.report["selected_model"],
                "group": target_group,
                "streaming": self.stream
            })
            self._record_usage(task, self.report["selected_model"], content)

            # Stop-detection / early termination
            stop = self.model_stop_detector.detect_stop(content, task)
            if stop:
                logger.warning("stop-detected: %s", stop)
                self.report["steps"].append({"step": "stop-detection", "detected": str(stop)})

            # Idempotency
            self.idempotency_checker.record_operation(
                "completion", {"prompt": self.prompt}, content
            )
        except Exception as exc:  # noqa: BLE001
            logger.exception("execution-error")
            self.report["steps"].append({"step": "execute", "status": "error",
                                          "error": str(exc), "model": self.report["selected_model"]})
            await self._recover_from_failure(task, target_group, exc)

    async def _collect_health_pings(self, session: ClientSession):
        """Fire lightweight health pings for a few top candidates to gather TTFT."""
        candidates = self.model_registry.get_models_in_group("coding")[:4]
        tasks = [
            _health_ping(session, m.name, m.name.split("/", 1)[0])
            for m in candidates
            if m.is_available()
        ]
        if not tasks:
            return
        results = await asyncio.gather(*tasks, return_exceptions=True)
        for model, metrics in zip(candidates, results):
            if isinstance(metrics, Exception):
                logger.debug("health-ping %s: %s", model.name, metrics)
                continue
            self.health_checker.update_health(
                model_name=model.name,
                provider=model.name.split("/", 1)[0],
                ttft=(metrics.get("ttft") / 1000.0) if metrics.get("ttft") else None,
                total_latency=(metrics.get("total_latency") / 1000.0) if metrics.get("total_latency") else None,
                success=metrics.get("success", False),
            )
            logger.debug("health-ping %s: ttft=%s success=%s",
                          model.name, metrics.get("ttft"), metrics.get("success"))

    async def _recover_from_failure(self, task: TaskState, model_name: str, exc: Exception):
        error_msg = str(exc)
        failure = self.failure_classifier.classify_failure(error_msg)
        self.report["failures"].append(failure)
        logger.info("classified failure: %s -> %s", error_msg[:60], failure["failure_type"])

        # --- Capability refusal fast-path ---
        # If the model said "I can't execute code" / "tool_use_not_supported",
        # skip the soft-retry entirely and jump to hard handoff with execution
        # capability requirements.
        if failure["failure_type"] == "CAPABILITY_UNSUPPORTED":
            logger.warning("capability-unsupported detected, skipping retries")
            handoff = self.handoff_manager.handle_capability_failure(
                task, Todo("todo_1", "Handoff", description="Capability handoff after refusal"),
                ["coding", "terminal_execution", "tool_use"], error_msg
            )
            if handoff:
                self.report["steps"].append({"step": "capability_handoff", "reason": "CAPABILITY_UNSUPPORTED",
                                              "target_model": handoff.get("target_model")})
                try:
                    target_model_name = handoff.get("target_model", model_name)
                    response = await _call_proxy(self.session, target_model_name, [{"role": "user", "content": self.prompt}])
                    content = await _extract_content(response)
                    self.report["final_content"] = content
                    self.report["selected_model"] = handoff.get("target_model")
                    self.report["steps"].append({"step": "execute-after-capability-handoff", "status": "ok"})
                    self._record_usage(task, handoff.get("target_model", "unknown"), content)
                except Exception as exc2:
                    logger.exception("post-capability-handoff-failed")
                    self.report["failures"].append({"type": "post_capability_handoff_error", "message": str(exc2)})
            return

        # Attempt a soft recovery first (retry same model if transient)
        recovery = self.failure_recovery.attempt_recovery(task, Todo("todo_1", "Recover",
                                                                     description="Recovery attempt"),
                                                         failure)
        if recovery and recovery.get("recovery_type") in ("retry_same_model", "retry_with_backoff",
                                                          "log_and_retry"):
            try:
                response = await _call_proxy(self.session, model_name, [{"role": "user", "content": self.prompt}])
                content = await _extract_content(response)
                self.report["final_content"] = content
                self.report["steps"].append({"step": "recovery", "status": "ok", "model": model_name})
                self._record_usage(task, model_name, content)
                return
            except Exception:
                pass

        # Hard handoff to next model
        handoff = self.handoff_manager.handle_capability_failure(
            task, Todo("todo_1", "Handoff", description="Handoff after failure"),
            [failure["failure_type"]], error_msg
        )
        if handoff:
            self.report["steps"].append({"step": "handoff", "target_model": handoff.get("target_model")})
            # Retry execution on the new model
            try:
                response = await _call_proxy(self.session, handoff["target_model"], [{"role": "user", "content": self.prompt}])
                content = await _extract_content(response)
                self.report["final_content"] = content
                self.report["selected_model"] = handoff["target_model"]
                self.report["steps"].append({"step": "execute-after-handoff", "status": "ok", "model": handoff["target_model"]})
                self._record_usage(task, handoff["target_model"], content)
            except Exception as exc2:  # noqa: BLE001
                logger.exception("post-handoff-failed")
                self.report["failures"].append({"type": "post_handoff_error", "message": str(exc2)})

    async def _verify_phase(self):
        logger.info("phase=verify")
        cg = CompletionGate(self.model_registry)
        # JSON auto-repair: the response from free models is often dirty;
        # pass it through the completion gate's repair pipeline.
        content = self.report.get("final_content")
        if content:
            repaired = cg.parse_model_response(content)
            if repaired is not None:
                self.report["final_content"] = json.dumps(repaired)
                self.report["steps"].append({"step": "json-repair", "status": "ok"})
            else:
                self.report["steps"].append({"step": "json-repair", "status": "skipped", "reason": "not JSON"})
        self.report["verification_passed"] = cg.can_complete_task(task) if (task := self.task) else True
        self.logger.log(EventType.VERIFICATION_COMPLETE, task_id=self.report["task_id"],
                        message="Verification complete", data={"passed": self.report["verification_passed"]})

    async def _review_phase(self):
        logger.info("phase=review")
        if not self.report.get("verification_passed", False):
            return
        try:
            cycle_result = self.review_loop.run_review_cycle(self.task)
            self.report["steps"].append({"step": "review-cycle", "result": cycle_result.get("success")})
        except Exception as exc:  # noqa: BLE001
            logger.warning("review-cycle-skipped: %s", exc)
            self.report["steps"].append({"step": "review-cycle", "result": "skipped", "error": str(exc)})

    async def _complete_phase(self):
        logger.info("phase=complete")
        task = self.task
        if task:
            task.status = "COMPLETED"
            self.report["status"] = "completed"
            task.verification_status = {"verification_passed": self.report["verification_passed"]}
            self.task_manager.save_task(task)
            self.review_loop.reset_review_count(task)
            self.logger.log(EventType.TASK_COMPLETE, task_id=self.report["task_id"],
                            message="Task completed",
                            data={"model_history": task.model_history[-3:], "content_len": len(self.report.get("final_content") or "")})
            self.report["steps"].append({"step": "persist", "status": "ok", "task_id": task.task_id})

    # ---------------------------------------------------------------- helpers
    def _infer_capability(self) -> str:
        p = self.prompt.lower()
        if any(k in p for k in ("code", "implement", "build", "function", "script", "program", "test", "refactor", "api", "class", "python", "javascript", "fix", "debug")):
            return "coding"
        if any(k in p for k in ("reason", "plan", "think", "thinking", "explain", "why", "how", "analysis", "design", "architecture", "strategy")):
            return "reasoning"
        if any(k in p for k in ("web", "fetch", "research", "document", "write", "article", "summary", "translate")):
            return "reasoning"
        return "coding"

    def _record_usage(self, task: TaskState, model_name: str, content: str):
        task.active_model = model_name
        task.model_history.append({
            "model": model_name,
            "switched_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
            "reason": "execution",
        })
        if hasattr(task, "completed_work"):
            task.completed_work.append(f"Completed: {self.prompt[:120]}")


# ---------------------------------------------------------------- CLI
if __name__ == "__main__":
    if len(sys.argv) > 1:
        prompt = " ".join(sys.argv[1:])
    else:
        prompt = sys.stdin.read().strip() or "Hello — confirm the MoonAI orchestrator is running"

    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s %(levelname)s %(name)s: %(message)s")

    orch = MoonOrchestrator(prompt)
    report = asyncio.run(orch.run())
    print("\n--- MoonAI execution report ---")
    print(f"status       : {report['status']}")
    print(f"task_id      : {report['task_id']}")
    print(f"selected_model: {report['selected_model']}")
    print(f"steps        : {len(report['steps'])}")
    print(f"verification : {report['verification_passed']}")
    print(f"failures     : {len(report['failures'])}")
    print(f"content      : {report['final_content']}")
    print("---------------------------------\n")
