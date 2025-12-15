"""Task dataclasses used by the orchestrator."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, Optional


@dataclass
class Task:
    """A single unit of work for an agent."""

    id: str
    description: str
    agent_name: str
    input: Any = None
    context: Dict[str, Any] = field(default_factory=dict)
    expected_output: Optional[str] = None


@dataclass
class TaskResult:
    """Result of executing a task."""

    task: Task
    success: bool
    output: str
    iterations: int
    trace: list[str]
