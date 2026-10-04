"""
MoonAI Handoff Manager with Exponential Backoff, Jitter & Circuit Breaker
Handles model handoffs, retries with backoff, and circuit breaking.
"""

import time
import random
from typing import Dict, List, Optional, Any, Callable
from datetime import datetime, timedelta
from collections import defaultdict

from .task_state import TaskState, Todo
from .model_registry import ModelCapabilityRegistry, ModelCapability


class HandoffManager:
    """Handles model handoffs with retry strategies and circuit breaking."""
    
    def __init__(self, model_registry: ModelCapabilityRegistry,
                 max_retries: int = 3,
                 initial_backoff: float = 1.0,
                 max_backoff: float = 30.0,
                 circuit_breaker_threshold: int = 3,
                 circuit_breaker_timeout: float = 900.0):  # 15 minutes
        self.model_registry = model_registry
        self.max_retries = max_retries
        self.initial_backoff = initial_backoff
        self.max_backoff = max_backoff
        self.circuit_breaker_threshold = circuit_breaker_threshold
        self.circuit_breaker_timeout = circuit_breaker_timeout
        
        # Track retry counts and circuit breaker states
        self.retry_counts: Dict[str, int] = defaultdict(int)
        self.circuit_breakers: Dict[str, float] = {}
        
        # Callback for when handoff occurs
        self._handoff_callback: Optional[Callable[[TaskState, Todo, str, str], None]] = None
    
    def set_handoff_callback(self, callback: Callable[[TaskState, Todo, str, str], None]):
        """Set callback to be called when a handoff occurs."""
        self._handoff_callback = callback
    
    def _get_model_key(self, model_name: str) -> str:
        """Generate a unique key for model."""
        return model_name
    
    def _calculate_backoff(self, retry_count: int) -> float:
        """Calculate exponential backoff with jitter."""
        backoff = min(self.initial_backoff * (2 ** (retry_count - 1)), self.max_backoff)
        # Add jitter (random between 0.8 and 1.2 times the backoff)
        jitter = random.uniform(0.8, 1.2)
        return backoff * jitter
    
    def _is_circuit_broken(self, model_name: str) -> bool:
        """Check if circuit breaker is active for a model."""
        model_key = self._get_model_key(model_name)
        if model_key in self.circuit_breakers:
            if time.time() < self.circuit_breakers[model_key]:
                return True
            else:
                # Circuit breaker expired, remove it
                del self.circuit_breakers[model_key]
        return False
    
    def _record_failure(self, model_name: str):
        """Record a failure for circuit breaker tracking."""
        model_key = self._get_model_key(model_name)
        self.retry_counts[model_key] += 1
        
        # Check if we should trip the circuit breaker
        if self.retry_counts[model_key] >= self.circuit_breaker_threshold:
            self.circuit_breakers[model_key] = time.time() + self.circuit_breaker_timeout
            self.retry_counts[model_key] = 0  # Reset counter after tripping
    
    def _record_success(self, model_name: str):
        """Record a success to reset retry counters."""
        model_key = self._get_model_key(model_name)
        self.retry_counts[model_key] = 0
    
    def handle_capability_failure(self, task: TaskState, todo: Todo,
                                 required_capabilities: List[str],
                                 error_message: str) -> Optional[Dict[str, Any]]:
        """Handle capability failure by finding an alternative model.

        When the failure is an explicit auth failure (AUTH_FAILURE / AUTH_ERROR),
        avoid re-selecting a model from the same provider so that the call to the
        misconfigured key is not repeated. In that case we skip the failed model
        and any other models on the same provider in the current group.
        """
        failed_provider = None
        if task.active_model and hasattr(task.active_model, 'name'):
            nm = task.active_model.name
            if '/' in nm:
                failed_provider = nm.split('/', 1)[0].lower()

        # Determine recovery behaviour from the error message so that a
        # misconfigured key is treated as a hard provider problem rather than a
        # transient capability issue.
        is_auth_failure = (
            self.failure_type_auth_error(error_message)
            or "auth failure" in error_message.lower()
            or "auth_error" in error_message.lower()
            or "access_token_type_unsupported" in error_message.lower()
        )

        is_capability_refusal = (
            "capability_unsupported" in error_message.lower()
            or "cannot execute terminal" in error_message.lower()
            or "function_calling_not_supported" in error_message.lower()
            or "tool_use_not_supported" in error_message.lower()
            or "tool_use_failed" in error_message.lower()
            or "i am a text model" in error_message.lower()
            or "does not support tool" in error_message.lower()
        )

        # Find a model with the required capabilities, preferring a different
        # provider when the failure was auth-related. The registry's helper
        # surfaces a model from a different provider in the same group when
        # possible.
        target_model = self.model_registry.select_model_for_capabilities(
            required_capabilities,
            prefer_group=task.active_model.group if task.active_model else None,
            skip_provider_id=failed_provider if is_auth_failure else None
        )
        
        if target_model:
            # Create handoff record
            handoff = task.create_handoff(
                todo_id=todo.id,
                source_model=task.active_model.name if task.active_model else "unknown",
                target_model=target_model.name,
                required_capability=",".join(required_capabilities),
                blocked_operation=todo.title,
                task_summary=task.original_request,
                expected_result=todo.description
            )
            
            # Update task state
            task.current_todo = todo.id
            todo.status = "DELEGATED"
            task.active_model = target_model
            
            # Record model switch
            task.record_model_switch(
                new_model=target_model.name,
                reason=f"Capability failure: {error_message}"
            )
            
            # Notify callback
            if self._handoff_callback:
                try:
                    self._handoff_callback(task, todo, task.active_model.name, error_message)
                except Exception:
                    pass
            
            return handoff
        
        # If no capable model found, mark as blocked
        todo.status = "BLOCKED"
        todo.failure_reason = f"No capable model found for capabilities: {required_capabilities}"
        task.record_failure(
            failure_type="CAPABILITY_ERROR",
            error_message=f"No capable model found for capabilities: {required_capabilities}",
            context={
                "todo_id": todo.id,
                "required_capabilities": required_capabilities
            }
        )
        
        return None
    
    @staticmethod
    def failure_type_auth_error(error_message: str) -> bool:
        """Return True when *error_message* indicates a deterministic auth failure
        (401 / ACCESS_TOKEN_TYPE_UNSUPPORTED / generic auth error)."""
        m = error_message.lower()
        return bool(
            "401" in m
            or "unauthorized" in m
            or "unauthenticated" in m
            or "access token type unsupported" in m
            or "access_token_type_unsupported" in m
            or "invalid authentication credentials" in m
            or "invalid api key" in m
            or "auth_error" in m
            or "auth failure" in m
            or "auth_failure" in m
        )

    @staticmethod
    def _does_provider_match(model_name: str, provider: str) -> bool:
        return model_name.lower().split('/', 1)[0] == provider.lower()

    @staticmethod
    def _all_models_from_same_provider(candidate: ModelCapability, other: ModelCapability) -> bool:
        a = candidate.name.split('/', 1)[0] if '/' in getattr(candidate, 'name', '') else ''
        b = other.name.split('/', 1)[0] if '/' in getattr(other, 'name', '') else ''
        return bool(a) and a == b

    @staticmethod
    def _preferred_model(candidate: ModelCapability, acceptable: List[ModelCapability], skip_this: Optional[str]) -> Optional[ModelCapability]:
        if acceptable and skip_this:
            for other in acceptable:
                if other.name != candidate.name and not HandoffManager._does_provider_match(other.name, skip_this):
                    return other
        return candidate

    @staticmethod
    def _preferred_from_different_provider(candidates: List[ModelCapability], skip_provider: str) -> Optional[ModelCapability]:
        """Pick the best model that is NOT from the failed provider, when possible."""
        from .model_registry import ModelCapability
        best: Optional[ModelCapability] = None
        best_score = -1
        for m in candidates:
            if skip_provider and m.name.lower().split('/', 1)[0] == skip_provider.lower():
                continue
            score = len(m.capabilities)
            if score > best_score:
                best_score = score
                best = m
        return best

    def handle_transient_failure(self, task: TaskState, todo: Todo,
                                 error_message: str) -> Optional[Dict[str, Any]]:
        """Handle transient failure with retry and backoff."""
        # --- Instant-auth fast-failover ---
        # Per failure_classifier.attempt_recovery, an explicit 401 /
        # ACCESS_TOKEN_TYPE_UNSUPPORTED is classified as AUTH_FAILURE and routed
        # here. Retrying or waiting for a backoff only burns time when the key
        # itself is wrong, so jump immediately to the capability handoff that
        # picks the next deployment in the chain.
        if self.failure_type_auth_error(error_message):
            return self.handle_capability_failure(
                task=task,
                todo=todo,
                required_capabilities=self._get_required_capabilities(todo),
                error_message=f"Auth failure detected - instant fallback to next provider ({error_message})"
            )

        # --- Capability refusal fast-failover ---
        # When a model explicitly refuses to execute terminal commands or use
        # tools, retrying is pointless. Jump straight to capability handoff
        # targeting a model with the required capabilities.
        if self.failure_type_capability_unsupported(error_message):
            return self.handle_capability_failure(
                task=task,
                todo=todo,
                required_capabilities=self._get_required_capabilities_with_execution(todo),
                error_message=f"Capability refusal detected - instant fallback to capable model ({error_message})"
            )

        # Check if we should retry
        if task.retry_count >= self.max_retries:
            # Max retries reached, perform capability handoff
            return self.handle_capability_failure(
                task=task,
                todo=todo,
                required_capabilities=self._get_required_capabilities(todo),
                error_message=error_message
            )

        # Check if circuit breaker is active
        if task.active_model and self._is_circuit_broken(task.active_model.name):
            # Circuit broken, perform capability handoff
            return self.handle_capability_failure(
                task=task,
                todo=todo,
                required_capabilities=self._get_required_capabilities(todo),
                error_message=f"Circuit breaker active: {error_message}"
            )

        # Calculate backoff
        backoff = self._calculate_backoff(task.retry_count + 1)
        
        # Record the failure for circuit breaker tracking
        if task.active_model:
            self._record_failure(task.active_model.name)
        
        # Update task state
        task.retry_count += 1
        todo.status = "IN_PROGRESS"
        todo.attempts += 1
        
        # Return retry information
        return {
            "action": "retry_with_backoff",
            "backoff_seconds": backoff,
            "retry_count": task.retry_count,
            "max_retries": self.max_retries,
            "model": task.active_model.name if task.active_model else "unknown",
            "error_message": error_message
        }
    
    def handle_success(self, task: TaskState, todo: Todo):
        """Handle successful operation to reset retry counters."""
        if task.active_model:
            self._record_success(task.active_model.name)
        task.retry_count = 0
    
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
    
    def get_retry_info(self, task: TaskState) -> Dict[str, Any]:
        """Get information about current retry state."""
        return {
            "retry_count": task.retry_count,
            "max_retries": self.max_retries,
            "next_backoff": self._calculate_backoff(task.retry_count + 1) if task.retry_count < self.max_retries else 0,
            "circuit_broken": self._is_circuit_broken(task.active_model.name) if task.active_model else False
        }


class CapabilityHandoffManager(HandoffManager):
    """Capability-aware handoff manager.

    Provided as a dedicated (subclass) name for compatibility with imports
    across the codebase; all capability-aware handoff logic lives in
    :class:`HandoffManager`.
    """

    pass