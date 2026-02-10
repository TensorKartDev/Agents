"""Autogen-powered orchestrator that aligns with Microsoft's Agent Framework."""

from __future__ import annotations

import json
import textwrap
from pathlib import Path
from typing import Any, Dict

from autogen import AssistantAgent, UserProxyAgent

from .config import AgentSpec, ProjectConfig
from .tasks.runner import TaskRunner, TaskStateRecord, TaskStateStore
from .tasks.base import TaskState
from .tools.base import ToolContext
from .tools.builtin import register_builtin_tools
from .tools.registry import ToolRegistry


class AutogenOrchestrator:
    """Runs project tasks using Microsoft Autogen agents backed by Ollama."""

    def __init__(self, project_config: ProjectConfig, approval_callback=None) -> None:
        self.config = project_config
        self.tool_registry = ToolRegistry()
        register_builtin_tools(self.tool_registry)
        self.tool_registry.configure_from_specs(self.config.tool_specs)
        self._store = TaskStateStore(Path(".agx") / "task_state.db")
        self._approval_callback = approval_callback

    def run(self) -> Dict[str, str]:
        outputs: Dict[str, str] = {}
        ordered = TaskRunner.order_tasks(self.config.tasks)
        for task_spec in ordered:
            self._store.upsert(TaskStateRecord(task_id=task_spec.id, state=TaskState.PENDING))
        for task_spec in ordered:
            if task_spec.task_type == "human_approval":
                existing = self._store.fetch(task_spec.id)
                if existing and existing.approved:
                    outputs[task_spec.id] = "Approved"
                    self._store.upsert(
                        TaskStateRecord(
                            task_id=task_spec.id,
                            state=TaskState.COMPLETED,
                            output="Approved",
                            approved=True,
                            reason=task_spec.reason,
                        )
                    )
                    continue
                if self._approval_callback is not None:
                    decision = self._approval_callback(task_spec)
                    if decision:
                        outputs[task_spec.id] = "Approved"
                        self._store.upsert(
                            TaskStateRecord(
                                task_id=task_spec.id,
                                state=TaskState.COMPLETED,
                                output="Approved",
                                approved=True,
                                reason=task_spec.reason,
                            )
                        )
                        continue
                wait_msg = f"WAITING_HUMAN: {task_spec.reason or ''}".strip()
                outputs[task_spec.id] = wait_msg
                self._store.upsert(
                    TaskStateRecord(
                        task_id=task_spec.id,
                        state=TaskState.WAITING_HUMAN,
                        output=wait_msg,
                        approved=False,
                        reason=task_spec.reason,
                    )
                )
                break
            if task_spec.task_type == "human_input":
                wait_msg = "WAITING_INPUT"
                outputs[task_spec.id] = wait_msg
                self._store.upsert(
                    TaskStateRecord(
                        task_id=task_spec.id,
                        state=TaskState.WAITING_HUMAN,
                        output=wait_msg,
                        approved=False,
                        reason=task_spec.description,
                    )
                )
                break

            self._store.upsert(TaskStateRecord(task_id=task_spec.id, state=TaskState.RUNNING))
            try:
                outputs[task_spec.id] = self.run_task(task_spec)
                self._store.upsert(
                    TaskStateRecord(
                        task_id=task_spec.id,
                        state=TaskState.COMPLETED,
                        output=outputs[task_spec.id],
                    )
                )
            except Exception as exc:
                self._store.upsert(
                    TaskStateRecord(
                        task_id=task_spec.id,
                        state=TaskState.FAILED,
                        error=str(exc),
                    )
                )
                raise
        return outputs

    def run_task(self, task_spec) -> str:
        agent_spec = self.config.get_agent(task_spec.agent)
        assistant = self._build_assistant(agent_spec, task_spec.id)
        user = self._build_user(task_spec.id)
        prompt = self._build_task_prompt(task_spec, agent_spec)
        result = user.initiate_chat(
            assistant,
            message=prompt,
            max_turns=max(4, agent_spec.planning.max_iterations * 2),
        )
        return self._extract_content(result)

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
                {
                    "name": tool_name,
                    "description": tool.description or tool_name,
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "input_text": {"type": "string"},
                        },
                    },
                    "function": self._wrap_tool(tool_name, tool, agent_spec.name, task_id),
                }
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
        if agent_spec.self_deciding:
            behavior = (
                "You may propose actions, commands, or code as recommendations. "
                "Clearly label them as proposed and do not claim execution."
            )
        else:
            behavior = "Do not output code or commands. Provide recommendations only."
        task_input = self._format_task_input(task_spec.input)
        return textwrap.dedent(
            f"""
            Task: {task_spec.description}
            Task Input (JSON if available): {task_input}
            Expected tools: {agent_spec.tools}
            Behavior: {behavior}
            Follow the workflow, calling registered functions by name.
            When completed, respond with 'FINAL: <summary>'.
            """
        ).strip()

    def _format_task_input(self, value: Any) -> str:
        if isinstance(value, (dict, list)):
            try:
                return json.dumps(value, indent=2)
            except Exception:
                return str(value)
        if value is None:
            return ""
        return str(value)

    def _extract_content(self, result: Any) -> str:
        if isinstance(result, str):
            return self._dedupe_content(result)
        if isinstance(result, dict):
            content = result.get("content", "") or str(result)
            return self._dedupe_content(content)
        # Autogen ChatResult objects typically expose chat_history/summary
        summary = getattr(result, "summary", None)
        if isinstance(summary, str) and summary.strip():
            return self._dedupe_content(summary)
        chat_history = getattr(result, "chat_history", None)
        if isinstance(chat_history, list) and chat_history:
            for item in reversed(chat_history):
                if isinstance(item, dict):
                    content = item.get("content")
                    if isinstance(content, str) and content.strip():
                        return self._dedupe_content(content)
        content = getattr(result, "content", None)
        if isinstance(content, str) and content.strip():
            return self._dedupe_content(content)
        return self._dedupe_content(str(result))

    @staticmethod
    def _dedupe_content(content: str) -> str:
        text = content.strip()
        if not text:
            return content
        # If content is duplicated back-to-back, keep one.
        half = len(text) // 2
        if len(text) % 2 == 0 and text[:half] == text[half:]:
            return text[:half].strip()
        # If multiple FINAL blocks, keep the first distinct block.
        if text.count("FINAL:") >= 2:
            first = text.find("FINAL:")
            rest = text.find("FINAL:", first + 6)
            if rest != -1:
                return text[:rest].strip()
        return content

    def _is_final_message(self, message: Dict[str, Any]) -> bool:
        content = (message or {}).get("content", "") or ""
        return content.strip().upper().startswith("FINAL:")
