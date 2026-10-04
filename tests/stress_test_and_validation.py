"""
MoonAI Stress Test and Validation Suite
Validates all core components of the MoonAI proxy system.
"""

import json
import time
import random
import asyncio
import sqlite3
import sys
import pytest
from pathlib import Path
from typing import Dict, Any, List

# Ensure the project root is importable regardless of the working directory
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from moon import (
    TaskState, TaskStateManager,
    ModelCapabilityRegistry,
    RateLimiter,
    CompletionGate,
    FailureClassifier,
    FailureRecoveryManager,
    HandoffManager,
    ReviewLoop,
    SemanticCache,
    HealthChecker
)


class TestMoonAI:
    """Test suite for MoonAI proxy system."""
    
    @pytest.fixture(autouse=True)
    def setup(self, tmp_path):
        """Setup test environment."""
        # Create temporary directory for tests
        self.test_dir = tmp_path / "moon_test"
        self.test_dir.mkdir()
        
        # Initialize components
        self.model_registry = ModelCapabilityRegistry()
        self.rate_limiter = RateLimiter()
        self.task_manager = TaskStateManager(str(self.test_dir / "tasks"))
        self.completion_gate = CompletionGate(self.model_registry)
        self.failure_classifier = FailureClassifier(self.model_registry)
        self.handoff_manager = HandoffManager(self.model_registry)
        self.recovery_manager = FailureRecoveryManager(self.model_registry, self.handoff_manager)
        self.review_loop = ReviewLoop(self.model_registry, self.completion_gate)
        self.semantic_cache = SemanticCache(str(self.test_dir / "cache"))
        self.health_checker = HealthChecker()
        
        # Start health checker
        self.health_checker.start()
        
        yield
        
        # Cleanup
        self.health_checker.stop()

    def test_json_auto_repair(self):
        """Test JSON auto-repair functionality."""
        test_cases = [
            # Malformed JSON with markdown wrappers
            ("```json\n{\"key\": \"value\"}\n```", {"key": "value"}),
            # Trailing comma
            ("{\"key\": \"value\",}", {"key": "value"}),
            # Missing closing brace
            ("{\"key\": \"value\"", {"key": "value"}),
            # Unescaped quotes
            ("{\"key\": 'value'}", {"key": "value"}),
            # Control characters
            ("{\"key\": \"val\x07ue\"}", {"key": "value"}),
            # Partial JSON
            ("{\"key\": \"value\"\n", {"key": "value"}),
        ]
        
        for malformed, expected in test_cases:
            repaired = self.completion_gate.parse_model_response(malformed)
            assert repaired == expected, f"Failed to repair: {malformed}"

    def test_sqlite_persistence(self):
        """Test SQLite persistence and crash recovery."""
        # Create a task
        task = self.task_manager.create_task("Test persistence")
        task_id = task.task_id
        
        # Add some todos
        todo1 = self.task_manager.add_todo(task, "First todo", "Test todo")
        todo2 = self.task_manager.add_todo(task, "Second todo", "Test todo")
        
        # Save task
        self.task_manager.save_task(task)
        
        # Simulate crash by creating a new manager
        new_manager = TaskStateManager(str(self.test_dir / "tasks"))
        
        # Load task
        loaded_task = new_manager.load_task(task_id)
        assert loaded_task is not None, "Failed to load task"
        assert loaded_task.task_id == task_id, "Task ID mismatch"
        assert len(loaded_task.todos) == 2, "Incorrect number of todos"
        
        # Test recovery of unfinished tasks
        unfinished = new_manager.recover_unfinished_tasks()
        assert task_id in unfinished, "Unfinished task not recovered"

    def test_high_concurrency_rate_limiting(self):
        """Test high concurrency and proactive rate limiting."""
        # Simulate high concurrency
        async def simulate_requests():
            tasks = []
            for i in range(100):
                # Randomly select a model
                model = random.choice(self.model_registry.get_all_models())
                provider, model_name = model.name.split('/', 1)
                
                # Record request
                self.rate_limiter.record_request(provider, model_name)
                
                # Check if we should switch
                if self.rate_limiter.should_switch_model(provider, model_name):
                    # Find alternative model
                    alt_models = self.rate_limiter.get_healthy_models(provider)
                    if alt_models:
                        alt_model = random.choice(alt_models)
                        self.rate_limiter.record_request(provider, alt_model)

            await asyncio.gather(*tasks)
        
        # Run the simulation
        asyncio.run(simulate_requests())
        
        # Verify rate limiting worked
        for model in self.model_registry.get_all_models():
            provider, model_name = model.name.split('/', 1)
            key = f"{provider.lower()}_{model_name.lower()}"
            rpm = len(self.rate_limiter.request_counts[key]["rpm"])
            rpd = len(self.rate_limiter.request_counts[key]["rpd"])
            
            # Check we didn't exceed limits
            limits = self.rate_limiter.get_provider_limits(provider)
            assert rpm <= limits["rpm"], f"RPM exceeded for {model.name}"
            assert rpd <= limits["rpd"], f"RPD exceeded for {model.name}"

    def test_semantic_caching(self):
        """Test semantic caching functionality."""
        # Test cache hit
        prompt = "What is the capital of France?"
        response = {"answer": "Paris"}
        model = "groq/gpt-oss-120b"
        
        # Put in cache
        self.semantic_cache.put(prompt, response, model)
        
        # Get from cache
        cached_response = self.semantic_cache.get(prompt, model)
        assert cached_response == response, "Cache miss for exact match"
        
        # Test semantic similarity
        similar_prompt = "What's the capital city of France?"
        cached_response = self.semantic_cache.get(similar_prompt, model)
        assert cached_response == response, "Cache miss for similar prompt"
        
        # Test different model
        cached_response = self.semantic_cache.get(prompt, "gemini/gemini-flash-latest")
        assert cached_response is None, "Cache hit for different model"

    def test_health_checker(self):
        """Test health checker functionality."""
        # Simulate some health checks
        test_models = [
            ("groq/gpt-oss-120b", "groq"),
            ("gemini/gemini-flash-latest", "gemini"),
            ("mistral/codestral-latest", "mistral"),
        ]
        
        for model_name, provider in test_models:
            # Simulate a health check
            ttft = random.uniform(0.1, 5.0)
            total_latency = ttft + random.uniform(0.1, 2.0)
            success = random.random() > 0.1  # 90% success rate
            
            self.health_checker.update_health(
                model_name=model_name,
                provider=provider,
                ttft=ttft if success else None,
                total_latency=total_latency if success else None,
                success=success
            )
        
        # Verify health status
        for model_name, provider in test_models:
            metrics = self.health_checker.get_health_status(model_name, provider)
            assert metrics is not None, f"No health metrics for {model_name}"
            
            # Check degraded status if TTFT is high
            if metrics.ttft > self.health_checker.degraded_ttft_threshold:
                assert metrics.is_degraded, f"Model {model_name} should be degraded"

    def test_review_loop_security(self):
        """Test review loop security guardrails."""
        # Create a task
        task = self.task_manager.create_task("Test review loop")
        
        # Simulate max iterations
        for i in range(self.review_loop.config["max_iterations"] + 1):
            result = self.review_loop.run_review_cycle(task)
            
            if i < self.review_loop.config["max_iterations"]:
                assert result["success"], f"Review cycle {i+1} failed unexpectedly"
            else:
                assert not result["success"], "Review loop didn't terminate at max iterations"
                assert "Maximum iterations reached" in result["reason"], "Incorrect termination reason"

        # Test token budget
        task = self.task_manager.create_task("Test token budget")
        self.review_loop.token_usage = self.review_loop.config["token_budget"] - 100
        
        # Simulate review that would exceed budget
        result = self.review_loop.run_review_cycle(task)
        assert result["success"], "Review cycle failed unexpectedly"
        
        # Next cycle should exceed budget
        result = self.review_loop.run_review_cycle(task)
        assert not result["success"], "Review loop didn't terminate at token budget"
        assert "Token budget exceeded" in result["reason"], "Incorrect termination reason"

    def test_capability_unsupported_refusal_failover(self):
        """Test model text refusal auto-classification as CAPABILITY_UNSUPPORTED and handoff."""
        # 1. Simulate model refusal text
        refusal_messages = [
            "I cannot execute terminal commands.",
            "I don't have access to terminal.",
            "I am a text model and cannot run code.",
            "function_calling_not_supported",
            "tool_use_failed",
            "This model does not support tool use."
        ]

        for msg in refusal_messages:
            classified = self.failure_classifier.classify_failure(msg)
            assert classified["failure_type"] == "CAPABILITY_UNSUPPORTED", f"Failed for '{msg}'"

        # 2. Test handoff on refusal
        task = self.task_manager.create_task("Run terminal check and build report")
        todo = self.task_manager.add_todo(task, "Execute terminal command", "Run bash script")

        # Set initial text-only model
        text_model = self.model_registry.get_model_by_name("openrouter/north-mini-code:free")
        if text_model:
            task.active_model = text_model

        # Perform recovery on refusal
        refusal_error = "I cannot execute terminal commands"
        failure = self.failure_classifier.classify_failure(refusal_error)

        recovery = self.recovery_manager.attempt_recovery(task, todo, failure)
        assert recovery["success"] is True
        assert recovery["failure_type"] == "CAPABILITY_UNSUPPORTED"
        assert recovery["action"] == "rotate_to_next_provider"

if __name__ == "__main__":
    pytest.main(["-v", str(Path(__file__).resolve())])