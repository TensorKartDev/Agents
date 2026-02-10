"""Configuration helpers for the AGX framework."""

from __future__ import annotations

import importlib
import pathlib
from dataclasses import dataclass, field
from typing import Any, Dict, Iterable, List, Mapping, MutableMapping, Optional

import yaml


class ConfigError(RuntimeError):
    """Raised when configuration files are invalid."""


@dataclass
class PlanningSpec:
    """Runtime planning parameters for an agent."""

    max_iterations: int = 4
    reflection: bool = False
    allow_parallel: bool = False

    @classmethod
    def from_mapping(cls, data: Optional[Mapping[str, Any]]) -> "PlanningSpec":
        if not data:
            return cls()
        return cls(
            max_iterations=int(data.get("max_iterations", 4)),
            reflection=bool(data.get("reflection", False)),
            allow_parallel=bool(data.get("allow_parallel", False)),
        )


@dataclass
class MemorySpec:
    """Memory backend configuration."""

    type: str = "agx.memory.simple:ConversationBufferMemory"
    params: Dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_mapping(cls, data: Optional[Mapping[str, Any]]) -> "MemorySpec":
        if not data:
            return cls()
        return cls(
            type=data.get("type", cls.type_default()),
            params=dict(data.get("params", {})),
        )

    @staticmethod
    def type_default() -> str:
        return "agx.memory.simple:ConversationBufferMemory"


@dataclass
class AgentSpec:
    """Definition of an agent from config."""

    name: str
    llm_provider: Optional[str]
    tools: List[str]
    planning: PlanningSpec
    description: Optional[str] = None
    memory: MemorySpec = field(default_factory=MemorySpec)
    metadata: Dict[str, Any] = field(default_factory=dict)
    llm_params: Dict[str, Any] = field(default_factory=dict)
    self_deciding: bool = False

    @classmethod
    def from_mapping(cls, name: str, data: Mapping[str, Any]) -> "AgentSpec":
        if "tools" not in data:
            raise ConfigError(f"Agent '{name}' requires a tools list")
        return cls(
            name=name,
            llm_provider=data.get("llm_provider"),
            tools=list(data.get("tools", [])),
            planning=PlanningSpec.from_mapping(data.get("planning")),
            description=data.get("description"),
            memory=MemorySpec.from_mapping(data.get("memory")),
            metadata=dict(data.get("metadata", {})),
            llm_params=dict(data.get("llm_params", {})),
            self_deciding=bool(data.get("self_deciding", False)),
        )


@dataclass
class TaskSpec:
    """Represents a task to be executed by an agent."""

    id: str
    agent: str
    description: str
    input: Any = None
    context: Dict[str, Any] = field(default_factory=dict)
    expected_output: Optional[str] = None
    depends_on: List[str] = field(default_factory=list)
    task_type: Optional[str] = None
    reason: Optional[str] = None
    ui: Optional[Dict[str, Any]] = None
    tool: Optional[str] = None
    source_task: Optional[str] = None

    @classmethod
    def from_mapping(cls, data: Mapping[str, Any]) -> "TaskSpec":
        missing = [key for key in ("id", "agent", "description") if key not in data]
        if missing:
            raise ConfigError(f"Task is missing required keys: {', '.join(missing)}")
        raw_depends = data.get("depends_on") or []
        if isinstance(raw_depends, str):
            depends_on = [raw_depends]
        else:
            depends_on = [str(item) for item in raw_depends]
        task_type = data.get("type") or data.get("task_type")
        if isinstance(task_type, str):
            normalized = task_type.strip().lower()
            if normalized in {"humanapprovaltask", "human_approval", "human-approval", "approval"}:
                task_type = "human_approval"
            elif normalized in {"humaninputtask", "human_input", "human-input", "input", "form"}:
                task_type = "human_input"
            elif normalized in {"tool_run", "tool-run", "tool"}:
                task_type = "tool_run"
            elif normalized in {"action_approval", "action-approval", "actions_approval", "approve_actions"}:
                task_type = "action_approval"
        else:
            task_type = None
        return cls(
            id=str(data["id"]),
            agent=str(data["agent"]),
            description=str(data["description"]),
            input=data.get("input"),
            context=dict(data.get("context", {})),
            expected_output=data.get("expected_output"),
            depends_on=depends_on,
            task_type=task_type,
            reason=data.get("reason"),
            ui=(dict(data.get("ui", {})) if isinstance(data.get("ui"), Mapping) else None),
            tool=(str(data.get("tool")) if data.get("tool") is not None else None),
            source_task=(str(data.get("source_task")) if data.get("source_task") is not None else None),
        )


