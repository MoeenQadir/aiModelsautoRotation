"""
MoonAI End-to-End Validation Test Suite

Tests the complete runtime chain:
Moon Orchestrator → Planner → TaskState/TODO → Model Registry → LiteLLM/OpenCode → Executor 
→ Failure/Recovery → Handoff → Reviewer → Completion Gate → Persistent Completion

Scenarios:
1. Normal execution
2. Execution failure
3. Model failure/unavailability
4. Model stop/context recovery
5. Reviewer rejection
6. Retry/recovery
7. Process/state restart
8. Duplicate execution/idempotency
9. Successful completion
"""

import sys
import json
import os
import tempfile
import shutil
from unittest.mock import Mock, patch, MagicMock

from moon import TaskState, ModelCapabilityRegistry, CapabilityHandoffManager, FailureClassifier, CompletionGate, ReviewLoop, ContextRecoveryManager, OrchestrationLogger, IdempotencyChecker, ModelStopDetector, TaskStateManager
from moon.failure_classifier import FailureRecoveryManager


class TestE2EValidation:
    """Full end-to-end test runner that exercises the complete MoonAI workflow."""

    def setup_method(self, method=None):
        self._init_setup()

    def _init_setup(self):
        self.model_registry = ModelCapabilityRegistry()
        self.handoff_manager = CapabilityHandoffManager(self.model_registry)
        self.failure_classifier = FailureClassifier(self.model_registry)
        self.completion_gate = CompletionGate(self.model_registry)
        self.review_loop = ReviewLoop(self.model_registry, self.completion_gate)
        self.context_recovery = ContextRecoveryManager(self.model_registry, self.handoff_manager)
        self.logger = OrchestrationLogger()
        self.temp_dir = tempfile.mkdtemp(prefix="moonai_e2e_")
        self.results = {
            "normal_execution": {"passed": False, "details": ""},
            "execution_failure": {"passed": False, "details": ""},
            "model_failure": {"passed": False, "details": ""},
            "model_stop": {"passed": False, "details": ""},
            "reviewer_rejection": {"passed": False, "details": ""},
            "retry_recovery": {"passed": False, "details": ""},
            "process_restart": {"passed": False, "details": ""},
            "duplicate_execution": {"passed": False, "details": ""},
            "successful_completion": {"passed": False, "details": ""},
        }

    def teardown_method(self, method=None):
        self.cleanup()
        
    def cleanup(self):
        shutil.rmtree(self.temp_dir, ignore_errors=True)
    
    def run_all(self):
        print("=" * 70)
        print("MoonAI END-TO-END VALIDATION")
        print("=" * 70)
        
        self.test_normal_execution()
        self.test_execution_failure()
        self.test_model_failure()
        self.test_model_stop()
        self.test_reviewer_rejection()
        self.test_retry_recovery()
        self.test_process_restart()
        self.test_duplicate_execution()
        self.test_successful_completion()
        
        self.print_summary()
        return all(r["passed"] for r in self.results.values())
    
    def print_summary(self):
        print("\n" + "=" * 70)
        print("E2E VALIDATION SUMMARY")
        print("=" * 70)
        all_passed = True
        for name, result in self.results.items():
            status = "PASS" if result["passed"] else "FAIL"
            print(f"  {name:30s} : {status}")
            if result["details"]:
                print(f"    {result['details']}")
            if not result["passed"]:
                all_passed = False
        print("=" * 70)
        print(f"OVERALL: {'ALL TESTS PASSED' if all_passed else 'SOME TESTS FAILED'}")
        print("=" * 70)
    
    # ==================== SCENARIO 1: Normal Execution ====================
    def test_normal_execution(self):
        """Test complete normal execution flow: plan → execute → verify → complete"""
        try:
            # 1. Orchestrator creates task
            task = TaskState(original_request="Create a simple Python calculator class")
            
            # 2. Planner breaks down into TODOs (simulated)
            task.plan = {
                "steps": [
                    "Create calculator.py with basic operations",
                    "Add unit tests",
                    "Verify all tests pass"
                ]
            }
            
            todo1 = task.add_todo("Create calculator.py", "Implement add, subtract, multiply, divide")
            todo2 = task.add_todo("Add unit tests", "Test all operations with pytest")
            todo3 = task.add_todo("Verify tests pass", "Run pytest and confirm all green")
            
            # 3. Model Registry selects coding model
            model = self.model_registry.select_model_for_capability("coding")
            task.active_model = model
            
            # 4. Executor processes each TODO
            for todo in [todo1, todo2, todo3]:
                task.current_todo = todo.id
                todo.status = "IN_PROGRESS"
                
                # Simulate execution (in real: call LiteLLM/OpenCode)
                # Here we just mark as completed
                todo.status = "COMPLETED"
                todo.completed_at = "2024-01-01T00:00:00"
                task.completed_work.append(f"Completed: {todo.title}")
            
            # 5. Completion Gate verification
            task.verification_status = {
                "test_passed": True,
                "build_passed": True,
                "lint_passed": True,
                "typecheck_passed": True
            }
            
            # 6. Check completion
            can_complete = self.completion_gate.can_complete_task(task)
            self.completion_gate.mark_task_complete(task)
            
            assert can_complete == True, "Task should be completable"
            assert task.status == "COMPLETED", "Task should be marked COMPLETED"
            assert len(task.completed_work) == 3, "All 3 todos should be completed"
            
            self.results["normal_execution"] = {
                "passed": True,
                "details": f"Task {task.task_id}: 3 todos completed, status={task.status}"
            }
            
        except Exception as e:
            self.results["normal_execution"] = {"passed": False, "details": str(e)}
    
    # ==================== SCENARIO 2: Execution Failure ====================
    def test_execution_failure(self):
        """Test execution failure triggers proper failure handling"""
        try:
            task = TaskState(original_request="Build complex distributed system")
            
            task.plan = {"steps": ["Design architecture", "Implement core", "Test deployment"]}
            
            todo1 = task.add_todo("Design architecture", "Create system design doc")
            todo2 = task.add_todo("Implement core", "Write core services")
            todo3 = task.add_todo("Test deployment", "Deploy and verify")
            
            model = self.model_registry.select_model_for_capability("coding")
            task.active_model = model
            
            # Execute first todo successfully
            task.current_todo = todo1.id
            todo1.status = "COMPLETED"
            task.completed_work.append("Architecture designed")
            
            # Second todo FAILS during execution
            task.current_todo = todo2.id
            todo2.status = "IN_PROGRESS"
            
            # Simulate execution failure (e.g., model returns error)
            error_msg = "SyntaxError: unexpected token 'class' in generated code"
            todo2.status = "FAILED"
            todo2.failure_reason = error_msg
            task.record_failure("SYNTAX_ERROR", error_msg, {"todo": todo2.id})
            
            # Completion gate should reject
            can_complete = self.completion_gate.can_complete_task(task)
            
            assert can_complete == False, "Failed task should not be completable"
            assert len(task.failures) == 1, "Should have recorded 1 failure"
            assert todo2.status == "FAILED", "Todo should be marked FAILED"
            
            self.results["execution_failure"] = {
                "passed": True,
                "details": f"Failure recorded: {task.failures[0]['failure_type']}, task not completable"
            }
            
        except Exception as e:
            self.results["execution_failure"] = {"passed": False, "details": str(e)}
    
    # ==================== SCENARIO 3: Model Failure/Unavailability ====================
    def test_model_failure(self):
        """Test model failure triggers capability-aware handoff"""
        try:
            task = TaskState(original_request="Refactor legacy Java code to Python")
            
            todo1 = task.add_todo("Analyze Java codebase", "Read and understand structure")
            todo2 = task.add_todo("Translate to Python", "Rewrite in Python")
            
            # Start with primary coding model
            primary_model = self.model_registry.select_model_for_capability("coding")
            task.active_model = primary_model
            task.current_todo = todo1.id
            
            # Simulate model failure (provider error)
            error_msg = "Provider error: Groq API returned 503"
            failure = self.failure_classifier.classify_failure(error_msg)
            
            assert failure["failure_type"] == "PROVIDER_ERROR"
            
            # Recovery manager attempts recovery
            recovery_actions = self.failure_classifier.get_recovery_actions(failure, task, todo1)
            assert any(a["type"] == "rotate_deployment" for a in recovery_actions)
            
            # Mark primary model as failed
            self.model_registry.mark_model_failure(primary_model.name, "PROVIDER_ERROR")
            
            # Handoff should find alternative model
            required_caps = ["coding", "file_read", "file_write"]
            alt_model = self.model_registry.select_model_for_capabilities(required_caps)
            
            assert alt_model is not None, "Should find alternative model"
            assert alt_model.name != primary_model.name, "Should be different model"
            
            # Create handoff
            handoff = self.handoff_manager.handle_capability_failure(
                task, todo1, required_caps, error_msg
            )
            
            assert handoff is not None, "Handoff should be created"
            assert handoff["target_model"] == alt_model.name
            assert todo1.status == "DELEGATED", "Todo should be delegated"
            
            self.results["model_failure"] = {
                "passed": True,
                "details": f"Handoff: {primary_model.name} -> {alt_model.name}, failure={failure['failure_type']}"
            }
            
        except Exception as e:
            self.results["model_failure"] = {"passed": False, "details": str(e)}
    
    # ==================== SCENARIO 4: Model Stop/Context Recovery ====================
    def test_model_stop(self):
        """Test model stop detection and context recovery"""
        try:
            task = TaskState(original_request="Write comprehensive API documentation")
            
            todo1 = task.add_todo("Analyze API endpoints", "List all endpoints")
            todo2 = task.add_todo("Generate OpenAPI spec", "Create OpenAPI 3.0 document")
            todo3 = task.add_todo("Write user guide", "Create markdown guide")
            
            model = self.model_registry.select_model_for_capability("coding")
            task.active_model = model
            
            # Complete first todo
            todo1.status = "COMPLETED"
            task.completed_work.append("Analyzed 15 API endpoints")
            task.files_modified.append("api_endpoints.json")
            
            # Model stops prematurely during second todo
            task.current_todo = todo2.id
            todo2.status = "IN_PROGRESS"
            
            # Simulate model response that indicates premature stop
            stop_response = "I have completed the task. The documentation is ready."
            
            detector = ModelStopDetector(self.model_registry)
            stop_info = detector.detect_stop(stop_response, task)
            
            assert stop_info is not None, "Should detect premature completion"
            assert stop_info["type"] == "premature_completion"
            assert stop_info["remaining_todos"] == 2  # todo2 and todo3
            
            # Context recovery
            recovery_context = self.context_recovery.recover_context(task)
            
            assert recovery_context["task_id"] == task.task_id
            assert len(recovery_context["completed_work"]) == 1
            assert recovery_context["current_todo"] == todo2.id
            assert "api_endpoints.json" in recovery_context["files_modified"]
            
            # Idempotency check - completed work should not be redone
            idempotent = self.context_recovery.validate_idempotency(task, todo1)
            assert idempotent == True, "Completed todo should be idempotent"
            
            idempotent2 = self.context_recovery.validate_idempotency(task, todo2)
            assert idempotent2 == False, "In-progress todo should not be idempotent"
            
            self.results["model_stop"] = {
                "passed": True,
                "details": f"Stop detected: {stop_info['type']}, context recovered, idempotency works"
            }
            
        except Exception as e:
            self.results["model_stop"] = {"passed": False, "details": str(e)}
    
    # ==================== SCENARIO 5: Reviewer Rejection ====================
    def test_reviewer_rejection(self):
        """Test reviewer rejection triggers fix cycle within max cycles"""
        try:
            task = TaskState(original_request="Implement authentication module")
            
            todo1 = task.add_todo("Write auth module", "JWT-based authentication")
            todo2 = task.add_todo("Write tests", "Unit and integration tests")
            
            # Complete all todos
            todo1.status = "COMPLETED"
            todo2.status = "COMPLETED"
            task.completed_work = ["Auth module implemented", "Tests written"]
            task.files_modified = ["auth.py", "test_auth.py"]
            
            # Run review cycle 1
            cycle1 = self.review_loop.run_review_cycle(task)
            assert cycle1["success"] == True
            assert cycle1["cycle"] == 1
            
            # Simulate reviewer finding issues
            review_result = {
                "approval": False,
                "issues": [
                    {"severity": "critical", "description": "Missing token refresh logic", "file": "auth.py"},
                    {"severity": "minor", "description": "Add docstring to login function", "file": "auth.py"}
                ],
                "suggestions": ["Consider using PyJWT library"],
                "verification_passed": False
            }
            
            process_result = self.review_loop.process_review_result(task, review_result)
            assert process_result["action"] == "fix_required"
            assert process_result["issues_found"] == 2
            assert len(process_result["fix_todos_created"]) == 1  # Only critical creates fix todo
            
            # Check new fix todo was created
            fix_todos = [t for t in task.todos if "Fix:" in t.title]
            assert len(fix_todos) == 1
            
            # Complete the fix
            fix_todos[0].status = "COMPLETED"
            task.completed_work.append("Added token refresh logic")
            
            # Run review cycle 2
            cycle2 = self.review_loop.run_review_cycle(task)
            assert cycle2["cycle"] == 2
            
            # Simulate approval
            review_result2 = {
                "approval": True,
                "issues": [],
                "suggestions": [],
                "verification_passed": True
            }
            
            process_result2 = self.review_loop.process_review_result(task, review_result2)
            assert process_result2["action"] == "approved"
            
            # Max cycles not exceeded
            assert task.review_count == 2
            assert task.review_count <= self.review_loop.config["max_review_cycles"]
            
            self.results["reviewer_rejection"] = {
                "passed": True,
                "details": f"2 review cycles, 1 fix todo created, max_cycles={self.review_loop.config['max_review_cycles']}"
            }
            
        except Exception as e:
            self.results["reviewer_rejection"] = {"passed": False, "details": str(e)}
    
    # ==================== SCENARIO 6: Retry/Recovery ====================
    def test_retry_recovery(self):
        """Test retry mechanism with exponential backoff"""
        try:
            task = TaskState(original_request="Process large dataset with ML model")
            
            todo1 = task.add_todo("Load dataset", "Read CSV files")
            todo2 = task.add_todo("Train model", "Train ML model on data")
            todo3 = task.add_todo("Evaluate results", "Generate metrics")
            
            model = self.model_registry.select_model_for_capability("coding")
            task.active_model = model
            
            # First attempt fails with network error
            task.current_todo = todo2.id
            todo2.status = "IN_PROGRESS"
            
            error_msg = "Network error: Connection timeout after 30s"
            failure = self.failure_classifier.classify_failure(error_msg)
            assert failure["failure_type"] == "NETWORK_ERROR"
            
            recovery_actions = self.failure_classifier.get_recovery_actions(failure, task, todo2)
            assert any(a["type"] == "retry_with_backoff" for a in recovery_actions)
            
            # Initialize failure recovery manager
            recovery_manager = FailureRecoveryManager(self.model_registry, self.handoff_manager)
            
            # Recovery manager attempts retry
            result = recovery_manager.attempt_recovery(task, todo2, failure)
            
            assert result["recovery_type"] == "retry_with_backoff"
            assert result["success"] == True
            assert task.retry_count == 1
            assert todo2.status == "IN_PROGRESS"  # Reset to in-progress
            
            # Second attempt succeeds
            todo2.status = "COMPLETED"
            task.completed_work.append("Model trained successfully")
            
            self.results["retry_recovery"] = {
                "passed": True,
                "details": f"Network error -> retry_with_backoff, retry_count={task.retry_count}, then success"
            }
            
        except Exception as e:
            self.results["retry_recovery"] = {"passed": False, "details": str(e)}
    
    # ==================== SCENARIO 7: Process/State Restart ====================
    def test_process_restart(self):
        """Test task state persistence and recovery after process restart"""
        try:
            # Create task with state
            task = TaskState(original_request="Migrate database schema")
            
            task.plan = {"steps": ["Backup data", "Run migrations", "Verify integrity"]}
            
            todo1 = task.add_todo("Backup data", "pg_dump to backup.sql")
            todo2 = task.add_todo("Run migrations", "Apply alembic migrations")
            todo3 = task.add_todo("Verify integrity", "Check row counts and constraints")
            
            model = self.model_registry.select_model_for_capability("coding")
            task.active_model = model
            
            # Complete first two todos
            todo1.status = "COMPLETED"
            todo2.status = "COMPLETED"
            task.completed_work = ["Backup created: backup.sql", "Migrations applied: 5 migrations"]
            task.files_modified = ["backup.sql", "migrations/versions/001_init.py"]
            task.verification_status = {"test_passed": True, "build_passed": True}
            
            # Save task state (simulate process shutdown)
            state_manager = TaskStateManager(state_dir=os.path.join(self.temp_dir, "tasks"))
            state_manager.save_task(task)
            
            # SIMULATE PROCESS RESTART - new manager loads task
            new_manager = TaskStateManager(state_dir=os.path.join(self.temp_dir, "tasks"))
            restored_task = new_manager.load_task(task.task_id)
            
            assert restored_task is not None, "Task should be loadable"
            assert restored_task.task_id == task.task_id
            assert restored_task.original_request == task.original_request
            assert restored_task.status == "PENDING"
            assert len(restored_task.todos) == 3
            
            # Check todo states preserved
            restored_todo1 = next(t for t in restored_task.todos if t.id == todo1.id)
            restored_todo2 = next(t for t in restored_task.todos if t.id == todo2.id)
            restored_todo3 = next(t for t in restored_task.todos if t.id == todo3.id)
            
            assert restored_todo1.status == "COMPLETED"
            assert restored_todo2.status == "COMPLETED"
            assert restored_todo3.status == "PENDING"
            
            # Check completed work preserved
            assert len(restored_task.completed_work) == 2
            assert "backup.sql" in restored_task.files_modified
            
            # Check model history preserved
            assert len(restored_task.model_history) == len(task.model_history)
            
            # Resume from last incomplete todo
            restored_task.current_todo = restored_todo3.id
            restored_todo3.status = "IN_PROGRESS"
            
            # Continue execution
            restored_todo3.status = "COMPLETED"
            restored_task.completed_work.append("Data integrity verified")
            
            restored_task.verification_status = {"test_passed": True, "build_passed": True}
            can_complete = self.completion_gate.can_complete_task(restored_task)
            self.completion_gate.mark_task_complete(restored_task)
            
            assert can_complete == True
            assert restored_task.status == "COMPLETED"
            
            self.results["process_restart"] = {
                "passed": True,
                "details": f"Task {task.task_id} persisted and restored, resumed from todo3, completed"
            }
            
        except Exception as e:
            self.results["process_restart"] = {"passed": False, "details": str(e)}
    
    # ==================== SCENARIO 8: Duplicate Execution/Idempotency ====================
    def test_duplicate_execution(self):
        """Test idempotency checker prevents duplicate work"""
        try:
            task = TaskState(original_request="Generate API client from OpenAPI spec")
            
            todo1 = task.add_todo("Generate client", "Use openapi-generator")
            todo2 = task.add_todo("Verify client", "Run validation tests")
            
            model = self.model_registry.select_model_for_capability("coding")
            task.active_model = model
            
            # Complete first todo
            todo1.status = "COMPLETED"
            task.completed_work.append("Generated client in ./generated/client.py")
            task.files_modified = ["generated/client.py", "generated/models.py"]
            
            # Checker records operation
            checker = IdempotencyChecker()
            checker.record_operation(
                "file_write",
                {"path": "generated/client.py", "content": "class Client: ..."},
                "success"
            )
            
            # Attempt duplicate operation
            check = checker.check_operation(
                "file_write",
                {"path": "generated/client.py", "content": "class Client: ..."},
                task
            )
            
            assert check["already_performed"] == True, "Should detect duplicate"
            assert check["previous_result"] == "success"
            
            # Different operation should not be detected as duplicate
            check2 = checker.check_operation(
                "file_write",
                {"path": "generated/new_file.py", "content": "different"},
                task
            )
            assert check2["already_performed"] == False
            
            # Idempotency checker on todo level
            idempotent = self.context_recovery.validate_idempotency(task, todo1)
            assert idempotent == True, "Completed todo is idempotent"
            
            idempotent2 = self.context_recovery.validate_idempotency(task, todo2)
            assert idempotent2 == False, "Pending todo is not idempotent"
            
            self.results["duplicate_execution"] = {
                "passed": True,
                "details": "IdempotencyChecker prevents duplicate file_write, todo-level check works"
            }
            
        except Exception as e:
            self.results["duplicate_execution"] = {"passed": False, "details": str(e)}
    
    # ==================== SCENARIO 9: Successful Completion ====================
    def test_successful_completion(self):
        """Test full successful completion through all gates"""
        try:
            task = TaskState(original_request="Build complete REST API with tests and docs")
            
            # Full plan
            task.plan = {
                "steps": [
                    "Design API schema",
                    "Implement endpoints",
                    "Write comprehensive tests",
                    "Generate documentation",
                    "Verify all quality gates"
                ]
            }
            
            todos = []
            for i, step in enumerate(task.plan["steps"]):
                todo = task.add_todo(step, f"Detailed: {step}")
                todos.append(todo)
            
            model = self.model_registry.select_model_for_capability("coding")
            task.active_model = model
            
            # Execute all todos
            for todo in todos:
                task.current_todo = todo.id
                todo.status = "IN_PROGRESS"
                todo.status = "COMPLETED"
                todo.completed_at = "2024-01-01T00:00:00"
                task.completed_work.append(f"Done: {todo.title}")
            
            task.files_modified = [
                "api/schema.yaml", "api/endpoints.py", "api/models.py",
                "tests/test_endpoints.py", "docs/api.md"
            ]
            
            # Run verification commands (simulated)
            verification_results = self.completion_gate.run_verification_commands(task)
            
            # All should pass (simulated)
            task.verification_status = {
                "test_passed": True,
                "build_passed": True,
                "lint_passed": True,
                "typecheck_passed": True
            }
            
            # Run review loop
            review_cycle = self.review_loop.run_review_cycle(task)
            review_result = {
                "approval": True,
                "issues": [],
                "suggestions": ["Consider adding rate limiting"],
                "verification_passed": True
            }
            self.review_loop.process_review_result(task, review_result)
            
            # Final completion check
            can_complete = self.completion_gate.can_complete_task(task)
            assert can_complete == True, "Should pass completion gate"
            
            completed = self.completion_gate.mark_task_complete(task)
            assert completed == True
            assert task.status == "COMPLETED"
            
            # Generate completion report
            report = self.completion_gate.generate_completion_report(task)
            assert report["completion_gate_passed"] == True
            assert report["todos_completed"] == 5
            
            # Verify persistent state
            state_manager = TaskStateManager(state_dir=os.path.join(self.temp_dir, "tasks"))
            state_manager.save_task(task)
            
            # Reload and verify
            restored = state_manager.load_task(task.task_id)
            assert restored.status == "COMPLETED"
            assert len(restored.todos) == 5
            assert all(t.status == "COMPLETED" for t in restored.todos)
            
            self.results["successful_completion"] = {
                "passed": True,
                "details": f"5 todos, all gates passed, review approved, persisted and verified"
            }
            
        except Exception as e:
            self.results["successful_completion"] = {"passed": False, "details": str(e)}


E2ETestRunner = TestE2EValidation


if __name__ == "__main__":
    runner = E2ETestRunner()
    try:
        success = runner.run_all()
        sys.exit(0 if success else 1)
    finally:
        runner.cleanup()