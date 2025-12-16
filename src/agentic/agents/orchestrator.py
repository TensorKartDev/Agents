"""High-level orchestration for running config-defined agents and tasks."""

from __future__ import annotations

from typing import Dict

from ..config import AgentSpec, ProjectConfig, instantiate_from_path
from ..llm.provider import LLMProvider
from ..memory.simple import ConversationBufferMemory
from ..tasks.base import Task
from ..tasks.runner import TaskRunner
from ..tools.builtin import register_builtin_tools
from ..tools.registry import ToolRegistry
from .base import Agent, PlanningConfig


class Orchestrator:
    """Builds agents/tools from config and runs the requested tasks."""

    def __init__(self, project_config: ProjectConfig) -> None:
        self.config = project_config
        self.tool_registry = ToolRegistry()
        register_builtin_tools(self.tool_registry)
        # Discover any tools provided by installed packages via entry points
        try:
            self.tool_registry.discover_entrypoints()
        except Exception:
            # keep startup resilient if discovery fails
            pass
        self.tool_registry.configure_from_specs(self.config.tool_specs)
        self.agents: Dict[str, Agent] = self._build_agents()
        self.tasks = self._build_tasks()
        self.runner = TaskRunner(self._resolve_agent)

    def _resolve_agent(self, name: str) -> Agent:
        try:
            return self.agents[name]
        except KeyError as exc:  # pragma: no cover - defensive
            raise RuntimeError(f"Unknown agent '{name}'") from exc

    def _build_agents(self) -> Dict[str, Agent]:
        agents: Dict[str, Agent] = {}
        for spec in self.config.agents.values():
            agents[spec.name] = self._materialize_agent(spec)
        return agents

    def _materialize_agent(self, spec: AgentSpec) -> Agent:
        provider_path = spec.llm_provider or self.config.defaults.llm_provider
        if not provider_path:
            provider_path = "agentic.llm.provider:ConsoleEchoProvider"
        provider_params = dict(self.config.defaults.llm_params)
        provider_params.update(spec.llm_params)
        provider: LLMProvider = instantiate_from_path(provider_path, **provider_params)
        memory = instantiate_from_path(spec.memory.type, **spec.memory.params)
        if not hasattr(memory, "add"):
            memory = ConversationBufferMemory()
        planning = PlanningConfig(
            max_iterations=spec.planning.max_iterations,
            reflection=spec.planning.reflection,
        )
        tools = {name: self.tool_registry.get(name) for name in spec.tools}
        description = spec.description or "General agent"
        return Agent(
            name=spec.name,
            description=description,
            llm_provider=provider,
            tools=tools,
            planning=planning,
            memory=memory if isinstance(memory, ConversationBufferMemory) else ConversationBufferMemory(),
        )

    def _build_tasks(self) -> list[Task]:
        items: list[Task] = []
        for spec in self.config.tasks:
            items.append(
                Task(
                    id=spec.id,
                    description=spec.description,
                    agent_name=spec.agent,
                    input=spec.input,
                    context=spec.context,
                    expected_output=spec.expected_output,
                )
            )
        return items

    def run(self) -> Dict[str, str]:
        outputs: Dict[str, str] = {}
        for task in self.tasks:
            result = self.runner.run(task)
            outputs[task.id] = result.output
        return outputs
