"""
MoonAI Failure Classification and Recovery System
Handles failure detection, classification, and recovery strategies.
"""

import json
from typing import Dict, Any, List, Optional, Callable
from datetime import datetime

from task_state import TaskState, Todo
from model_registry import ModelCapabilityRegistry
from handoff_manager import CapabilityHandoffManager


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
        """Infer error type from error message."""
        error_message = error_message.lower()
        
        if "permission" in error_message or "access denied" in error_message:
            return "PERMISSION_ERROR"
        elif "rate limit" in error_message or "too many requests" in error_message:
            return "RATE_LIMIT"
        elif "timeout" in error_message or "took too long" in error_message:
            return "TIMEOUT"
        elif "context window" in error_message or "too large" in error_message:
            return "CONTEXT_LIMIT"
        elif "tool error" in error_message or "invalid tool call" in error_message:
            return "TOOL_ERROR"
        elif "network error" in error_message or "connection failed" in error_message:
            return "NETWORK_ERROR"
        elif "syntax error" in error_message or "invalid syntax" in error_message:
            return "SYNTAX_ERROR"
        elif "build error" in error_message or "compilation failed" in error_message:
            return "BUILD_ERROR"
        elif "test failure" in error_message or "assertion failed" in error_message:
            return "TEST_FAILURE"
        elif "dependency error" in error_message or "missing dependency" in error_message:
            return "DEPENDENCY_ERROR"
        elif "authentication error" in error_message or "invalid api key" in error_message:
            return "AUTH_ERROR"
        elif "provider error" in error_message or "service unavailable" in error_message:
            return "PROVIDER_ERROR"
        elif "model error" in error_message or "model failed" in error_message:
            return "MODEL_ERROR"
        
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
            # Rotate API key or provider
            actions.append({
                "type": "rotate_api_key",
                "description": "Rotate API key or switch provider"
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


class FailureRecoveryManager:
    """Manages failure recovery strategies."""
    
    def __init__(self, model_registry: ModelCapabilityRegistry,
                 handoff_manager: CapabilityHandoffManager):
        self.model_registry = model_registry
        self.handoff_manager = handoff_manager
    
    def attempt_recovery(self, task: TaskState, todo: Todo,
                         failure: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """Attempt to recover from a failure."""
        failure_type = failure["failure_type"]
        
        # Get recovery strategy
        strategy = self.model_registry.get_failure_strategy(failure_type)
        
        # Execute recovery based on strategy
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
