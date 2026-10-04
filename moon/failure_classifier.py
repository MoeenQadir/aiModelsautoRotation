"""
MoonAI Failure Classification and Recovery System
Handles failure detection, classification, and recovery strategies.
"""

import json
from typing import Dict, Any, List, Optional, Callable
from datetime import datetime

from .task_state import TaskState, Todo
from .model_registry import ModelCapabilityRegistry
from .handoff_manager import CapabilityHandoffManager, HandoffManager


class FailureClassifier:
    """Classifies failures and determines appropriate recovery strategies."""
    
    def __init__(self, model_registry: ModelCapabilityRegistry):
        self.model_registry = model_registry
        self.failure_history: List[Dict[str, Any]] = []
    
    def classify_failure(self, error_message: str, 
                         error_type: str = None,
                         context: Dict[str, Any] = None) -> Dict[str, Any]:
        """Classify a failure and return recovery strategy."""
        # Use provided error type or infer from message
        if not error_type:
            error_type = self._infer_error_type(error_message)
        
        # Get recovery strategy from registry
        strategy = self.model_registry.get_failure_strategy(error_type)
        
        # Record failure
        failure_record = {
            "error_type": error_type,
            "error_message": error_message,
            "context": context or {},
            "recovery_strategy": strategy,
            "timestamp": datetime.now().isoformat()
        }
        
        self.failure_history.append(failure_record)
        
        return {
            "failure_type": error_type,
            "error_message": error_message,
            "recovery_strategy": strategy,
            "context": context
        }
    
    def _infer_error_type(self, error_message: str) -> str:
        """Infer error type from error message.

        Explicit category prefixes ("network error", "auth error", ...) are
        checked before generic symptom keywords ("timeout", ...) so that a
        message like "Network error: Connection timeout" classifies as
        NETWORK_ERROR rather than TIMEOUT.
        """
        error_message = error_message.lower()

        # --- Deterministic auth failures take priority over category matching.
        # A 401 / ACCESS_TOKEN_TYPE_UNSUPPORTED is not a transient network
        # problem; the key/credential is simply wrong for the endpoint. Retrying
        # or putting the model on cooldown wastes seconds that are better spent
        # falling to the next provider in the chain.
        auth_markers = (
            "401",
            "unauthorized",
            "unauthenticated",
            "access token type unsupported",
            "access_token_type_unsupported",
            "invalid authentication credentials",
            "invalid api key",
            "authentication error",
            "auth error",
            "auth_error",
            "auth failure",
            "auth_failure",
        )
        if any(marker in error_message for marker in auth_markers):
            return "AUTH_FAILURE"

        # --- Capability refusal / unsupported feature detection ---
        # Models that cannot execute terminal commands, use tools, or run code
        # return text refusals. These are deterministic — the model will never
        # gain the capability mid-session. Rotate immediately.
        capability_markers = (
            "cannot execute terminal commands",
            "can't execute terminal commands",
            "cannot execute commands",
            "can't execute commands",
            "i don't have access to terminal",
            "i do not have access to terminal",
            "don't have direct access to a terminal",
            "don't have access to a terminal",
            "don't have access to the terminal",
            "do not have direct access to a terminal",
            "i am an ai text-based model and don't have",
            "i am a text model and cannot run code",
            "i'm a text model and cannot run code",
            "i cannot run code",
            "i can't run code",
            "i cannot execute code",
            "i can't execute code",
            "function_calling_not_supported",
            "function calling is not supported",
            "tool_use_failed",
            "tool use is not supported",
            "tool_use_not_supported",
            "does not support function calling",
            "does not support tool use",
            "does not support tools",
            "tool_calls is not supported",
            "tools parameter is not supported",
            "unable to execute",
            "no code execution capability",
            "code execution is not available",
            "cannot perform terminal operations",
            "i cannot access the file system",
            "i can't access the file system",
            "i don't have the ability to run",
            "i do not have the ability to run",
        )
        if any(marker in error_message for marker in capability_markers):
            return "CAPABILITY_UNSUPPORTED"

        # --- Missing data / dataset detection ---
        missing_data_markers = (
            "no_data",
            "no candles found",
            "no data found",
            "data not found",
            "missing dataset",
            "dataset not found",
            "empty dataset",
            "0 candles found",
        )
        if any(marker in error_message for marker in missing_data_markers):
            return "MISSING_DATA"

        # --- Explicit category mentions first ---
        if "permission" in error_message or "access denied" in error_message:
            return "PERMISSION_ERROR"
        elif "rate limit" in error_message or "too many requests" in error_message:
            return "RATE_LIMIT"
        elif "network error" in error_message or "connection failed" in error_message:
            return "NETWORK_ERROR"
        elif "authentication error" in error_message or "invalid api key" in error_message:
            return "AUTH_ERROR"
        elif "provider error" in error_message or "service unavailable" in error_message:
            return "PROVIDER_ERROR"
        elif "tool error" in error_message or "invalid tool call" in error_message:
            return "TOOL_ERROR"
        elif "syntax error" in error_message or "invalid syntax" in error_message:
            return "SYNTAX_ERROR"
        elif "build error" in error_message or "compilation failed" in error_message:
            return "BUILD_ERROR"
        elif "test failure" in error_message or "assertion failed" in error_message:
            return "TEST_FAILURE"
        elif "dependency error" in error_message or "missing dependency" in error_message:
            return "DEPENDENCY_ERROR"
        elif "model error" in error_message or "model failed" in error_message:
            return "MODEL_ERROR"
        # --- Generic symptom keywords ---
        elif "timeout" in error_message or "took too long" in error_message:
            return "TIMEOUT"
        elif "context window" in error_message or "too large" in error_message:
            return "CONTEXT_LIMIT"
        
        return "UNKNOWN_ERROR"
    
    def get_recovery_actions(self, failure: Dict[str, Any], 
                             task: TaskState,
                             todo: Todo) -> List[Dict[str, Any]]:
        """Generate recovery actions based on failure type and context."""
        failure_type = failure["failure_type"]
        strategy = failure["recovery_strategy"]
        
        actions = []
        
        if failure_type == "PERMISSION_ERROR":
            # Capability handoff needed
            actions.append({
                "type": "capability_handoff",
                "description": "Find model with required capability",
                "capabilities": self._get_required_capabilities(todo)
            })

        elif failure_type == "PROVIDER_ERROR":
            # Rotate to a different deployment or provider
            actions.append({
                "type": "rotate_deployment",
                "description": "Switch to different deployment or provider"
            })

        elif failure_type == "RATE_LIMIT":
            # Rotate deployment
            actions.append({
                "type": "rotate_deployment",
                "description": "Switch to different deployment or provider"
            })
            actions.append({
                "type": "cooldown",
                "description": "Wait for rate limit cooldown",
                "cooldown_seconds": strategy.get("cooldown_seconds", 300)
            })
        
        elif failure_type == "TIMEOUT":
            # Retry with longer timeout
            actions.append({
                "type": "retry_with_longer_timeout",
                "description": "Retry operation with increased timeout"
            })
        
        elif failure_type == "CONTEXT_LIMIT":
            # Compact context or use larger context model
            actions.append({
                "type": "compact_context",
                "description": "Compact task context and continue"
            })
            actions.append({
                "type": "use_larger_context_model",
                "description": "Switch to model with larger context window"
            })
        
        elif failure_type == "TOOL_ERROR":
            # Fix tool call and retry
            actions.append({
                "type": "fix_tool_call",
                "description": "Identify and fix the tool call issue"
            })
        
        elif failure_type == "CAPABILITY_ERROR":
            # Capability-aware handoff
            actions.append({
                "type": "capability_handoff",
                "description": "Find model with required capability",
                "capabilities": self._get_required_capabilities(todo)
            })
        
        elif failure_type == "CAPABILITY_UNSUPPORTED":
            # Capability refusal — model lacks terminal/tool/code execution
            actions.append({
                "type": "capability_handoff",
                "description": "Model lacks required capability (terminal/tool/code execution) — rotate to capable model",
                "capabilities": self._get_required_capabilities_with_execution(todo)
            })

        elif failure_type == "MISSING_DATA":
            # Autonomous data auto-fetch / data generator action
            actions.append({
                "type": "auto_fetch_data",
                "description": "Autonomous Turbo Mode: Automatically fetch or download required dataset and re-execute task"
            })
        
        elif failure_type == "NETWORK_ERROR":
            # Retry with backoff
            actions.append({
                "type": "retry_with_backoff",
                "description": "Retry with exponential backoff"
            })
        
        elif failure_type == "SYNTAX_ERROR":
            # Create fix TODO
            actions.append({
                "type": "create_fix_todo",
                "description": "Create TODO to fix syntax error"
            })
        
        elif failure_type == "BUILD_ERROR":
            # Create fix TODO
            actions.append({
                "type": "create_fix_todo",
                "description": "Create TODO to fix build error"
            })
        
        elif failure_type == "TEST_FAILURE":
            # Create fix TODO
            actions.append({
                "type": "create_fix_todo",
                "description": "Create TODO to fix test failure"
            })
        
        elif failure_type == "DEPENDENCY_ERROR":
            # Install dependency or find alternative
            actions.append({
                "type": "install_dependency",
                "description": "Install missing dependency"
            })
        
        elif failure_type == "AUTH_ERROR":
            # Legacy AUTH_ERROR path — prefer AUTH_FAILURE above for explicit
            # 401 / ACCESS_TOKEN_TYPE_UNSUPPORTED, but route this the same way.
            actions.append({
                "type": "instant_fallback",
                "description": "Auth error (legacy AUTH_ERROR) - rotate to next provider immediately, no retries, no cooldown"
            })
            # Rotate API key or provider (legacy AUTH_ERROR path — prefer
            # AUTH_FAILURE above for explicit 401 / ACCESS_TOKEN_TYPE_UNSUPPORTED)
            actions.append({
                "type": "instant_fallback",
                "description": "Auth error - rotate API key or switch provider"
            })
        
        else:
            # Default recovery
            actions.append({
                "type": "log_and_retry",
                "description": "Log failure and retry with same model"
            })
        
        return actions
    
    def _get_required_capabilities(self, todo: Todo) -> List[str]:
        """Determine required capabilities for a todo."""
        # This is a simplified version - in a real implementation you would analyze
        # the todo description and determine required capabilities
        
        if "code" in todo.title.lower() or "implement" in todo.title.lower():
            return ["coding"]
        elif "analyze" in todo.title.lower() or "review" in todo.title.lower():
            return ["reasoning"]
        elif "test" in todo.title.lower():
            return ["testing"]
        elif "build" in todo.title.lower():
            return ["build"]
        
        return ["coding"]

    def _get_required_capabilities_with_execution(self, todo: Todo) -> List[str]:
        """Determine required capabilities for a todo that needs execution support.

        Unlike ``_get_required_capabilities``, this always includes
        ``terminal_execution`` and ``tool_use`` so the handoff targets a model
        that can actually run commands and use tools.
        """
        base = self._get_required_capabilities(todo)
        for cap in ("terminal_execution", "tool_use"):
            if cap not in base:
                base.append(cap)
        return base


