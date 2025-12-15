"""Base classes for tools."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict


@dataclass
class ToolContext:
    """Metadata passed to tool invocations."""

    agent_name: str
    task_id: str
    iteration: int
    metadata: Dict[str, str] = field(default_factory=dict)


@dataclass
class ToolResult:
    """Result returned by a tool."""

    content: str
    metadata: Dict[str, str] = field(default_factory=dict)


class Tool:
    """Base tool class."""

    name: str
    description: str

    def __init__(self, name: str, description: str | None = None, **kwargs: object) -> None:
        self.name = name
        self.description = description or self.__class__.__doc__ or ""
        self.config = kwargs

    def run(self, *, input_text: str, context: ToolContext) -> ToolResult:  # pragma: no cover - abstract
        raise NotImplementedError
