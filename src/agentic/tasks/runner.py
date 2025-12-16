"""Task runner utilities."""

from __future__ import annotations

from typing import Callable, Dict

from .base import Task, TaskResult


class TaskRunner:
    """Executes tasks by dispatching them to the correct agent."""

    def __init__(self, agent_resolver: Callable[[str], object]):
        self._resolver = agent_resolver
        self._results: Dict[str, TaskResult] = {}

    def run(self, task: Task) -> TaskResult:
        agent = self._resolver(task.agent_name)
        if not hasattr(agent, "run_task"):
            raise AttributeError(f"Agent {task.agent_name} missing run_task method")
        result: TaskResult = agent.run_task(task)
        self._results[task.id] = result
        return result

    def results(self) -> Dict[str, TaskResult]:
        return dict(self._results)
