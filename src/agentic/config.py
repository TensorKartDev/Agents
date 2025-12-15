"""Configuration helpers for the agentic template."""

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

    type: str = "agentic.memory.simple:ConversationBufferMemory"
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
        return "agentic.memory.simple:ConversationBufferMemory"


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

    @classmethod
    def from_mapping(cls, data: Mapping[str, Any]) -> "TaskSpec":
        missing = [key for key in ("id", "agent", "description") if key not in data]
        if missing:
            raise ConfigError(f"Task is missing required keys: {', '.join(missing)}")
        return cls(
            id=str(data["id"]),
            agent=str(data["agent"]),
            description=str(data["description"]),
            input=data.get("input"),
            context=dict(data.get("context", {})),
            expected_output=data.get("expected_output"),
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

    @classmethod
    def from_file(cls, path: str | pathlib.Path) -> "ProjectConfig":
        data = yaml.safe_load(pathlib.Path(path).read_text())
        if not isinstance(data, MutableMapping):
            raise ConfigError("Configuration root must be a mapping")
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
            name=data.get("name", pathlib.Path(path).stem),
            description=data.get("description"),
            defaults=DefaultsSpec.from_mapping(data.get("defaults")),
            agents=agents,
            tasks=tasks,
            tool_specs=tool_specs,
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
