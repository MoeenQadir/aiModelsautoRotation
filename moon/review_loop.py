"""
MoonAI Review Loop System
Handles iterative review and fix cycles with maximum cycle limits.
"""

import json
from typing import Dict, Any, List, Optional
from datetime import datetime

from .task_state import TaskState, Todo
from .model_registry import ModelCapabilityRegistry
from .completion_gate import CompletionGate


class ReviewLoop:
    """Handles iterative review and fix cycles."""
    
    def __init__(self, model_registry: ModelCapabilityRegistry,
                 completion_gate: CompletionGate,
                 config: Dict[str, Any] = None):
        self.model_registry = model_registry
        self.completion_gate = completion_gate
        self.config = config or {
            "max_review_cycles": 3,
            "enable_auto_review": True,
            "review_threshold": 0.8
        }
        self.review_history: List[Dict[str, Any]] = []
    
    def run_review_cycle(self, task: TaskState, 
                         review_model: str = None) -> Dict[str, Any]:
        """Run a single review cycle."""
        if task.review_count >= self.config["max_review_cycles"]:
            return {
                "success": False,
                "reason": "Maximum review cycles reached",
                "max_cycles": self.config["max_review_cycles"],
                "current_cycles": task.review_count
            }
        
        task.review_count += 1
        
        # Select review model (prefer reasoning model for review)
        if not review_model:
            review_model = self._select_review_model(task)
        
        # Create review prompt
        review_prompt = self._create_review_prompt(task)
        
        # Record review attempt
        review_record = {
            "cycle": task.review_count,
            "review_model": review_model,
            "review_prompt": review_prompt,
            "timestamp": datetime.now().isoformat()
        }
        
        self.review_history.append(review_record)
        
        return {
            "success": True,
            "review_model": review_model,
            "review_prompt": review_prompt,
            "cycle": task.review_count
        }
    
    def _select_review_model(self, task: TaskState) -> str:
        """Select the best model for code review."""
        # Prefer reasoning models for review
        review_models = self.model_registry.get_models_in_group("reasoning")
        if not review_models:
            review_models = self.model_registry.get_models_in_group("coding")
        
        # Filter available models
        available_models = [m for m in review_models if m.is_available()]
        if not available_models:
            return "moon/reasoning"  # Fallback
        
        # Select model with most capabilities
        best_model = max(available_models, key=lambda m: len(m.capabilities))
        return best_model.name
    
    def _create_review_prompt(self, task: TaskState) -> str:
        """Create a review prompt for the reviewer model."""
        prompt = f"""
# Code Review Task

## Original Request
{task.original_request}

## Task Plan
{json.dumps(task.plan, indent=2) if task.plan else "No plan available"}

## Completed Work
{json.dumps(task.completed_work, indent=2)}

## Files Modified
{json.dumps(task.files_modified, indent=2)}

## Todos Status
{json.dumps([{"id": t.id, "title": t.title, "status": t.status} for t in task.todos], indent=2)}

## Verification Status
{json.dumps(task.verification_status, indent=2)}

## Recent Errors
{json.dumps(task.recent_errors, indent=2)}

## Instructions
Please review the completed work and check for:
1. Code quality and correctness
2. Adherence to original requirements
3. Potential bugs or issues
4. Missing functionality
5. Test coverage
6. Documentation

Respond with a JSON object containing:
{{
    "approval": true/false,
    "issues": [
        {{"severity": "critical/major/minor", "description": "...", "file": "..."}}
    ],
    "suggestions": [...],
    "verification_passed": true/false
}}
"""
        return prompt
    
    def process_review_result(self, task: TaskState, 
                              review_result: Dict[str, Any]) -> Dict[str, Any]:
        """Process the review result and take appropriate action."""
        approval = review_result.get("approval", False)
        issues = review_result.get("issues", [])
        suggestions = review_result.get("suggestions", [])
        verification_passed = review_result.get("verification_passed", False)
        
        # Record review result
        self.review_history[-1]["result"] = review_result
        
        if approval and verification_passed:
            return {
                "action": "approved",
                "message": "Review passed, task can proceed to completion gate"
            }
        
        # Create fix TODOs for issues
        fix_todos = []
        for issue in issues:
            if issue.get("severity") in ("critical", "major"):
                fix_todo = task.add_todo(
                    title=f"Fix: {issue['description']}",
                    description=f"Review issue: {issue['description']} in {issue.get('file', 'unknown file')}",
                    dependencies=[t.id for t in task.todos if t.status == "COMPLETED"]
                )
                fix_todos.append(fix_todo.id)
        
        # Add suggestions as minor TODOs
        for suggestion in suggestions:
            task.add_todo(
                title=f"Improve: {suggestion}",
                description=f"Review suggestion: {suggestion}",
                dependencies=[t.id for t in task.todos if t.status == "COMPLETED"]
            )
        
        return {
            "action": "fix_required",
            "issues_found": len(issues),
            "fix_todos_created": fix_todos,
            "message": f"Review found {len(issues)} issues, {len(fix_todos)} fix TODOs created"
        }
    
    def should_continue_review(self, task: TaskState) -> bool:
        """Check if review should continue."""
        return task.review_count < self.config["max_review_cycles"]
    
    def get_review_summary(self) -> Dict[str, Any]:
        """Get summary of all review cycles."""
        return {
            "total_cycles": len(self.review_history),
            "cycles": self.review_history,
            "max_cycles": self.config["max_review_cycles"]
        }
    
    def run_full_review_loop(self, task: TaskState) -> Dict[str, Any]:
        """Run the full review loop until max cycles or approval."""
        results = []
        
        while self.should_continue_review(task):
            cycle_result = self.run_review_cycle(task)
            results.append(cycle_result)
            
            if not cycle_result["success"]:
                break
            
            # The review would be done by an external model
            # In a real implementation, you would call the model here
            # For now, we just return the prompt and wait for external review
            break
        
        return {
            "cycles_completed": len(results),
            "results": results,
            "max_cycles_reached": task.review_count >= self.config["max_review_cycles"]
        }
    
    def reset_review_count(self, task: TaskState):
        """Reset review count (e.g., after major changes)."""
        task.review_count = 0