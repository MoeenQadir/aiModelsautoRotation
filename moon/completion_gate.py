"""
MoonAI Completion Gate and Verification System
Handles task completion verification and final checks.
"""

import json
import subprocess
from typing import Dict, Any, List, Optional
from datetime import datetime

from .task_state import TaskState, Todo
from .model_registry import ModelCapabilityRegistry


class CompletionGate:
    """Handles task completion verification and final checks."""
    
    def __init__(self, model_registry: ModelCapabilityRegistry,
                 config: Dict[str, Any] = None):
        self.model_registry = model_registry
        self.config = config or {
            "require_tests_pass": True,
            "require_build_pass": True,
            "require_lint_pass": True,
            "require_typecheck_pass": True,
            "verification_commands": {
                "test": "npm run test",
                "build": "npm run build",
                "lint": "npm run lint",
                "typecheck": "npm run typecheck"
            }
        }
    
    def can_complete_task(self, task: TaskState) -> bool:
        """Check if task can be marked complete."""
        # Check if all todos are completed
        active_todos = [t for t in task.todos if t.status not in ("COMPLETED", "FAILED", "SKIPPED")]
        if active_todos:
            return False
        
        # Check if all verification steps passed
        if not self._check_verification_status(task):
            return False
        
        # Check if all required commands passed
        if not self._check_required_commands(task):
            return False
        
        # Check if all user requirements are met
        if not self._check_user_requirements(task):
            return False
        
        # Check for obvious unfinished work
        if self._check_for_unfinished_work(task):
            return False
        
        return True
    
    def _check_verification_status(self, task: TaskState) -> bool:
        """Check verification status of all todos."""
        for todo in task.todos:
            if todo.status == "FAILED" and not todo.failure_reason.startswith("Verification"):
                return False
        return True
    
    def _check_required_commands(self, task: TaskState) -> bool:
        """Check if all required commands passed."""
        if self.config["require_tests_pass"]:
            if not task.verification_status.get("test_passed", False):
                return False
        
        if self.config["require_build_pass"]:
            if not task.verification_status.get("build_passed", False):
                return False
        
        if self.config["require_lint_pass"]:
            if not task.verification_status.get("lint_passed", False):
                return False
        
        if self.config["require_typecheck_pass"]:
            if not task.verification_status.get("typecheck_passed", False):
                return False
        
        return True
    
    def _check_user_requirements(self, task: TaskState) -> bool:
        """Check if all user requirements are met."""
        # This is a simplified version - in a real implementation you would analyze
        # the original request and compare with completed work
        return True
    
    def _check_for_unfinished_work(self, task: TaskState) -> bool:
        """Check for obvious unfinished work."""
        # Check for common signs of unfinished work
        for todo in task.todos:
            if "fix" in todo.title.lower() and todo.status != "COMPLETED":
                return True
            if "verify" in todo.title.lower() and todo.status != "COMPLETED":
                return True
        
        # Check if any files were modified but not verified
        if task.files_modified and not task.verification_status.get("files_verified", False):
            return True
        
        return False
    
    def run_verification_commands(self, task: TaskState) -> Dict[str, Any]:
        """Run all verification commands and update task status."""
        results = {}
        
        # Run test command
        if self.config["require_tests_pass"]:
            test_result = self._run_command(self.config["verification_commands"]["test"])
            results["test"] = test_result
            task.verification_status["test_passed"] = test_result["success"]
            
            if not test_result["success"]:
                # Create fix TODO for failed tests
                task.add_todo(
                    title="Fix failed tests",
                    description=f"Tests failed with output: {test_result['output']}",
                    dependencies=[t.id for t in task.todos if t.status == "COMPLETED"]
                )
        
        # Run build command
        if self.config["require_build_pass"]:
            build_result = self._run_command(self.config["verification_commands"]["build"])
            results["build"] = build_result
            task.verification_status["build_passed"] = build_result["success"]
            
            if not build_result["success"]:
                # Create fix TODO for failed build
                task.add_todo(
                    title="Fix build failure",
                    description=f"Build failed with output: {build_result['output']}",
                    dependencies=[t.id for t in task.todos if t.status == "COMPLETED"]
                )
        
        # Run lint command
        if self.config["require_lint_pass"]:
            lint_result = self._run_command(self.config["verification_commands"]["lint"])
            results["lint"] = lint_result
            task.verification_status["lint_passed"] = lint_result["success"]
            
            if not lint_result["success"]:
                # Create fix TODO for lint issues
                task.add_todo(
                    title="Fix lint issues",
                    description=f"Lint check failed with output: {lint_result['output']}",
                    dependencies=[t.id for t in task.todos if t.status == "COMPLETED"]
                )
        
        # Run typecheck command
        if self.config["require_typecheck_pass"]:
            typecheck_result = self._run_command(self.config["verification_commands"]["typecheck"])
            results["typecheck"] = typecheck_result
            task.verification_status["typecheck_passed"] = typecheck_result["success"]
            
            if not typecheck_result["success"]:
                # Create fix TODO for typecheck issues
                task.add_todo(
                    title="Fix typecheck issues",
                    description=f"Typecheck failed with output: {typecheck_result['output']}",
                    dependencies=[t.id for t in task.todos if t.status == "COMPLETED"]
                )
        
        return results
    
    def _run_command(self, command: str) -> Dict[str, Any]:
        """Run a shell command and return the result."""
        try:
            result = subprocess.run(
                command,
                shell=True,
                check=True,
                capture_output=True,
                text=True,
                timeout=300
            )
            return {
                "success": True,
                "output": result.stdout,
                "error": result.stderr
            }
        except subprocess.CalledProcessError as e:
            return {
                "success": False,
                "output": e.stdout,
                "error": e.stderr,
                "returncode": e.returncode
            }
        except subprocess.TimeoutExpired as e:
            return {
                "success": False,
                "output": "",
                "error": "Command timed out",
                "timeout": True
            }
    
    def generate_completion_report(self, task: TaskState) -> Dict[str, Any]:
        """Generate a completion report for the task."""
        report = {
            "task_id": task.task_id,
            "original_request": task.original_request,
            "status": task.status,
            "completed_at": datetime.now().isoformat(),
            "todos_completed": len([t for t in task.todos if t.status == "COMPLETED"]),
            "todos_failed": len([t for t in task.todos if t.status == "FAILED"]),
            "verification_status": task.verification_status,
            "files_modified": task.files_modified,
            "decisions_made": task.decisions_made,
            "recent_errors": task.recent_errors,
            "model_history": task.model_history,
            "completion_gate_passed": self.can_complete_task(task)
        }
        
        return report
    
    def mark_task_complete(self, task: TaskState) -> bool:
        """Mark task as complete if all conditions are met."""
        if self.can_complete_task(task):
            task.status = "COMPLETED"
            task.updated_at = datetime.now().isoformat()
            return True
        return False
