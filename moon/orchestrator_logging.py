"""
MoonAI Structured Logging and Observability System
Provides structured logging for orchestration events and observability.
"""

import json
import logging
import os
from typing import Dict, Any, Optional, List
from datetime import datetime
from pathlib import Path
from dataclasses import dataclass, asdict
from enum import Enum

class LogLevel(Enum):
    DEBUG = "DEBUG"
    INFO = "INFO"
    WARN = "WARN"
    ERROR = "ERROR"
    CRITICAL = "CRITICAL"

class EventType(Enum):
    TASK_START = "task_start"
    TASK_COMPLETE = "task_complete"
    TASK_FAIL = "task_fail"
    TASK_PAUSE = "task_pause"
    TASK_RESUME = "task_resume"
    TODO_CREATE = "todo_create"
    TODO_COMPLETE = "todo_complete"
    TODO_FAIL = "todo_fail"
    TODO_DELEGATE = "todo_delegate"
    MODEL_SWITCH = "model_switch"
    MODEL_FAIL = "model_fail"
    PERMISSION_FAIL = "permission_fail"
    PERMISSION_SWITCH = "permission_switch"
    HANDOFF = "handoff"
    REVIEW_START = "review_start"
    REVIEW_COMPLETE = "review_complete"
    REVIEW_FAIL = "review_fail"
    VERIFICATION_START = "verification_start"
    VERIFICATION_COMPLETE = "verification_complete"
    VERIFICATION_FAIL = "verification_fail"
    CONTEXT_RECOVERY = "context_recovery"
    HANDOFF_COMPLETE = "handoff_complete"
    HANDOFF_FAIL = "handoff_fail"

@dataclass
class LogEntry:
    """A single log entry."""
    timestamp: str
    level: str
    event_type: str
    task_id: Optional[str]
    message: str
    data: Dict[str, Any]
    model_name: Optional[str]
    todo_id: Optional[str]
    
    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)
    
    def to_json(self) -> str:
        return json.dumps(self.to_dict())

