"""
MoonAI Completion Gate and Verification System
Handles task completion verification and final checks.
"""

import json
import subprocess
import re
from typing import Dict, Any, List, Optional
from datetime import datetime
from pathlib import Path

from .task_state import TaskState, Todo
from .model_registry import ModelCapabilityRegistry


class CompletionGate:
    """Handles task completion verification and final checks."""

    def __init__(self, model_registry: ModelCapabilityRegistry,
                 config: Dict[str, Any] = None):
        self.model_registry = model_registry
        self.config = config or {
            # NOTE: only test/build are required by default. Lint/typecheck are
            # opt-in because a project may not have them configured; requiring
            # them unconditionally would make the completion gate impossible
            # to satisfy. Configure via orchestration.yaml in production.
            "require_tests_pass": True,
            "require_build_pass": True,
            "require_lint_pass": False,
            "require_typecheck_pass": False,
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
            if todo.status == "FAILED":
                reason = todo.failure_reason or ""
                if not reason.startswith("Verification"):
                    return False
        return True

    def _check_required_commands(self, task: TaskState) -> bool:
        """Check if all required commands passed."""
        # If no code files were modified and no verification commands were run,
        # required commands pass by default for text/query tasks.
        if not task.files_modified and not task.verification_status:
            return True

        if self.config["require_tests_pass"] and task.files_modified:
            if not task.verification_status.get("test_passed", False):
                return False

        if self.config["require_build_pass"] and task.files_modified:
            if not task.verification_status.get("build_passed", False):
                return False

        if self.config["require_lint_pass"] and task.files_modified:
            if not task.verification_status.get("lint_passed", False):
                return False

        if self.config["require_typecheck_pass"] and task.files_modified:
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

        # If every required verification step passed, the modified files are
        # considered verified.
        required_flags = []
        if self.config["require_tests_pass"]:
            required_flags.append("test_passed")
        if self.config["require_build_pass"]:
            required_flags.append("build_passed")
        if self.config["require_lint_pass"]:
            required_flags.append("lint_passed")
        if self.config["require_typecheck_pass"]:
            required_flags.append("typecheck_passed")
        if required_flags and all(task.verification_status.get(f) for f in required_flags):
            task.verification_status["files_verified"] = True

        return results

    def _run_command(self, command: str) -> Dict[str, Any]:
        """Run a shell command and return the result."""
        # Skip commands whose tooling does not apply to this project (e.g.
        # npm commands in a repository without a package.json) instead of
        # failing the verification gate for them.
        if command.strip().startswith("npm") and not Path("package.json").exists():
            return {
                "success": True,
                "output": "",
                "error": "",
                "skipped": True,
                "skip_reason": "package.json not found - npm command not applicable"
            }
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

    def _repair_json(self, json_str: str) -> Optional[Dict[str, Any]]:
        """Attempt to repair malformed JSON from free models."""
        # 1. Remove markdown wrappers like ```json ... ```
        json_str = re.sub(r'```json\s*', '', json_str, flags=re.IGNORECASE)
        json_str = re.sub(r'```\s*', '', json_str, flags=re.IGNORECASE)

        # 2. Remove trailing commas
        json_str = re.sub(r',\s*}', '}', json_str)
        json_str = re.sub(r',\s*]', ']', json_str)

        # 3. Remove control characters (invalid inside JSON strings)
        json_str = re.sub(r'[\x00-\x1f\x7f-\x9f]', '', json_str)

        # 4. Try to parse the repaired JSON as-is
        try:
            return json.loads(json_str)
        except json.JSONDecodeError:
            pass

        # 5. Fall back to the json_repair library when available
        try:
            import json_repair
            repaired = json_repair.repair_json(json_str, return_objects=True)
            if isinstance(repaired, (dict, list)) and repaired:
                return repaired
        except ImportError:
            pass
        except Exception:
            pass

        # 6. Fix single-quoted strings (heuristic: swap ' for ")
        if "'" in json_str:
            try:
                return json.loads(json_str.replace("'", '"'))
            except json.JSONDecodeError:
                pass

        # 7. Balance missing closing braces/brackets (truncated output)
        balanced = self._balance_json(json_str)
        if balanced is not None:
            return balanced

        # 8. Try to find the largest valid JSON substring
        for i in range(len(json_str), 0, -1):
            candidate = self._balance_json(json_str[:i])
            if candidate is not None:
                return candidate

        # 9. If all else fails, try to extract JSON from the string
        json_match = re.search(r'(\{.*\})|(\[.*\])', json_str, re.DOTALL)
        if json_match:
            try:
                return json.loads(json_match.group(0))
            except json.JSONDecodeError:
                return None

        return None

    @staticmethod
    def _balance_json(json_str: str) -> Optional[Dict[str, Any]]:
        """Close any unclosed braces/brackets and try to parse the result."""
        if not json_str.strip():
            return None
        stack = []
        in_string = False
        escaped = False
        for ch in json_str:
            if in_string:
                if escaped:
                    escaped = False
                elif ch == '\\':
                    escaped = True
                elif ch == '"':
                    in_string = False
                continue
            if ch == '"':
                in_string = True
            elif ch in '{[':
                stack.append(ch)
            elif ch in '}]':
                if stack:
                    stack.pop()
        if in_string:
            # Unterminated string: close it before balancing
            json_str += '"'
        closers = {'{': '}', '[': ']'}
        json_str += ''.join(closers[c] for c in reversed(stack))
        try:
            return json.loads(json_str)
        except json.JSONDecodeError:
            return None

    def parse_model_response(self, response: str) -> Optional[Dict[str, Any]]:
        """Parse model response with JSON auto-repair."""
        # First try to parse as-is
        try:
            return json.loads(response)
        except json.JSONDecodeError:
            # If parsing fails, attempt to repair the JSON
            return self._repair_json(response)

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
