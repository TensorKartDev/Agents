"""Task dataclasses used by the orchestrator."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, Optional


class TaskState(str, Enum):
    """Lifecycle states for tasks."""

    PENDING = "PENDING"
    RUNNING = "RUNNING"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"
    WAITING_HUMAN = "WAITING_HUMAN"


@dataclass
class Task:
    """A single unit of work for an agent."""

    id: str
    description: str
    agent_name: str
    input: Any = None
    context: Dict[str, Any] = field(default_factory=dict)
    expected_output: Optional[str] = None
    depends_on: list[str] = field(default_factory=list)


@dataclass
class HumanApprovalTask(Task):
    """Task that blocks until a human approves continuation."""

    reason: str = ""


@dataclass
class TaskResult:
    """Result of executing a task."""

    task: Task
    success: bool
    output: str
    iterations: int
    trace: list[str]
    state: Optional[TaskState] = None
    error: Optional[str] = None

    def __post_init__(self) -> None:
        if self.state is None:
            self.state = TaskState.COMPLETED if self.success else TaskState.FAILED
