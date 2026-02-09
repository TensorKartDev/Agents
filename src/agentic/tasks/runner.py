"""Task runner utilities."""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Dict, Iterable, List, Optional, Sequence

from .base import HumanApprovalTask, HumanInputTask, Task, TaskResult, TaskState


@dataclass
class TaskStateRecord:
    task_id: str
    state: TaskState
    output: Optional[str] = None
    iterations: Optional[int] = None
    trace: Optional[list[str]] = None
    error: Optional[str] = None
    reason: Optional[str] = None
    approved: Optional[bool] = None
    updated_at: Optional[str] = None


class TaskStateStore:
    """SQLite-backed persistence for task state."""

    def __init__(self, db_path: Path) -> None:
        self.db_path = db_path
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._ensure_schema()

    def _connect(self) -> sqlite3.Connection:
        return sqlite3.connect(self.db_path)

    def _ensure_schema(self) -> None:
        with self._connect() as conn:
            cur = conn.execute("PRAGMA user_version")
            version = int(cur.fetchone()[0])
            if version == 0:
                conn.execute(
                    """
                    CREATE TABLE IF NOT EXISTS task_runs (
                        task_id TEXT PRIMARY KEY,
                        state TEXT NOT NULL,
                        output TEXT,
                        iterations INTEGER,
                        trace TEXT,
                        error TEXT,
                        updated_at TEXT NOT NULL
                    )
                    """
                )
                conn.execute("PRAGMA user_version = 1")
                version = 1
            if version == 1:
                conn.execute("ALTER TABLE task_runs ADD COLUMN reason TEXT")
                conn.execute("ALTER TABLE task_runs ADD COLUMN approved INTEGER")
                conn.execute("PRAGMA user_version = 2")
            conn.commit()

    def upsert(self, record: TaskStateRecord) -> None:
        updated_at = record.updated_at or datetime.now(timezone.utc).isoformat()
        trace_json = json.dumps(record.trace) if record.trace is not None else None
        approved_value = None if record.approved is None else int(record.approved)
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO task_runs (task_id, state, output, iterations, trace, error, reason, approved, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(task_id) DO UPDATE SET
                    state=excluded.state,
                    output=excluded.output,
                    iterations=excluded.iterations,
                    trace=excluded.trace,
                    error=excluded.error,
                    reason=excluded.reason,
                    approved=excluded.approved,
                    updated_at=excluded.updated_at
                """,
                (
                    record.task_id,
                    record.state.value,
                    record.output,
                    record.iterations,
                    trace_json,
                    record.error,
                    record.reason,
                    approved_value,
                    updated_at,
                ),
            )
            conn.commit()

    def fetch(self, task_id: str) -> Optional[TaskStateRecord]:
        with self._connect() as conn:
            cur = conn.execute(
                "SELECT task_id, state, output, iterations, trace, error, reason, approved, updated_at FROM task_runs WHERE task_id = ?",
                (task_id,),
            )
            row = cur.fetchone()
        if not row:
            return None
        trace = json.loads(row[4]) if row[4] else None
        return TaskStateRecord(
            task_id=row[0],
            state=TaskState(row[1]),
            output=row[2],
            iterations=row[3],
            trace=trace,
            error=row[5],
            reason=row[6],
            approved=(bool(row[7]) if row[7] is not None else None),
            updated_at=row[8],
        )

    def set_approval(self, task_id: str, approved: bool, reason: Optional[str] = None) -> None:
        record = self.fetch(task_id)
        state = record.state if record else TaskState.WAITING_HUMAN
        self.upsert(
            TaskStateRecord(
                task_id=task_id,
                state=state,
                approved=approved,
                reason=reason if reason is not None else (record.reason if record else None),
            )
        )


class TaskRunner:
    """Executes tasks by dispatching them to the correct agent."""

    def __init__(
        self,
        agent_resolver: Callable[[str], object],
        db_path: Optional[Path] = None,
        approval_callback: Optional[Callable[[HumanApprovalTask], Optional[bool]]] = None,
    ):
        self._resolver = agent_resolver
        self._results: Dict[str, TaskResult] = {}
        self._store = TaskStateStore(
            db_path if db_path is not None else Path(".agentic") / "task_state.db"
        )
        self._approval_callback = approval_callback

    def approve(self, task_id: str, approved: bool, reason: Optional[str] = None) -> None:
        self._store.set_approval(task_id, approved, reason=reason)

    @staticmethod
    def order_tasks(tasks: Sequence[object]) -> List[object]:
        """Return tasks ordered by dependencies (topological sort)."""
        task_map = {getattr(task, "id"): task for task in tasks}
        deps_map: Dict[str, List[str]] = {}
        for task in tasks:
            raw_deps = getattr(task, "depends_on", []) or []
            deps = [str(dep) for dep in raw_deps]
            deps_map[getattr(task, "id")] = deps
            missing = [dep for dep in deps if dep not in task_map]
            if missing:
                raise ValueError(f"Task {getattr(task, 'id')} depends on unknown tasks: {missing}")

        ordered: List[object] = []
        visited: Dict[str, str] = {}

        def visit(task_id: str) -> None:
            state = visited.get(task_id)
            if state == "temp":
                raise ValueError(f"Cyclic dependency detected at task {task_id}")
            if state == "perm":
                return
            visited[task_id] = "temp"
            for dep in deps_map.get(task_id, []):
                visit(dep)
            visited[task_id] = "perm"
            ordered.append(task_map[task_id])

        for task_id in task_map:
            visit(task_id)
        return ordered

    def run(self, task: Task) -> TaskResult:
        if isinstance(task, HumanApprovalTask):
            return self._handle_human_task(task)
        if isinstance(task, HumanInputTask):
            return self._handle_human_input(task)
        self._store.upsert(TaskStateRecord(task_id=task.id, state=TaskState.RUNNING))
        agent = self._resolver(task.agent_name)
        if not hasattr(agent, "run_task"):
            raise AttributeError(f"Agent {task.agent_name} missing run_task method")
        result: TaskResult = agent.run_task(task)
        if result.state is None:
            result.state = TaskState.COMPLETED if result.success else TaskState.FAILED
        if result.state == TaskState.WAITING_HUMAN:
            result.success = False
        self._results[task.id] = result
        self._store.upsert(
            TaskStateRecord(
                task_id=task.id,
                state=result.state,
                output=result.output,
                iterations=result.iterations,
                trace=result.trace,
                error=result.error,
            )
        )
        return result

    def _handle_human_task(self, task: HumanApprovalTask) -> TaskResult:
        existing = self._store.fetch(task.id)
        if existing and existing.approved:
            result = TaskResult(
                task=task,
                success=True,
                output="Approved",
                iterations=0,
                trace=[],
                state=TaskState.COMPLETED,
            )
            self._results[task.id] = result
            self._store.upsert(
                TaskStateRecord(
                    task_id=task.id,
                    state=TaskState.COMPLETED,
                    output=result.output,
                    reason=task.reason or existing.reason,
                    approved=True,
                )
            )
            return result

        if self._approval_callback is not None:
            decision = self._approval_callback(task)
            if decision:
                self._store.set_approval(task.id, True, reason=task.reason)
                result = TaskResult(
                    task=task,
                    success=True,
                    output="Approved",
                    iterations=0,
                    trace=[],
                    state=TaskState.COMPLETED,
                )
                self._results[task.id] = result
                return result

        result = TaskResult(
            task=task,
            success=False,
            output=f"WAITING_HUMAN: {task.reason}".strip(),
            iterations=0,
            trace=[],
            state=TaskState.WAITING_HUMAN,
        )
        self._results[task.id] = result
        self._store.upsert(
            TaskStateRecord(
                task_id=task.id,
                state=TaskState.WAITING_HUMAN,
                output=result.output,
                reason=task.reason,
                approved=False,
            )
        )
        return result

    def _handle_human_input(self, task: HumanInputTask) -> TaskResult:
        existing = self._store.fetch(task.id)
        if existing and existing.output and existing.state == TaskState.COMPLETED:
            result = TaskResult(
                task=task,
                success=True,
                output=existing.output,
                iterations=0,
                trace=[],
                state=TaskState.COMPLETED,
            )
            self._results[task.id] = result
            return result

        result = TaskResult(
            task=task,
            success=False,
            output="WAITING_INPUT",
            iterations=0,
            trace=[],
            state=TaskState.WAITING_HUMAN,
        )
        self._results[task.id] = result
        self._store.upsert(
            TaskStateRecord(
                task_id=task.id,
                state=TaskState.WAITING_HUMAN,
                output=result.output,
                reason=task.description,
                approved=False,
            )
        )
        return result

    def run_all(self, tasks: Iterable[Task]) -> Dict[str, TaskResult]:
        task_map = {task.id: task for task in tasks}
        for task in task_map.values():
            for dep in task.depends_on or []:
                if dep not in task_map:
                    raise ValueError(f"Task {task.id} depends on unknown task {dep}")
            existing = self._store.fetch(task.id)
            if existing and existing.state == TaskState.WAITING_HUMAN and not existing.approved:
                if self._approval_callback is None:
                    self._results[task.id] = TaskResult(
                        task=task,
                        success=False,
                        output=existing.output or "WAITING_HUMAN",
                        iterations=0,
                        trace=existing.trace or [],
                        state=TaskState.WAITING_HUMAN,
                    )
                else:
                    self._store.upsert(TaskStateRecord(task_id=task.id, state=TaskState.PENDING))
            else:
                self._store.upsert(TaskStateRecord(task_id=task.id, state=TaskState.PENDING))

        states: Dict[str, TaskState] = {task_id: TaskState.PENDING for task_id in task_map}
        for task_id, result in self._results.items():
            states[task_id] = result.state or TaskState.PENDING
        remaining = set(task_map.keys())
        while remaining:
            ready = []
            for task_id in remaining:
                deps = task_map[task_id].depends_on or []
                if all(states.get(dep) == TaskState.COMPLETED for dep in deps):
                    ready.append(task_id)

            if not ready:
                blocked = False
                for task_id in list(remaining):
                    deps = task_map[task_id].depends_on or []
                    if any(states.get(dep) == TaskState.FAILED for dep in deps):
                        self._store.upsert(
                            TaskStateRecord(
                                task_id=task_id,
                                state=TaskState.FAILED,
                                error="dependency_failed",
                            )
                        )
                        states[task_id] = TaskState.FAILED
                        remaining.remove(task_id)
                        blocked = True
                    elif any(states.get(dep) == TaskState.WAITING_HUMAN for dep in deps):
                        blocked = True
                if blocked:
                    break
                raise ValueError("No runnable tasks; cyclic dependency or unresolved prerequisites.")

            for task_id in ready:
                task = task_map[task_id]
                self.run(task)
                remaining.remove(task_id)
                states[task_id] = self._results[task_id].state
                if states[task_id] == TaskState.WAITING_HUMAN:
                    # Stop further processing until approval.
                    return dict(self._results)
        return dict(self._results)

    def results(self) -> Dict[str, TaskResult]:
        return dict(self._results)
