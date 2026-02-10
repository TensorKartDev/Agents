"""Provider abstractions used by the agent runtime."""

from __future__ import annotations

import itertools
import json
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Any, Dict, Iterable, Protocol


@dataclass
class PromptContext:
    """Metadata about the prompt being generated."""

    agent_name: str
    task_id: str
    iteration: int


class LLMProvider(Protocol):
    """Interface for language model providers."""

    def generate(self, prompt: str, context: PromptContext) -> str:  # pragma: no cover - interface
        """Return a response for the given prompt."""


class ConsoleEchoProvider:
    """Fallback provider that asks the human operator for a response."""

    def __init__(self, prefix: str = "Agent response") -> None:
        self.prefix = prefix

    def generate(self, prompt: str, context: PromptContext) -> str:
        header = f"\n[{self.prefix}] {context.agent_name}:{context.task_id} iteration {context.iteration}\n"
        print(header)
        print(prompt)
        print("Type JSON response (end with empty line):")
        lines = []
        while True:
            try:
                line = input()
            except EOFError:  # pragma: no cover - console only
                break
            if not line:
                break
            lines.append(line)
        return "\n".join(lines)


class StaticResponseProvider:
    """Provider that replays a finite list of responses (useful for tests)."""

    def __init__(self, responses: Iterable[str]):
        self._responses = iter(responses)
        self._counter = itertools.count()

    def generate(self, prompt: str, context: PromptContext) -> str:
        try:
            return next(self._responses)
        except StopIteration as exc:  # pragma: no cover - debug guard
            raise RuntimeError("StaticResponseProvider exhausted") from exc


class OllamaProvider:
    """Calls a locally hosted Ollama model via its HTTP API."""

    def __init__(
        self,
        model: str,
        *,
        host: str = "http://localhost:11434",
        options: Dict[str, Any] | None = None,
        system_prompt: str | None = None,
        timeout: float = 120.0,
    ) -> None:
        self.model = model
        self.host = host.rstrip("/")
        self.options = options or {}
        self.system_prompt = system_prompt
        self.timeout = timeout

    def generate(self, prompt: str, context: PromptContext) -> str:
        payload: Dict[str, Any] = {
            "model": self.model,
            "prompt": prompt,
            "stream": False,
            "options": self.options,
        }
        if self.system_prompt:
            payload["system"] = self.system_prompt.format(
                agent=context.agent_name, task=context.task_id, iteration=context.iteration
            )
        request = urllib.request.Request(
            url=f"{self.host}/api/generate",
            data=json.dumps(payload).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as response:
                body = response.read().decode("utf-8")
        except urllib.error.URLError as exc:
            raise RuntimeError(f"OllamaProvider failed to reach {self.host}: {exc}") from exc
        data = json.loads(body)
        if "error" in data:
            raise RuntimeError(f"OllamaProvider error: {data['error']}")
        result = data.get("response")
        if not isinstance(result, str):
            raise RuntimeError(f"OllamaProvider returned unexpected payload: {data}")
        return result.strip()