class OrchestrationLogger:
    """Structured logger for orchestration events."""
    
    def __init__(self, 
                 log_directory: str = None,
                 log_level: LogLevel = LogLevel.INFO,
                 max_file_size_mb: int = 100,
                 max_backup_files: int = 5):
        self.log_level = log_level
        self.log_directory = Path(log_directory or ".moon/logs")
        self.max_file_size_mb = max_file_size_mb
        self.max_backup_files = max_backup_files
        
        # Ensure log directory exists
        self.log_directory.mkdir(parents=True, exist_ok=True)
        
        # Initialize Python logger
        self.logger = logging.getLogger("moon_orchestrator")
        self.logger.setLevel(log_level.value)
        
        # Add file handler
        log_file = self.log_directory / "orchestration.log"
        file_handler = logging.FileHandler(log_file, encoding='utf-8')
        file_handler.setLevel(log_level.value)
        
        # Add formatter
        formatter = logging.Formatter(
            '%(asctime)s - %(name)s - %(levelname)s - %(message)s'
        )
        file_handler.setFormatter(formatter)
        self.logger.addHandler(file_handler)
        
        # Add console handler
        console_handler = logging.StreamHandler()
        console_handler.setLevel(log_level.value)
        console_handler.setFormatter(formatter)
        self.logger.addHandler(console_handler)
        
        # In-memory event buffer
        self.events: List[LogEntry] = []
    
    def log(self, 
            event_type: EventType,
            task_id: Optional[str],
            message: str,
            level: LogLevel = LogLevel.INFO,
            data: Dict[str, Any] = None,
            model_name: Optional[str] = None,
            todo_id: Optional[str] = None):
        """Log an orchestration event."""
        entry = LogEntry(
            timestamp=datetime.now().isoformat(),
            level=level.value,
            event_type=event_type.value,
            task_id=task_id,
            message=message,
            data=data or {},
            model_name=model_name,
            todo_id=todo_id
        )
        
        # Add to in-memory buffer
        self.events.append(entry)
        
        # Log to file and console
        log_message = f"{entry.to_json()}"
        if level == LogLevel.DEBUG:
            self.logger.debug(log_message)
        elif level == LogLevel.INFO:
            self.logger.info(log_message)
        elif level == LogLevel.WARN:
            self.logger.warning(log_message)
        elif level == LogLevel.ERROR:
            self.logger.error(log_message)
        elif level == LogLevel.CRITICAL:
            self.logger.critical(log_message)
        
        return entry
    
    def log_task_start(self, task_id: str, original_request: str, plan: Dict[str, Any] = None):
        """Log task start event."""
        return self.log(
            event_type=EventType.TASK_START,
            task_id=task_id,
            message="Task started",
            data={
                "original_request": original_request,
                "plan": plan
            }
        )
    
    def log_task_complete(self, task_id: str, completion_report: Dict[str, Any] = None):
        """Log task complete event."""
        return self.log(
            event_type=EventType.TASK_COMPLETE,
            task_id=task_id,
            message="Task completed",
            data={"completion_report": completion_report}
        )
    
    def log_task_fail(self, task_id: str, error: str, task_state: Dict[str, Any] = None):
        """Log task failure event."""
        return self.log(
            event_type=EventType.TASK_FAIL,
            task_id=task_id,
            message="Task failed",
            level=LogLevel.ERROR,
            data={
                "error": error,
                "task_state": task_state
            }
        )
    
    def log_todo_create(self, task_id: str, todo_id: str, title: str, description: str = None):
        """Log TODO creation event."""
        return self.log(
            event_type=EventType.TODO_CREATE,
            task_id=task_id,
            message=f"TODO created: {title}",
            data={
                "todo_id": todo_id,
                "title": title,
                "description": description
            },
            todo_id=todo_id
        )
    
    def log_todo_complete(self, task_id: str, todo_id: str, title: str, result: Dict[str, Any] = None):
        """Log TODO completion event."""
        return self.log(
            event_type=EventType.TODO_COMPLETE,
            task_id=task_id,
            message=f"TODO completed: {title}",
            data={
                "todo_id": todo_id,
                "title": title,
                "result": result
            },
            todo_id=todo_id
        )
    
    def log_todo_fail(self, task_id: str, todo_id: str, title: str, error: str, 
                      failure_type: str = "unknown"):
        """Log TODO failure event."""
        return self.log(
            event_type=EventType.TODO_FAIL,
            task_id=task_id,
            message=f"TODO failed: {title}",
            level=LogLevel.ERROR,
            data={
                "todo_id": todo_id,
                "title": title,
                "error": error,
                "failure_type": failure_type
            },
            todo_id=todo_id
        )
    
    def log_model_switch(self, task_id: str, source_model: str, target_model: str, 
                         reason: str, handoff: Dict[str, Any] = None):
        """Log model switch event."""
        return self.log(
            event_type=EventType.MODEL_SWITCH,
            task_id=task_id,
            message=f"Model switch: {source_model} -> {target_model}",
            data={
                "source_model": source_model,
                "target_model": target_model,
                "reason": reason,
                "handoff": handoff
            },
            model_name=target_model
        )
    
    def log_model_fail(self, task_id: str, model_name: str, error: str, 
                       failure_type: str = "unknown"):
        """Log model failure event."""
        return self.log(
            event_type=EventType.MODEL_FAIL,
            task_id=task_id,
            message=f"Model failed: {model_name}",
            level=LogLevel.ERROR,
            data={
                "model_name": model_name,
                "error": error,
                "failure_type": failure_type
            },
            model_name=model_name
        )
    
    def log_permission_fail(self, task_id: str, model_name: str, operation: str, 
                            error: str):
        """Log permission failure event."""
        return self.log(
            event_type=EventType.PERMISSION_FAIL,
            task_id=task_id,
            message=f"Permission failed: {operation}",
            level=LogLevel.WARN,
            data={
                "model_name": model_name,
                "operation": operation,
                "error": error
            },
            model_name=model_name
        )
    
    def log_handoff(self, task_id: str, source_model: str, target_model: str, 
                    todo_id: str, required_capability: str, 
                    blocked_operation: str, task_summary: str, 
                    expected_result: str):
        """Log handoff event."""
        return self.log(
            event_type=EventType.HANDOFF,
            task_id=task_id,
            message=f"Handoff: {source_model} -> {target_model} for {todo_id}",
            data={
                "source_model": source_model,
                "target_model": target_model,
                "todo_id": todo_id,
                "required_capability": required_capability,
                "blocked_operation": blocked_operation,
                "task_summary": task_summary,
                "expected_result": expected_result
            },
            model_name=target_model,
            todo_id=todo_id
        )
    
    def log_review_start(self, task_id: str, review_model: str, cycle: int):
        """Log review start event."""
        return self.log(
            event_type=EventType.REVIEW_START,
            task_id=task_id,
            message=f"Review started (cycle {cycle})",
            data={
                "review_model": review_model,
                "cycle": cycle
            },
            model_name=review_model
        )
    
    def log_review_complete(self, task_id: str, review_model: str, cycle: int, 
                            approved: bool, issues_found: int = 0):
        """Log review complete event."""
        level = LogLevel.INFO if approved else LogLevel.WARN
        return self.log(
            event_type=EventType.REVIEW_COMPLETE,
            task_id=task_id,
            message=f"Review completed (cycle {cycle}) - {'Approved' if approved else 'Needs fixes'}",
            level=level,
            data={
                "review_model": review_model,
                "cycle": cycle,
                "approved": approved,
                "issues_found": issues_found
            },
            model_name=review_model
        )
    
    def log_verification_start(self, task_id: str, commands: List[str] = None):
        """Log verification start event."""
        return self.log(
            event_type=EventType.VERIFICATION_START,
            task_id=task_id,
            message="Verification started",
            data={"commands": commands}
        )
    
    def log_verification_complete(self, task_id: str, results: Dict[str, Any], 
                                  all_passed: bool):
        """Log verification complete event."""
        level = LogLevel.INFO if all_passed else LogLevel.ERROR
        return self.log(
            event_type=EventType.VERIFICATION_COMPLETE,
            task_id=task_id,
            message=f"Verification completed - {'All passed' if all_passed else 'Failures detected'}",
            level=level,
            data={"results": results, "all_passed": all_passed}
        )
    
    def log_context_recovery(self, task_id: str, checkpoint: Dict[str, Any] = None,
                             recovery_model: str = None):
        """Log context recovery event."""
        return self.log(
            event_type=EventType.CONTEXT_RECOVERY,
            task_id=task_id,
            message="Context recovery initiated",
            data={
                "checkpoint": checkpoint,
                "recovery_model": recovery_model
            },
            model_name=recovery_model
        )
    
    def get_task_events(self, task_id: str) -> List[Dict[str, Any]]:
        """Get all events for a specific task."""
        return [e.to_dict() for e in self.events if e.task_id == task_id]
    
    def get_event_type_counts(self, task_id: str = None) -> Dict[str, int]:
        """Get event type counts, optionally filtered by task."""
        events = self.events
        if task_id:
            events = [e for e in events if e.task_id == task_id]
        
        counts = {}
        for event in events:
            counts[event.event_type] = counts.get(event.event_type, 0) + 1
        return counts
    
    def generate_task_summary(self, task_id: str) -> Dict[str, Any]:
        """Generate a summary of events for a task."""
        events = self.get_task_events(task_id)
        if not events:
            return {"task_id": task_id, "events": [], "summary": "No events found"}
        
        event_types = [e["event_type"] for e in events]
        
        return {
            "task_id": task_id,
            "total_events": len(events),
            "event_types": event_types,
            "first_event": events[0]["timestamp"],
            "last_event": events[-1]["timestamp"],
            "has_model_switches": "model_switch" in event_types,
            "has_failures": any(
                et in event_types for et in ["task_fail", "todo_fail", "model_fail"]
            ),
            "completed": "task_complete" in event_types,
            "events": events
        }
    
    def clear_events(self):
        """Clear the in-memory event buffer."""
        self.events = []
    
    def get_recent_events(self, limit: int = 10) -> List[Dict[str, Any]]:
        """Get the most recent N events."""
        return [e.to_dict() for e in self.events[-limit:]]
