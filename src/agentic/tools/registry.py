"""Registry that keeps track of available tools."""

from __future__ import annotations

from typing import Callable, Dict

from ..config import ToolSpec, instantiate_from_path
from .base import Tool

ToolFactory = Callable[[], Tool]


class ToolRegistry:
    """Stores tool factories and lazily instantiates them when requested."""

    def __init__(self) -> None:
        self._factories: Dict[str, ToolFactory] = {}
        self._instances: Dict[str, Tool] = {}

    def register_instance(self, tool: Tool, *, overwrite: bool = False) -> None:
        if tool.name in self._instances and not overwrite:
            raise ValueError(f"Tool {tool.name} already registered")
        self._instances[tool.name] = tool

    def register_factory(self, name: str, factory: ToolFactory, *, overwrite: bool = False) -> None:
        if name in self._factories and not overwrite:
            raise ValueError(f"Tool factory {name} already registered")
        self._factories[name] = factory

    def register_from_spec(self, spec: ToolSpec) -> None:
        def factory() -> Tool:
            instance = instantiate_from_path(spec.type, name=spec.name, **spec.args)
            if not isinstance(instance, Tool):  # pragma: no cover - guard
                raise TypeError(f"Tool '{spec.name}' must inherit Tool")
            return instance

        self.register_factory(spec.name, factory, overwrite=True)

    def configure_from_specs(self, specs: Dict[str, ToolSpec]) -> None:
        for spec in specs.values():
            self.register_from_spec(spec)

    def get(self, name: str) -> Tool:
        if name in self._instances:
            return self._instances[name]
        if name not in self._factories:
            raise KeyError(f"Tool {name} not registered")
        instance = self._factories[name]()
        self._instances[name] = instance
        return instance

    def __contains__(self, name: str) -> bool:
        return name in self._instances or name in self._factories

    def available(self) -> Dict[str, Tool]:
        for name in list(self._factories.keys()):
            if name not in self._instances:
                self._instances[name] = self._factories[name]()
        return dict(self._instances)
