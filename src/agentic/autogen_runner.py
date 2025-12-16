"""Autogen-powered orchestrator that aligns with Microsoft's Agent Framework."""

from __future__ import annotations

import json
import textwrap
from typing import Any, Dict

from autogen import AssistantAgent, UserProxyAgent

from .config import AgentSpec, ProjectConfig
from .tools.base import ToolContext
from .tools.builtin import register_builtin_tools
from .tools.registry import ToolRegistry


class AutogenOrchestrator:
    """Runs project tasks using Microsoft Autogen agents backed by Ollama."""

    def __init__(self, project_config: ProjectConfig) -> None:
        self.config = project_config
        self.tool_registry = ToolRegistry()
        register_builtin_tools(self.tool_registry)
        self.tool_registry.configure_from_specs(self.config.tool_specs)

    def run(self) -> Dict[str, str]:
        outputs: Dict[str, str] = {}
        for task_spec in self.config.tasks:
            agent_spec = self.config.get_agent(task_spec.agent)
            assistant = self._build_assistant(agent_spec, task_spec.id)
            user = self._build_user(task_spec.id)
            prompt = self._build_task_prompt(task_spec, agent_spec)
            result = user.initiate_chat(
                assistant,
                message=prompt,
                max_turns=max(4, agent_spec.planning.max_iterations * 2),
            )
            outputs[task_spec.id] = self._extract_content(result)
        return outputs

    def _build_assistant(self, agent_spec: AgentSpec, task_id: str) -> AssistantAgent:
        llm_params = self._resolve_llm_params(agent_spec)
        config_list = [
            {
                "model": llm_params.get("model", "llama3"),
                "api_base": llm_params.get("host", "http://127.0.0.1:11434"),
                "api_type": "ollama",
                "api_key": llm_params.get("api_key", "NA"),
            }
        ]
        system_message = textwrap.dedent(
            f"""
            You are agent {agent_spec.name}. Description: {agent_spec.description or 'General agent'}.
            Use the registered functions to complete the assigned task. When you finish, respond with:
            FINAL: <concise summary of the findings or answer>.
            """
        ).strip()
        assistant = AssistantAgent(
            name=f"{agent_spec.name}_{task_id}",
            llm_config={
                "timeout": llm_params.get("timeout", 120),
                "config_list": config_list,
                "cache_seed": None,
            },
            system_message=system_message,
        )
        for tool_name in agent_spec.tools:
            tool = self.tool_registry.get(tool_name)
            assistant.register_function(
                function=self._wrap_tool(tool_name, tool, agent_spec.name, task_id),
                name=tool_name,
                description=tool.description or tool_name,
            )
        return assistant

    def _build_user(self, task_id: str) -> UserProxyAgent:
        return UserProxyAgent(
            name=f"{task_id}_runner",
            human_input_mode="NEVER",
            code_execution_config=False,
            is_termination_msg=self._is_final_message,
        )

    def _resolve_llm_params(self, agent_spec: AgentSpec) -> Dict[str, Any]:
        params = dict(self.config.defaults.llm_params)
        params.update(agent_spec.llm_params)
        return params

    def _wrap_tool(self, name: str, tool, agent_name: str, task_id: str):
        def _tool_func(input_text: Any = "", **kwargs: Any) -> str:
            payload = self._format_tool_input(input_text, kwargs)
            result = tool.run(
                input_text=payload,
                context=ToolContext(
                    agent_name=agent_name,
                    task_id=task_id,
                    iteration=0,
                    metadata={"tool": name},
                ),
            )
            return result.content

        _tool_func.__name__ = name
        _tool_func.__doc__ = tool.description or name
        return _tool_func

    def _format_tool_input(self, input_text: Any, extra: Dict[str, Any]) -> str:
        if isinstance(input_text, str) and not extra:
            return input_text
        data: Dict[str, Any] = {}
        if input_text not in ("", None):
            data["input"] = input_text
        if extra:
            data.update(extra)
        return json.dumps(data)

    def _build_task_prompt(self, task_spec, agent_spec: AgentSpec) -> str:
        return textwrap.dedent(
            f"""
            Task: {task_spec.description}
            Task Input: {task_spec.input}
            Expected tools: {agent_spec.tools}
            Follow the workflow, calling registered functions by name.
            When completed, respond with 'FINAL: <summary>'.
            """
        ).strip()

    def _extract_content(self, result: Any) -> str:
        if isinstance(result, str):
            return result
        if isinstance(result, dict):
            return result.get("content", "")
        return str(result)

    def _is_final_message(self, message: Dict[str, Any]) -> bool:
        content = (message or {}).get("content", "") or ""
        return content.strip().upper().startswith("FINAL:")
