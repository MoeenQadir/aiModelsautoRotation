"""
MoonAI Model Stop Detection and Context Recovery System
Detects when models stop prematurely and handles context recovery.
"""

import json
from typing import Dict, Any, List, Optional
from datetime import datetime

from task_state import TaskState, Todo
from model_registry import ModelCapabilityRegistry
from handoff_manager import CapabilityHandoffManager
from failure_classifier import FailureClassifier


class ModelStopDetector:
    """Detects when models stop prematurely."""
    
    STOP_PATTERNS = [
        "task completed",
        "here is the completed",
        "the task is done",
        "i have completed",
        "finished the task",
        "all done",
        "that's all",
        "completed successfully",
    ]
    
    REFUSAL_PATTERNS = [
        "i cannot",
        "i'm unable",
        "i can't",
        "not able to",
        "unable to perform",
        "cannot perform",
        "don't have permission",
        "not authorized",
        "refuse to",
    ]
    
    EXPLANATION_PATTERNS = [
        "here is an explanation",
        "let me explain",
        "the approach would be",
        "you could implement",
        "here's how you would",
        "the solution is",
    ]
    
    PARTIAL_IMPLEMENTATION_PATTERNS = [
        "here is the first part",
        "here is the beginning",
        "i'll start with",
        "let me begin",
        "partial implementation",
    ]
    
    def __init__(self, model_registry: ModelCapabilityRegistry):
        self.model_registry = model_registry
    
    def detect_stop(self, response: str, task: TaskState) -> Optional[Dict[str, Any]]:
        """Detect if a model stopped prematurely."""
        response_lower = response.lower()
        
        # Check for completion claims while todos remain
        if self._check_completion_claim(response_lower, task):
            return {
                "type": "premature_completion",
                "reason": "Model claimed completion while TODOs remain",
                "remaining_todos": len([t for t in task.todos if t.status not in ("COMPLETED", "FAILED", "SKIPPED")])
            }
        
        # Check for refusals
        if self._check_refusal(response_lower):
            return {
                "type": "refusal",
                "reason": "Model refused to perform operation"
            }
        
        # Check for explanations instead of execution
        if self._check_explanation(response_lower):
            return {
                "type": "explanation_instead_of_execution",
                "reason": "Model provided explanation instead of executing"
            }
        
        # Check for partial implementation
        if self._check_partial_implementation(response_lower):
            return {
                "type": "partial_implementation",
                "reason": "Model provided partial implementation"
            }
        
        # Check for empty or very short response
        if len(response.strip()) < 50:
            return {
                "type": "empty_response",
                "reason": "Model returned empty or very short response"
            }
        
        return None
    
    def _check_completion_claim(self, response: str, task: TaskState) -> bool:
        """Check if model claims completion while TODOs remain."""
        active_todos = [t for t in task.todos if t.status not in ("COMPLETED", "FAILED", "SKIPPED")]
        
        if not active_todos:
            return False
        
        for pattern in self.STOP_PATTERNS:
            if pattern in response:
                return True
        
        return False
    
    def _check_refusal(self, response: str) -> bool:
        """Check if model refused to perform operation."""
        for pattern in self.REFUSAL_PATTERNS:
            if pattern in response:
                return True
        return False
    
    def _check_explanation(self, response: str) -> bool:
        """Check if model provided explanation instead of execution."""
        # If response is mostly explanation without tool calls
        explanation_count = sum(1 for pattern in self.EXPLANATION_PATTERNS if pattern in response)
        return explanation_count > 0 and "tool" not in response.lower()
    
    def _check_partial_implementation(self, response: str) -> bool:
        """Check if model provided partial implementation."""
        for pattern in self.PARTIAL_IMPLEMENTATION_PATTERNS:
            if pattern in response:
                return True
        return False