class FailureRecoveryManager:
    """Manages failure recovery strategies."""

    def __init__(self, model_registry: ModelCapabilityRegistry,
                 handoff_manager: HandoffManager):
        self.model_registry = model_registry
        self.handoff_manager = handoff_manager
    
    def attempt_recovery(self, task: TaskState, todo: Todo,
                         failure: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """Attempt to recover from a failure."""
        failure_type = failure["failure_type"]

        # --- Instant-auth fast-failover ---
        # A 401 / ACCESS_TOKEN_TYPE_UNSUPPORTED is deterministic: the credential
        # is wrong for the endpoint. Do NOT retry (the retry will 401 again) and
        # do NOT put the model on a cooldown (the proxy's `allowed_fails`/`cooldown_time`
        # is already configured to keep that short). Jump to the next deployment
        # in the chain so the caller pays the misconfigured-key hit once.
        if failure_type in ("AUTH_FAILURE", "AUTH_ERROR", "CAPABILITY_UNSUPPORTED"):
            return {
                "recovery_type": "instant_fallback",
                "failure_type": failure_type,
                "action": "rotate_to_next_provider",
                "model": task.active_model.name if task.active_model else None,
                "success": True,
                "message": f"{failure_type} detected - rotating to next provider immediately (no retries, no backoff)"
            }

        # --- Normal recovery path ---
        strategy = self.model_registry.get_failure_strategy(failure_type)

        if strategy["recovery"] == "retry_same_model":
            return self._retry_same_model(task, todo, failure)
        
        elif strategy["recovery"] == "rotate_deployment":
            return self._rotate_deployment(task, todo, failure)
        
        elif strategy["recovery"] == "capability_aware_handoff":
            return self._capability_handoff(task, todo, failure)
        
        elif strategy["recovery"] == "compact_context_or_use_larger_context_model":
            return self._compact_context(task, todo, failure)
        
        elif strategy["recovery"] == "fix_tool_call_and_retry":
            return self._fix_tool_call(task, todo, failure)
        
        elif strategy["recovery"] == "find_capable_model":
            return self._find_capable_model(task, todo, failure)
        
        elif strategy["recovery"] == "retry_with_backoff":
            return self._retry_with_backoff(task, todo, failure)
        
        elif strategy["recovery"] in ["create_fix_todo", "review_and_fix"]:
            return self._create_fix_todo(task, todo, failure)
        
        elif strategy["recovery"] == "install_dependency_or_find_alternative":
            return self._install_dependency(task, todo, failure)
        
        elif strategy["recovery"] == "rotate_api_key_or_provider":
            return self._rotate_api_key(task, todo, failure)
        
        else:
            return self._log_and_retry(task, todo, failure)
    
    def _retry_same_model(self, task: TaskState, todo: Todo,
                          failure: Dict[str, Any]) -> Dict[str, Any]:
        """Retry with the same model."""
        task.retry_count += 1
        
        # Mark todo as in progress again
        task.update_todo_status(
            todo_id=todo.id,
            status="IN_PROGRESS",
            result=None,
            failure_reason=None
        )
        
        return {
            "recovery_type": "retry_same_model",
            "model": task.active_model.name if task.active_model else None,
            "retry_count": task.retry_count,
            "success": True
        }
    
    def _rotate_deployment(self, task: TaskState, todo: Todo,
                           failure: Dict[str, Any]) -> Dict[str, Any]:
        """Rotate to a different deployment or provider."""
        # Find next model in the same group
        if task.active_model:
            group_models = self.model_registry.get_models_in_group(task.active_model.group)
            if group_models:
                # Find next available model
                current_index = None
                for i, model in enumerate(group_models):
                    if model.name == task.active_model.name:
                        current_index = i
                        break
                
                if current_index is not None:
                    next_index = (current_index + 1) % len(group_models)
                    next_model = group_models[next_index]
                    
                    if next_model.is_available():
                        task.record_model_switch(
                            new_model=next_model.name,
                            reason="Deployment rotation"
                        )
                        
                        return {
                            "recovery_type": "rotate_deployment",
                            "model": next_model.name,
                            "success": True
                        }
        
        # If no next model found, fall back to capability handoff
        return self._capability_handoff(task, todo, failure)
    
    def _capability_handoff(self, task: TaskState, todo: Todo,
                            failure: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """Perform capability-aware handoff."""
        # Get required capabilities
        required_capabilities = self._get_required_capabilities(todo)
        
        # Find a model with the required capabilities
        target_model = self.model_registry.select_model_for_capabilities(
            required_capabilities,
            prefer_group=task.active_model.group if task.active_model else None
        )
        
        if target_model:
            handoff = self.handoff_manager.handle_capability_failure(
                task=task,
                todo=todo,
                required_capabilities=required_capabilities,
                error_message=failure["error_message"]
            )
            
            return {
                "recovery_type": "capability_handoff",
                "handoff": handoff,
                "success": handoff is not None
            }
        
        # If no capable model found, mark as blocked
        task.update_todo_status(
            todo_id=todo.id,
            status="BLOCKED",
            failure_reason=f"No capable model found for capabilities: {required_capabilities}"
        )
        
        return {
            "recovery_type": "capability_handoff",
            "success": False,
            "error": "No capable model found"
        }
    
    def _compact_context(self, task: TaskState, todo: Todo,
                          failure: Dict[str, Any]) -> Dict[str, Any]:
        """Compact context and continue."""
        # Generate compact recovery context
        recovery_context = task.generate_recovery_context()
        
        # Save context summary to task
        task.context_summary = recovery_context
        
        return {
            "recovery_type": "compact_context",
            "context_summary": recovery_context,
            "success": True
        }
    
    def _fix_tool_call(self, task: TaskState, todo: Todo,
                       failure: Dict[str, Any]) -> Dict[str, Any]:
        """Fix tool call and retry."""
        task.retry_count += 1
        
        # Mark todo as in progress again
        task.update_todo_status(
            todo_id=todo.id,
            status="IN_PROGRESS",
            result=None,
            failure_reason=None
        )
        
        return {
            "recovery_type": "fix_tool_call",
            "model": task.active_model.name if task.active_model else None,
            "retry_count": task.retry_count,
            "success": True
        }
    
    def _find_capable_model(self, task: TaskState, todo: Todo,
                            failure: Dict[str, Any]) -> Dict[str, Any]:
        """Find a capable model for the required operation."""
        # Get required capabilities
        required_capabilities = self._get_required_capabilities(todo)
        
        # Find a model with the required capabilities
        target_model = self.model_registry.select_model_for_capabilities(
            required_capabilities,
            prefer_group=task.active_model.group if task.active_model else None
        )
        
        if target_model:
            handoff = self.handoff_manager.handle_capability_failure(
                task=task,
                todo=todo,
                required_capabilities=required_capabilities,
                error_message=failure["error_message"]
            )
            
            return {
                "recovery_type": "find_capable_model",
                "handoff": handoff,
                "success": handoff is not None
            }
        
        # If no capable model found, mark as blocked
        task.update_todo_status(
            todo_id=todo.id,
            status="BLOCKED",
            failure_reason=f"No capable model found for capabilities: {required_capabilities}"
        )
        
        return {
            "recovery_type": "find_capable_model",
            "success": False,
            "error": "No capable model found"
        }
    
    def _retry_with_backoff(self, task: TaskState, todo: Todo,
                            failure: Dict[str, Any]) -> Dict[str, Any]:
        """Retry with exponential backoff."""
        task.retry_count += 1
        
        # Mark todo as in progress again
        task.update_todo_status(
            todo_id=todo.id,
            status="IN_PROGRESS",
            result=None,
            failure_reason=None
        )
        
        return {
            "recovery_type": "retry_with_backoff",
            "model": task.active_model.name if task.active_model else None,
            "retry_count": task.retry_count,
            "success": True
        }
    
    def _create_fix_todo(self, task: TaskState, todo: Todo,
                          failure: Dict[str, Any]) -> Dict[str, Any]:
        """Create a fix TODO for the failure."""
        # Create a new TODO to fix the issue
        fix_todo = task.add_todo(
            title=f"Fix: {todo.title}",
            description=f"Fix the issue that caused the failure: {failure['error_message']}",
            dependencies=[todo.id]
        )
        
        return {
            "recovery_type": "create_fix_todo",
            "fix_todo_id": fix_todo.id,
            "success": True
        }
    
    def _install_dependency(self, task: TaskState, todo: Todo,
                            failure: Dict[str, Any]) -> Dict[str, Any]:
        """Install missing dependency."""
        task.retry_count += 1
        
        # Mark todo as in progress again
        task.update_todo_status(
            todo_id=todo.id,
            status="IN_PROGRESS",
            result=None,
            failure_reason=None
        )
        
        return {
            "recovery_type": "install_dependency",
            "model": task.active_model.name if task.active_model else None,
            "retry_count": task.retry_count,
            "success": True
        }
    
    def _rotate_api_key(self, task: TaskState, todo: Todo,
                        failure: Dict[str, Any]) -> Dict[str, Any]:
        """Rotate API key or switch provider."""
        # Find next model in the same group
        if task.active_model:
            group_models = self.model_registry.get_models_in_group(task.active_model.group)
            if group_models:
                # Find next available model
                current_index = None
                for i, model in enumerate(group_models):
                    if model.name == task.active_model.name:
                        current_index = i
                        break
                
                if current_index is not None:
                    next_index = (current_index + 1) % len(group_models)
                    next_model = group_models[next_index]
                    
                    if next_model.is_available():
                        task.record_model_switch(
                            new_model=next_model.name,
                            reason="API key rotation"
                        )
                        
                        return {
                            "recovery_type": "rotate_api_key",
                            "model": next_model.name,
                            "success": True
                        }
        
        # If no next model found, fall back to capability handoff
        return self._capability_handoff(task, todo, failure)
    
    def _log_and_retry(self, task: TaskState, todo: Todo,
                       failure: Dict[str, Any]) -> Dict[str, Any]:
        """Log failure and retry with same model."""
        task.retry_count += 1
        
        # Mark todo as in progress again
        task.update_todo_status(
            todo_id=todo.id,
            status="IN_PROGRESS",
            result=None,
            failure_reason=None
        )
        
        return {
            "recovery_type": "log_and_retry",
            "model": task.active_model.name if task.active_model else None,
            "retry_count": task.retry_count,
            "success": True
        }
    
    def _get_required_capabilities(self, todo: Todo) -> List[str]:
        """Determine required capabilities for a todo."""
        # This is a simplified version - in a real implementation you would analyze
        # the todo description and determine required capabilities
        
        if "code" in todo.title.lower() or "implement" in todo.title.lower():
            return ["coding"]
        elif "analyze" in todo.title.lower() or "review" in todo.title.lower():
            return ["reasoning"]
        elif "test" in todo.title.lower():
            return ["testing"]
        elif "build" in todo.title.lower():
            return ["build"]
        
        return ["coding"]
