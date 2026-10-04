"""
MoonAI Capability-Aware Handoff System
Handles model switching when capability failures occur.
"""

import json
from typing import Dict, Any, Optional
from datetime import datetime

from .task_state import TaskState, Todo
from .model_registry import ModelCapabilityRegistry


class CapabilityHandoffManager:
    """Manages capability-aware model handoffs."""
    
    def __init__(self, model_registry: ModelCapabilityRegistry):
        self.model_registry = model_registry
    
    def handle_capability_failure(self, task: TaskState, todo: Todo, 
                                 required_capabilities: list[str], 
                                 error_message: str) -> Optional[Dict[str, Any]]:
        """Handle a capability failure by finding an appropriate model."""
        # Classify the failure
        failure_type = self._classify_failure(error_message)
        
        # Get recovery strategy
        strategy = self.model_registry.get_failure_strategy(failure_type)
        
        # If this is a capability failure, try to find a capable model
        if failure_type == "CAPABILITY_ERROR" or "PERMISSION_ERROR":
            # Find a model with the required capabilities
            target_model = self.model_registry.select_model_for_capabilities(
                required_capabilities,
                prefer_group=task.active_model.group if task.active_model else None
            )
            
            if target_model:
                # Create handoff
                handoff = task.create_handoff(
                    todo_id=todo.id,
                    source_model=task.active_model.name if task.active_model else "unknown",
                    target_model=target_model.name,
                    required_capability=", ".join(required_capabilities),
                    blocked_operation=todo.title,
                    task_summary=task.original_request[:200],
                    expected_result=todo.description
                )
                
                # Update todo status
                task.update_todo_status(
                    todo_id=todo.id,
                    status="DELEGATED",
                    failure_reason=f"Capability failure: {error_message}"
                )
                
                # Record model switch
                task.record_model_switch(
                    new_model=target_model.name,
                    reason=f"Capability failure: {error_message}"
                )
                
                return handoff
        
        # For other failures, follow the recovery strategy
        if strategy["recovery"] == "capability_aware_handoff":
            # Try to find a model that can handle the operation
            target_model = self.model_registry.select_model_for_capabilities(
                required_capabilities,
                prefer_group=task.active_model.group if task.active_model else None
            )
            
            if target_model:
                handoff = task.create_handoff(
                    todo_id=todo.id,
                    source_model=task.active_model.name if task.active_model else "unknown",
                    target_model=target_model.name,
                    required_capability=", ".join(required_capabilities),
                    blocked_operation=todo.title,
                    task_summary=task.original_request[:200],
                    expected_result=todo.description
                )
                
                task.update_todo_status(
                    todo_id=todo.id,
                    status="DELEGATED",
                    failure_reason=f"Capability failure: {error_message}"
                )
                
                task.record_model_switch(
                    new_model=target_model.name,
                    reason=f"Capability failure: {error_message}"
                )
                
                return handoff
        
        # If no capable model found, mark as blocked
        task.update_todo_status(
            todo_id=todo.id,
            status="BLOCKED",
            failure_reason=f"No capable model found for capabilities: {required_capabilities}"
        )
        
        return None
    
    def _classify_failure(self, error_message: str) -> str:
        """Classify the type of failure based on error message."""
        error_message = error_message.lower()
        
        if "permission" in error_message or "access denied" in error_message:
            return "PERMISSION_ERROR"
        elif "capability" in error_message or "cannot perform" in error_message:
            return "CAPABILITY_ERROR"
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
        
        return "UNKNOWN_ERROR"
    
    def complete_handoff(self, task: TaskState, handoff: Dict[str, Any], 
                          result: str, success: bool) -> bool:
        """Complete a handoff and return control to original model."""
        # Update todo status based on handoff result
        if success:
            task.update_todo_status(
                todo_id=handoff["todo_id"],
                status="COMPLETED",
                result=result
            )
            
            # Record the completed work
            task.completed_work.append(
                f"Handoff completed for {handoff['blocked_operation']} by {handoff['target_model']}"
            )
        else:
            task.update_todo_status(
                todo_id=handoff["todo_id"],
                status="FAILED",
                failure_reason=result
            )
            
            # Record the failure
            task.record_failure(
                failure_type="HANDOFF_FAILURE",
                error_message=result,
                context={
                    "handoff": handoff,
                    "result": result
                }
            )
        
        # Return to previous model if it's still the best choice
        if task.previous_model:
            # Check if previous model can handle the remaining work
            previous_model = self.model_registry.get_model_by_name(task.previous_model.name)
            if previous_model and previous_model.is_available():
                # Check if previous model has all required capabilities for remaining work
                required_capabilities = self._get_remaining_capabilities(task)
                if previous_model.has_all_capabilities(required_capabilities):
                    task.record_model_switch(
                        new_model=task.previous_model.name,
                        reason="Returning to previous model after handoff"
                    )
                    return True
        
        # If previous model can't handle remaining work, stay with current model
        return success
    
    def _get_remaining_capabilities(self, task: TaskState) -> list[str]:
        """Determine capabilities needed for remaining work."""
        # This is a simplified version - in a real implementation you would analyze
        # the remaining todos and determine required capabilities
        pending_todos = [t for t in task.todos if t.status in ("PENDING", "IN_PROGRESS")]
        
        # For simplicity, assume all pending todos require coding capability
        return ["coding"]
    
    def handle_model_stop(self, task: TaskState, todo: Todo, reason: str) -> Optional[Dict[str, Any]]:
        """Handle a model stopping prematurely."""
        # Classify the stop reason
        failure_type = self._classify_stop_reason(reason)
        
        # Get recovery strategy
        strategy = self.model_registry.get_failure_strategy(failure_type)
        
        # If this is a capability stop, try to find a capable model
        if failure_type == "CAPABILITY_STOP":
            # Determine required capabilities from the todo
            required_capabilities = self._get_required_capabilities_from_todo(todo)
            
            # Find a model with the required capabilities
            target_model = self.model_registry.select_model_for_capabilities(
                required_capabilities,
                prefer_group=task.active_model.group if task.active_model else None
            )
            
            if target_model:
                # Create handoff
                handoff = task.create_handoff(
                    todo_id=todo.id,
                    source_model=task.active_model.name if task.active_model else "unknown",
                    target_model=target_model.name,
                    required_capability=", ".join(required_capabilities),
                    blocked_operation=todo.title,
                    task_summary=task.original_request[:200],
                    expected_result=todo.description
                )
                
                # Update todo status
                task.update_todo_status(
                    todo_id=todo.id,
                    status="DELEGATED",
                    failure_reason=f"Model stopped: {reason}"
                )
                
                # Record model switch
                task.record_model_switch(
                    new_model=target_model.name,
                    reason=f"Model stopped: {reason}"
                )
                
                return handoff
        
        # For other stop reasons, follow the recovery strategy
        if strategy["recovery"] == "capability_aware_handoff":
            # Try to find a model that can handle the operation
            required_capabilities = self._get_required_capabilities_from_todo(todo)
            target_model = self.model_registry.select_model_for_capabilities(
                required_capabilities,
                prefer_group=task.active_model.group if task.active_model else None
            )
            
            if target_model:
                handoff = task.create_handoff(
                    todo_id=todo.id,
                    source_model=task.active_model.name if task.active_model else "unknown",
                    target_model=target_model.name,
                    required_capability=", ".join(required_capabilities),
                    blocked_operation=todo.title,
                    task_summary=task.original_request[:200],
                    expected_result=todo.description
                )
                
                task.update_todo_status(
                    todo_id=todo.id,
                    status="DELEGATED",
                    failure_reason=f"Model stopped: {reason}"
                )
                
                task.record_model_switch(
                    new_model=target_model.name,
                    reason=f"Model stopped: {reason}"
                )
                
                return handoff
        
        # If no capable model found, mark as blocked
        task.update_todo_status(
            todo_id=todo.id,
            status="BLOCKED",
            failure_reason=f"Model stopped: {reason}"
        )
        
        return None
    
    def _classify_stop_reason(self, reason: str) -> str:
        """Classify the reason for model stopping."""
        reason = reason.lower()
        
        if "capability" in reason or "cannot perform" in reason:
            return "CAPABILITY_STOP"
        elif "rate limit" in reason or "too many requests" in reason:
            return "RATE_LIMIT"
        elif "timeout" in reason or "took too long" in reason:
            return "TIMEOUT"
        elif "context window" in reason or "too large" in reason:
            return "CONTEXT_LIMIT"
        elif "tool error" in reason or "invalid tool call" in reason:
            return "TOOL_ERROR"
        elif "network error" in reason or "connection failed" in reason:
            return "NETWORK_ERROR"
        elif "syntax error" in reason or "invalid syntax" in reason:
            return "SYNTAX_ERROR"
        elif "build error" in reason or "compilation failed" in reason:
            return "BUILD_ERROR"
        elif "test failure" in reason or "assertion failed" in reason:
            return "TEST_FAILURE"
        elif "dependency error" in reason or "missing dependency" in reason:
            return "DEPENDENCY_ERROR"
        elif "authentication error" in reason or "invalid api key" in reason:
            return "AUTH_ERROR"
        
        return "UNKNOWN_STOP_REASON"
    
    def _get_required_capabilities_from_todo(self, todo: Todo) -> list[str]:
        """Determine required capabilities from a todo."""
        # This is a simplified version - in a real implementation you would analyze
        # the todo description and determine required capabilities
        
        # For coding-related todos, assume coding capability is needed
        if "code" in todo.title.lower() or "implement" in todo.title.lower():
            return ["coding"]
        
        # For analysis-related todos, assume reasoning capability is needed
        if "analyze" in todo.title.lower() or "review" in todo.title.lower():
            return ["reasoning"]
        
        # Default to coding capability
        return ["coding"]
