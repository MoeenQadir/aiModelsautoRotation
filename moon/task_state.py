"""
MoonAI Task State Manager
Handles persistent task state, TODO management, and task lifecycle.
"""

import json
import os
import time
import uuid
import sqlite3
from pathlib import Path
from typing import Optional, List, Dict, Any
from datetime import datetime


class TaskState:
    """Represents the complete state of a task."""

    def __init__(self, task_id: str = None, original_request: str = "", status: str = "PENDING"):
        self.task_id = task_id or self._generate_task_id()
        self.original_request = original_request
        self.status = status
        self.created_at = datetime.now().isoformat()
        self.updated_at = datetime.now().isoformat()
        self.current_step = None
        self.current_todo = None
        self.todos: List[Dict[str, Any]] = []
        self.active_model = None
        self.previous_model = None
        self.model_history: List[Dict[str, Any]] = []
        self.completed_work: List[str] = []
        self.pending_work: List[str] = []
        self.blocked_work: List[str] = []
        self.failures: List[Dict[str, Any]] = []
        self.handoffs: List[Dict[str, Any]] = []
        self.verification_status: Dict[str, Any] = {}
        self.retry_count = 0
        self.review_count = 0
        self.context_summary = None
        self.recent_errors: List[Dict[str, Any]] = []
        self.files_modified: List[str] = []
        self.decisions_made: List[str] = []
        self.plan = None

    def add_todo(self, title: str, description: str = "", dependencies: List[str] = None, parent_todo: str = None) -> "Todo":
        """Add a TODO to the task."""
        todo_id = f"todo_{len(self.todos) + 1}"
        if parent_todo:
            todo_id = f"{parent_todo}_{len([t for t in self.todos if hasattr(t, 'parent_todo') and t.parent_todo == parent_todo]) + 1}"

        todo = Todo(
            todo_id=todo_id,
            title=title,
            description=description,
            dependencies=dependencies or [],
            parent_todo=parent_todo
        )
        self.todos.append(todo)
        return todo

    def record_failure(self, failure_type: str, error_message: str, context: Dict[str, Any] = None):
        """Record a failure in task state."""
        self.failures.append({
            "failure_type": failure_type,
            "error_message": error_message,
            "context": context or {},
            "timestamp": datetime.now().isoformat()
        })
        self.recent_errors.append({
            "type": failure_type,
            "message": error_message,
            "timestamp": datetime.now().isoformat()
        })

    def record_model_switch(self, new_model: str, reason: str):
        """Record a model switch in task history."""
        if self.active_model:
            self.previous_model = self.active_model
        self.active_model = new_model
        self.model_history.append({
            "model": new_model,
            "switched_at": datetime.now().isoformat(),
            "reason": reason
        })

    def update_todo_status(self, todo_id: str, status: str, result: str = None, failure_reason: str = None):
        """Update TODO status."""
        for todo in self.todos:
            if hasattr(todo, 'id') and todo.id == todo_id:
                todo.status = status
                if result:
                    todo.result = result
                if failure_reason:
                    todo.failure_reason = failure_reason
                if status == "IN_PROGRESS":
                    todo.started_at = datetime.now().isoformat()
                elif status == "COMPLETED":
                    todo.completed_at = datetime.now().isoformat()
                elif status == "FAILED":
                    self.failures.append({
                        "todo_id": todo_id,
                        "failure_reason": failure_reason,
                        "timestamp": datetime.now().isoformat()
                    })
                break

    def create_handoff(self, todo_id: str, source_model: str, target_model: str,
                        required_capability: str, blocked_operation: str,
                        task_summary: str, expected_result: str = None) -> Dict[str, Any]:
        """Create a structured handoff record."""
        handoff = {
            "task_id": self.task_id,
            "todo_id": todo_id,
            "source_model": source_model,
            "target_model": target_model,
            "reason": "CAPABILITY_FAILURE",
            "required_capability": required_capability,
            "blocked_operation": blocked_operation,
            "task_summary": task_summary,
            "completed_work": self.completed_work,
            "pending_work": self.pending_work,
            "relevant_files": self.files_modified,
            "expected_result": expected_result,
            "verification_required": True,
            "created_at": datetime.now().isoformat()
        }
        self.handoffs.append(handoff)
        return handoff

    def generate_recovery_context(self) -> Dict[str, Any]:
        """Generate compact recovery context for model handoff."""
        todos_data = []
        for t in self.todos:
            if hasattr(t, 'to_dict'):
                todos_data.append(t.to_dict())
            else:
                todos_data.append(str(t))
        return {
            "task_id": self.task_id,
            "original_request": self.original_request,
            "current_status": self.status,
            "plan": self.plan,
            "todos": todos_data,
            "completed_work": self.completed_work,
            "current_todo": self.current_todo,
            "files_modified": self.files_modified,
            "recent_errors": self.recent_errors[-5:] if self.recent_errors else [],
            "previous_model_action": self.previous_model,
            "context_summary": self.context_summary
        }

    def _generate_task_id(self) -> str:
        return f"task_{int(time.time())}_{uuid.uuid4().hex[:8]}"

    def to_dict(self) -> Dict[str, Any]:
        todos_dict = []
        for todo in self.todos:
            if hasattr(todo, 'to_dict'):
                todos_dict.append(todo.to_dict())
            elif isinstance(todo, Todo):
                todos_dict.append(todo.to_dict())

        return {
            "task_id": self.task_id,
            "original_request": self.original_request,
            "status": self.status,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "current_step": self.current_step,
            "current_todo": self.current_todo,
            "todos": todos_dict,
            "active_model": self.active_model,
            "previous_model": self.previous_model,
            "model_history": self.model_history,
            "completed_work": self.completed_work,
            "pending_work": self.pending_work,
            "blocked_work": self.blocked_work,
            "failures": self.failures,
            "handoffs": self.handoffs,
            "verification_status": self.verification_status,
            "retry_count": self.retry_count,
            "review_count": self.review_count,
            "context_summary": self.context_summary,
            "recent_errors": self.recent_errors,
            "files_modified": self.files_modified,
            "decisions_made": self.decisions_made,
            "plan": self.plan
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> 'TaskState':
        state = cls.__new__(cls)
        for key, value in data.items():
            if key == 'todos':
                state.todos = [Todo.from_dict(t) if isinstance(t, dict) else t for t in value]
            else:
                setattr(state, key, value)
        return state

    def update_timestamp(self):
        self.updated_at = datetime.now().isoformat()


class Todo:
    """Represents a single TODO item in the task."""

    def __init__(self, todo_id: str, title: str, description: str = "", 
                 status: str = "PENDING", dependencies: List[str] = None,
                 assigned_model: str = None, parent_todo: str = None):
        self.id = todo_id
        self.title = title
        self.description = description
        self.status = status
        self.dependencies = dependencies or []
        self.assigned_model = assigned_model
        self.attempts = 0
        self.started_at = None
        self.completed_at = None
        self.result = None
        self.failure_reason = None
        self.parent_todo = parent_todo

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "title": self.title,
            "description": self.description,
            "status": self.status,
            "dependencies": self.dependencies,
            "assigned_model": self.assigned_model,
            "attempts": self.attempts,
            "started_at": self.started_at,
            "completed_at": self.completed_at,
            "result": self.result,
            "failure_reason": self.failure_reason,
            "parent_todo": self.parent_todo
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> 'Todo':
        todo = cls.__new__(cls)
        for key, value in data.items():
            setattr(todo, key, value)
        return todo


class TaskStateManager:
    """Manages persistent task state and TODOs with SQLite primary and JSON fallback."""

    def __init__(self, state_dir: str = ".moon/tasks"):
        self.state_dir = Path(state_dir)
        self.state_dir.mkdir(parents=True, exist_ok=True)
        self.use_sqlite = False
        self.db_path = self.state_dir / "task_state.db"
        self._init_sqlite()
        
    def _init_sqlite(self):
        """Initialize SQLite database and set use_sqlite flag."""
        try:
            conn = sqlite3.connect(self.db_path)
            cursor = conn.cursor()
            # Create tasks table if not exists
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS tasks (
                    task_id TEXT PRIMARY KEY,
                    task_state TEXT NOT NULL,
                    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            ''')
            conn.commit()
            conn.close()
            self.use_sqlite = True
        except Exception as e:
            # Fall back to JSON file storage
            self.use_sqlite = False
            # Ensure state directory exists for JSON files
            self.state_dir.mkdir(parents=True, exist_ok=True)

    def _get_db_connection(self):
        """Get a database connection."""
        if not self.use_sqlite:
            return None
        try:
            return sqlite3.connect(self.db_path)
        except Exception:
            self.use_sqlite = False
            return None

    def create_task(self, original_request: str) -> TaskState:
        """Create a new task with initial state."""
        task = TaskState(original_request=original_request)
        self.save_task(task)
        return task

    def save_task(self, task: TaskState):
        """Persist task state to disk (SQLite primary, JSON fallback)."""
        task.update_timestamp()
        task_json = json.dumps(task.to_dict(), default=lambda o: o.to_dict() if hasattr(o, 'to_dict') else str(o))
        
        if self.use_sqlite:
            try:
                conn = self._get_db_connection()
                if conn:
                    cursor = conn.cursor()
                    cursor.execute(
                        "INSERT OR REPLACE INTO tasks (task_id, task_state, updated_at) VALUES (?, ?, ?)",
                        (task.task_id, task_json, task.updated_at)
                    )
                    conn.commit()
                    conn.close()
                    return
            except Exception:
                self.use_sqlite = False
        
        # Fallback to JSON file storage
        task_dir = self.state_dir / task.task_id
        task_dir.mkdir(parents=True, exist_ok=True)
        task_file = task_dir / "task.json"
        with open(task_file, 'w', encoding='utf-8') as f:
            f.write(task_json)

    def load_task(self, task_id: str) -> Optional[TaskState]:
        """Load task state from disk (SQLite primary, JSON fallback)."""
        if self.use_sqlite:
            try:
                conn = self._get_db_connection()
                if conn:
                    cursor = conn.cursor()
                    cursor.execute(
                        "SELECT task_state FROM tasks WHERE task_id = ?",
                        (task_id,)
                    )
                    row = cursor.fetchone()
                    conn.close()
                    if row:
                        data = json.loads(row[0])
                        return TaskState.from_dict(data)
            except Exception:
                self.use_sqlite = False
        
        # Fallback to JSON file storage
        task_file = self.state_dir / task_id / "task.json"
        if not task_file.exists():
            return None
        
        with open(task_file, 'r', encoding='utf-8') as f:
            data = json.load(f)
        return TaskState.from_dict(data)

    def list_tasks(self) -> List[Dict[str, Any]]:
        """List all tasks with basic info."""
        tasks = []
        if self.use_sqlite:
            try:
                conn = self._get_db_connection()
                if conn:
                    cursor = conn.cursor()
                    cursor.execute(
                        "SELECT task_id, task_state, updated_at FROM tasks ORDER BY updated_at DESC"
                    )
                    rows = cursor.fetchall()
                    conn.close()
                    for row in rows:
                        data = json.loads(row[1])
                        tasks.append({
                            "task_id": row[0],
                            "status": data.get("status", "UNKNOWN"),
                            "created_at": data.get("created_at", ""),
                            "updated_at": row[2],
                            "original_request": data.get("original_request", "")[:100]
                        })
                    return tasks
            except Exception:
                self.use_sqlite = False
        
        # Fallback to JSON file storage
        for task_dir in self.state_dir.iterdir():
            if task_dir.is_dir():
                task = self.load_task(task_dir.name)
                if task:
                    tasks.append({
                        "task_id": task.task_id,
                        "status": task.status,
                        "created_at": task.created_at,
                        "updated_at": task.updated_at,
                        "original_request": task.original_request[:100]
                    })
        return sorted(tasks, key=lambda x: x["created_at"], reverse=True)

    def add_todo(self, task: TaskState, title: str, description: str = "",
                  dependencies: List[str] = None, parent_todo: str = None) -> Todo:
        """Add a TODO to the task."""
        todo_id = f"todo_{len(task.todos) + 1}"
        if parent_todo:
            todo_id = f"{parent_todo}_{len([t for t in task.todos if hasattr(t, 'parent_todo') and t.parent_todo == parent_todo]) + 1}"
        
        todo = Todo(
            todo_id=todo_id,
            title=title,
            description=description,
            dependencies=dependencies,
            parent_todo=parent_todo
        )
        task.todos.append(todo)
        self.save_task(task)
        return todo

    def update_todo_status(self, task: TaskState, todo_id: str, status: str,
                          result: str = None, failure_reason: str = None):
        """Update TODO status."""
        for todo in task.todos:
            if hasattr(todo, 'id') and todo.id == todo_id:
                todo.status = status
                if result:
                    todo.result = result
                if failure_reason:
                    todo.failure_reason = failure_reason
                if status == "IN_PROGRESS":
                    todo.started_at = datetime.now().isoformat()
                elif status == "COMPLETED":
                    todo.completed_at = datetime.now().isoformat()
                elif status == "FAILED":
                    task.failures.append({
                        "todo_id": todo_id,
                        "failure_reason": failure_reason,
                        "timestamp": datetime.now().isoformat()
                    })
                break
        self.save_task(task)

    def get_active_todos(self, task: TaskState) -> List[Todo]:
        """Get all active (not completed/failed/skipped) todos."""
        return [todo for todo in task.todos if hasattr(todo, 'status') and todo.status in ("PENDING", "IN_PROGRESS", "BLOCKED")]

    def get_pending_todos(self, task: TaskState) -> List[Todo]:
        """Get all pending todos."""
        return [todo for todo in task.todos if hasattr(todo, 'status') and todo.status == "PENDING"]

    def get_blocked_todos(self, task: TaskState) -> List[Todo]:
        """Get all blocked todos."""
        return [todo for todo in task.todos if hasattr(todo, 'status') and todo.status == "BLOCKED"]

    def get_failed_todos(self, task: TaskState) -> List[Todo]:
        """Get all failed todos."""
        return [todo for todo in task.todos if hasattr(todo, 'status') and todo.status == "FAILED"]

    def can_complete_task(self, task: TaskState) -> bool:
        """Check if task can be marked complete."""
        active_todos = self.get_active_todos(task)
        return len(active_todos) == 0

    def record_model_switch(self, task: TaskState, new_model: str, reason: str):
        """Record a model switch in task history."""
        if task.active_model:
            task.previous_model = task.active_model
        task.active_model = new_model
        task.model_history.append({
            "model": new_model,
            "switched_at": datetime.now().isoformat(),
            "reason": reason
        })
        self.save_task(task)

    def record_failure(self, task: TaskState, failure_type: str, error_message: str,
                      context: Dict[str, Any] = None):
        """Record a failure in task state."""
        task.failures.append({
            "failure_type": failure_type,
            "error_message": error_message,
            "context": context or {},
            "timestamp": datetime.now().isoformat()
        })
        task.recent_errors.append({
            "type": failure_type,
            "message": error_message,
            "timestamp": datetime.now().isoformat()
        })
        self.save_task(task)

    def create_handoff(self, task: TaskState, todo_id: str, source_model: str,
                      target_model: str, required_capability: str,
                      blocked_operation: str, task_summary: str,
                      expected_result: str = None) -> Dict[str, Any]:
        """Create a structured handoff record."""
        handoff = {
            "task_id": task.task_id,
            "todo_id": todo_id,
            "source_model": source_model,
            "target_model": target_model,
            "reason": "CAPABILITY_FAILURE",
            "required_capability": required_capability,
            "blocked_operation": blocked_operation,
            "task_summary": task_summary,
            "completed_work": task.completed_work,
            "pending_work": task.pending_work,
            "relevant_files": task.files_modified,
            "expected_result": expected_result,
            "verification_required": True,
            "created_at": datetime.now().isoformat()
        }
        task.handoffs.append(handoff)
        self.save_task(task)
        return handoff

    def generate_recovery_context(self, task: TaskState) -> Dict[str, Any]:
        """Generate compact recovery context for model handoff."""
        todos_data = []
        for t in task.todos:
            if hasattr(t, 'to_dict'):
                todos_data.append(t.to_dict())
            else:
                todos_data.append(str(t))
        return {
            "task_id": task.task_id,
            "original_request": task.original_request,
            "current_status": task.status,
            "plan": task.plan,
            "todos": todos_data,
            "completed_work": task.completed_work,
            "current_todo": task.current_todo,
            "files_modified": task.files_modified,
            "recent_errors": task.recent_errors[-5:] if task.recent_errors else [],
            "previous_model_action": task.previous_model,
            "context_summary": task.context_summary
        }

    def recover_unfinished_tasks(self) -> List[str]:
        """Recover task IDs of unfinished tasks (PENDING, IN_PROGRESS, BLOCKED)."""
        unfinished = []
        tasks = self.list_tasks()
        for task_info in tasks:
            if task_info["status"] in ("PENDING", "IN_PROGRESS", "BLOCKED"):
                unfinished.append(task_info["task_id"])
        return unfinished

    def delete_task(self, task_id: str):
        """Delete a task and all its data."""
        if self.use_sqlite:
            try:
                conn = self._get_db_connection()
                if conn:
                    cursor = conn.cursor()
                    cursor.execute("DELETE FROM tasks WHERE task_id = ?", (task_id,))
                    conn.commit()
                    conn.close()
                    return
            except Exception:
                self.use_sqlite = False
        
        # Fallback to JSON file storage
        task_dir = self.state_dir / task_id
        if task_dir.exists():
            import shutil
            shutil.rmtree(task_dir)