class ContextRecoveryManager:
    """Manages context recovery after model switches."""
    
    def __init__(self, model_registry: ModelCapabilityRegistry,
                 handoff_manager: CapabilityHandoffManager):
        self.model_registry = model_registry
        self.handoff_manager = handoff_manager
    
    def recover_context(self, task: TaskState) -> Dict[str, Any]:
        """Generate recovery context for the next model."""
        # Generate compact recovery context
        recovery_context = task.generate_recovery_context()
        
        # Add model-specific context
        recovery_context["active_model"] = task.active_model
        recovery_context["previous_model"] = task.previous_model
        recovery_context["model_history"] = task.model_history
        
        return recovery_context
    
    def compact_context(self, task: TaskState, max_tokens: int = 5000) -> Dict[str, Any]:
        """Compact task context to fit within token limits."""
        # Create a compact summary
        summary = {
            "task_id": task.task_id,
            "original_request": task.original_request[:500],
            "status": task.status,
            "current_todo": task.current_todo,
            "completed_todos": len([t for t in task.todos if t.status == "COMPLETED"]),
            "total_todos": len(task.todos),
            "files_modified": task.files_modified[-10:],  # Last 10 files
            "recent_errors": task.recent_errors[-3:],  # Last 3 errors
            "model_history": task.model_history[-3:],  # Last 3 model switches
        }
        
        return summary
    
    def resume_from_checkpoint(self, task: TaskState, 
                               checkpoint: Dict[str, Any]) -> TaskState:
        """Resume task from a checkpoint."""
        # Restore task state from checkpoint
        for key, value in checkpoint.items():
            if hasattr(task, key):
                setattr(task, key, value)
        
        return task
    
    def create_checkpoint(self, task: TaskState) -> Dict[str, Any]:
        """Create a checkpoint of the current task state."""
        return {
            "task_id": task.task_id,
            "original_request": task.original_request,
            "status": task.status,
            "current_step": task.current_step,
            "current_todo": task.current_todo,
            "todos": [t for t in task.todos],
            "active_model": task.active_model,
            "previous_model": task.previous_model,
            "completed_work": task.completed_work,
            "pending_work": task.pending_work,
            "files_modified": task.files_modified,
            "decisions_made": task.decisions_made,
            "verification_status": task.verification_status,
            "created_at": datetime.now().isoformat()
        }
    
    def handle_model_death(self, task: TaskState) -> Dict[str, Any]:
        """Handle a model dying during task execution."""
        # Find the current todo
        current_todo = None
        for todo in task.todos:
            if todo.id == task.current_todo:
                current_todo = todo
                break
        
        if not current_todo:
            # No current todo, find next pending
            for todo in task.todos:
                if todo.status == "PENDING":
                    current_todo = todo
                    break
        
        if not current_todo:
            return {
                "success": False,
                "reason": "No active todo found"
            }
        
        # Determine required capabilities
        required_capabilities = self._get_required_capabilities(current_todo)
        
        # Find a capable model
        target_model = self.model_registry.select_model_for_capabilities(
            required_capabilities,
            prefer_group=task.active_model.group if task.active_model else None
        )
        
        if not target_model:
            return {
                "success": False,
                "reason": "No capable model found"
            }
        
        # Create handoff
        handoff = task.create_handoff(
            todo_id=current_todo.id,
            source_model=task.active_model.name if task.active_model else "unknown",
            target_model=target_model.name,
            required_capability=", ".join(required_capabilities),
            blocked_operation=current_todo.title,
            task_summary=task.original_request[:200],
            expected_result=current_todo.description
        )
        
        # Update todo status
        task.update_todo_status(
            todo_id=current_todo.id,
            status="DELEGATED",
            failure_reason="Previous model died"
        )
        
        # Record model switch
        task.record_model_switch(
            new_model=target_model.name,
            reason="Previous model died"
        )
        
        return {
            "success": True,
            "handoff": handoff,
            "target_model": target_model.name,
            "recovery_context": self.recover_context(task)
        }
    
    def _get_required_capabilities(self, todo: Todo) -> List[str]:
        """Determine required capabilities for a todo."""
        if "code" in todo.title.lower() or "implement" in todo.title.lower():
            return ["coding"]
        elif "analyze" in todo.title.lower() or "review" in todo.title.lower():
            return ["reasoning"]
        elif "test" in todo.title.lower():
            return ["testing"]
        elif "build" in todo.title.lower():
            return ["build"]
        
        return ["coding"]
    
    def validate_idempotency(self, task: TaskState, todo: Todo) -> bool:
        """Check if work is already completed to avoid duplication."""
        # Check if todo was already completed
        if todo.status == "COMPLETED":
            return True
        
        # Check if files were already modified
        for file in task.files_modified:
            if file in todo.description:
                return True
        
        # Check git diff if available
        # This would require actual git integration
        
        return False


class IdempotencyChecker:
    """Checks for idempotency before executing operations."""
    
    def __init__(self):
        self.operation_history: List[Dict[str, Any]] = []
    
    def check_operation(self, operation: str, parameters: Dict[str, Any],
                        task: TaskState) -> Dict[str, Any]:
        """Check if an operation was already performed."""
        # Create operation signature
        signature = f"{operation}:{json.dumps(parameters, sort_keys=True)}"
        
        # Check if this operation was already performed
        for record in self.operation_history:
            if record["signature"] == signature:
                return {
                    "already_performed": True,
                    "previous_result": record["result"],
                    "timestamp": record["timestamp"]
                }
        
        return {
            "already_performed": False
        }
    
    def record_operation(self, operation: str, parameters: Dict[str, Any],
                         result: Any):
        """Record an operation for future idempotency checks."""
        signature = f"{operation}:{json.dumps(parameters, sort_keys=True)}"
        
        self.operation_history.append({
            "signature": signature,
            "operation": operation,
            "parameters": parameters,
            "result": result,
            "timestamp": datetime.now().isoformat()
        })
    
    def clear_history(self):
        """Clear operation history."""
        self.operation_history = []