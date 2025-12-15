"""Core agent and planning loop implementations."""

from __future__ import annotations

import json
import textwrap
from dataclasses import dataclass
from typing import Dict, List

from ..llm.provider import LLMProvider, PromptContext
from ..memory.simple import ConversationBufferMemory
from ..tasks.base import Task, TaskResult
from ..tools.base import Tool, ToolContext


@dataclass
class AgentAction:
    """Parsed output from the model."""

    thought: str
    action: str
    action_input: str
    answer: str | None = None

    @property
    def is_final(self) -> bool:
        return self.action == "final"


@dataclass
class PlanningConfig:
    max_iterations: int
    reflection: bool


class Agent:
    """Agent that iteratively plans and executes tool calls."""

    def __init__(
        self,
        name: str,
        description: str,
        llm_provider: LLMProvider,
        tools: Dict[str, Tool],
        planning: PlanningConfig,
        memory: ConversationBufferMemory | None = None,
    ) -> None:
        self.name = name
        self.description = description
        self.llm_provider = llm_provider
        self.tools = tools
        self.planning = planning
        self.memory = memory or ConversationBufferMemory()

    def run_task(self, task: Task) -> TaskResult:
        loop = PlanningLoop(agent=self, task=task)
        return loop.execute()


class PlanningLoop:
    """Simple ReAct-style planning loop."""

    def __init__(self, agent: Agent, task: Task) -> None:
        self.agent = agent
        self.task = task
        self.trace: List[str] = []

    def execute(self) -> TaskResult:
        for iteration in range(1, self.agent.planning.max_iterations + 1):
            prompt = self._build_prompt(iteration)
            context = PromptContext(
                agent_name=self.agent.name,
                task_id=self.task.id,
                iteration=iteration,
            )
            response = self.agent.llm_provider.generate(prompt, context)
            action = self._parse_response(response)
            self.trace.append(f"model@{iteration}: {response}")
            if action.is_final:
                self.agent.memory.add("assistant", action.answer or action.action_input)
                return TaskResult(
                    task=self.task,
                    success=True,
                    output=action.answer or action.action_input,
                    iterations=iteration,
                    trace=self.trace,
                )
            observation = self._invoke_tool(action, iteration)
            self.agent.memory.add("tool", observation)
        # max iterations reached
        return TaskResult(
            task=self.task,
            success=False,
            output="Max iterations reached without final answer",
            iterations=self.agent.planning.max_iterations,
            trace=self.trace,
        )

    def _build_prompt(self, iteration: int) -> str:
        tools_desc = "\n".join(
            f"- {name}: {tool.description}" for name, tool in self.agent.tools.items()
        )
        memory_dump = "\n".join(f"{item.role}: {item.content}" for item in self.agent.memory.dump())
        scratch = textwrap.dedent(
            f"""
            You are agent {self.agent.name}. Task: {self.task.description}.
            You MUST respond using JSON with keys thought, action, input, answer (answer required when action == "final").
            Tools available:\n{tools_desc or '- none'}
            Task input: {self.task.input}
            Context: {self.task.context}
            Current memory:\n{memory_dump or 'empty'}
            Iteration: {iteration}
            """
        ).strip()
        return scratch

    def _parse_response(self, response: str) -> AgentAction:
        try:
            payload = json.loads(response)
        except json.JSONDecodeError:
            # Treat as direct answer
            return AgentAction(
                thought="Responding directly",
                action="final",
                action_input=response,
                answer=response,
            )
        return AgentAction(
            thought=payload.get("thought", ""),
            action=payload.get("action", "final"),
            action_input=payload.get("input", ""),
            answer=payload.get("answer"),
        )

    def _invoke_tool(self, action: AgentAction, iteration: int) -> str:
        tool_name = action.action
        if tool_name not in self.agent.tools:
            return f"Unknown tool '{tool_name}'"
        tool = self.agent.tools[tool_name]
        result = tool.run(
            input_text=action.action_input,
            context=ToolContext(
                agent_name=self.agent.name,
                task_id=self.task.id,
                iteration=iteration,
                metadata={"task_description": self.task.description},
            ),
        )
        observation = f"Tool {tool_name} => {result.content}"
        self.trace.append(observation)
        return observation