@dataclass
class ToolSpec:
    """Configuration for a tool instance."""

    name: str
    type: str
    args: Dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_mapping(cls, name: str, data: Mapping[str, Any]) -> "ToolSpec":
        if "type" not in data:
            raise ConfigError(f"Tool '{name}' requires a type path")
        return cls(name=name, type=str(data["type"]), args=dict(data.get("args", {})))


@dataclass
class DefaultsSpec:
    """Optional defaults applied to agents/tasks."""

    llm_provider: Optional[str] = None
    llm_params: Dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_mapping(cls, data: Optional[Mapping[str, Any]]) -> "DefaultsSpec":
        if not data:
            return cls()
        return cls(
            llm_provider=data.get("llm_provider"),
            llm_params=dict(data.get("llm_params", {})),
        )


@dataclass
class ProjectConfig:
    """Representation of the YAML configuration."""

    name: str
    description: Optional[str]
    defaults: DefaultsSpec
    agents: Dict[str, AgentSpec]
    tasks: List[TaskSpec]
    tool_specs: Dict[str, ToolSpec]
    file_path: Optional[pathlib.Path] = None

    @classmethod
    def from_file(cls, path: str | pathlib.Path) -> "ProjectConfig":
        p = pathlib.Path(path)
        data = yaml.safe_load(p.read_text())
        if not isinstance(data, MutableMapping):
            raise ConfigError("Configuration root must be a mapping")
        return cls.from_mapping(data, p)

    @classmethod
    def from_yaml(cls, content: str) -> "ProjectConfig":
        data = yaml.safe_load(content)
        if not isinstance(data, MutableMapping):
            raise ConfigError("Configuration root must be a mapping")
        return cls.from_mapping(data)

    @classmethod
    def from_mapping(cls, data: MutableMapping, path: Optional[pathlib.Path] = None) -> "ProjectConfig":
        agents = {
            name: AgentSpec.from_mapping(name, info)
            for name, info in (data.get("agents") or {}).items()
        }
        tasks = [TaskSpec.from_mapping(item) for item in data.get("tasks", [])]
        if not agents:
            raise ConfigError("At least one agent must be defined")
        if not tasks:
            raise ConfigError("At least one task must be defined")
        tool_specs = {
            name: ToolSpec.from_mapping(name, info)
            for name, info in (data.get("tools") or {}).items()
        }
        return cls(
            name=data.get("name", path.stem if path else "Untitled"),
            description=data.get("description"),
            defaults=DefaultsSpec.from_mapping(data.get("defaults")),
            agents=agents,
            tasks=tasks,
            tool_specs=tool_specs,
            file_path=path,
        )

    def get_agent(self, name: str) -> AgentSpec:
        try:
            return self.agents[name]
        except KeyError as exc:
            raise ConfigError(f"Unknown agent '{name}' referenced by task") from exc


def import_string(path: str) -> Any:
    """Return attribute from module specified by path "module:qualname"."""

    if ":" not in path:
        raise ConfigError(f"Import path '{path}' must use module:qualname format")
    module_path, attr = path.split(":", 1)
    module = importlib.import_module(module_path)
    try:
        return getattr(module, attr)
    except AttributeError as exc:
        raise ConfigError(f"Module '{module_path}' has no attribute '{attr}'") from exc


def instantiate_from_path(path: str, *args: Any, **kwargs: Any) -> Any:
    """Import and instantiate a class given its dotted path."""

    cls = import_string(path)
    return cls(*args, **kwargs)


def ensure_iterable(value: Any) -> Iterable[Any]:
    if value is None:
        return []
    if isinstance(value, (list, tuple, set)):
        return value
    return [value]
