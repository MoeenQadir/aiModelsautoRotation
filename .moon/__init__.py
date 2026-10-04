"""
MoonAI Orchestration Package
"""

from .task_state import TaskState, Todo
from .model_registry import ModelCapabilityRegistry, ModelInfo, CapabilityGroup
from .handoff_manager import CapabilityHandoffManager
from .failure_classifier import FailureClassifier, FailureType
from .completion_gate import CompletionGate
from .review_loop import ReviewLoop
from .context_recovery import ModelStopDetector, ContextRecoveryManager, IdempotencyChecker
from .orchestrator_logging import OrchestrationLogger, LogLevel, EventType, LogEntry

__all__ = [
    "TaskState",
    "Todo",
    "ModelCapabilityRegistry",
    "ModelInfo",
    "CapabilityGroup",
    "CapabilityHandoffManager",
    "FailureClassifier",
    "FailureType",
    "CompletionGate",
    "ReviewLoop",
    "ModelStopDetector",
    "ContextRecoveryManager",
    "IdempotencyChecker",
    "OrchestrationLogger",
    "LogLevel",
    "EventType",
    "LogEntry",
]

__version__ = "1.0.0"