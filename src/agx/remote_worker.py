"""Remote worker helpers for AGX control-plane/worker execution."""

from __future__ import annotations

import io
import json
import socket
import time
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from typing import Any, Dict, List, Optional

import yaml

from .agents.manifest import normalize_manifest, validate_manifest
from .agents.orchestrator import Orchestrator
from .autogen_runner import AutogenOrchestrator
from .config import ProjectConfig
from .runtime.interoperability import parse_output_text
from .tasks.runner import TaskRunner
from .tools.base import ToolContext
from .tools.builtin import register_builtin_tools
from .tools.registry import ToolRegistry
from .workspace import resolve_workspace_paths


def discover_worker_agents(
    *,
    agents_dir: Optional[Path] = None,
    registry_path: Optional[Path] = None,
    base_dir: Optional[Path] = None,
) -> List[Dict[str, Any]]:
    workspace = resolve_workspace_paths(base_dir=base_dir)
    agent_root = (agents_dir or workspace.agents_dir).expanduser().resolve()
    registry = (registry_path or workspace.registry_path).expanduser().resolve()
    if not agent_root.exists() or not registry.exists():
        return []

    try:
        registry_data = yaml.safe_load(registry.read_text()) or {}
    except Exception:
        return []
    allowed = registry_data.get("agents") if isinstance(registry_data, dict) else []
    if not isinstance(allowed, list):
        return []

    discovered: List[Dict[str, Any]] = []
    for slug in sorted(str(item) for item in allowed):
        agent_dir = agent_root / slug
        if not agent_dir.is_dir():
            continue
        manifest_path = agent_dir / "agent.yaml"
        if not manifest_path.exists():
            manifest_path = agent_dir / "agent.yml"
        if not manifest_path.exists():
            continue
        try:
            manifest = yaml.safe_load(manifest_path.read_text()) or {}
        except Exception:
            continue
        if not isinstance(manifest, dict):
            continue
        errors = validate_manifest(manifest)
        if errors:
            continue
        manifest = normalize_manifest(manifest)
        config_rel = str(manifest.get("config_path") or manifest.get("config") or "config.yaml")
        config_path = (agent_dir / config_rel).resolve()
        if not config_path.exists():
            continue
        try:
            config_data = yaml.safe_load(config_path.read_text()) or {}
        except Exception:
            continue
        if not isinstance(config_data, dict):
            continue
        discovered.append(
            {
                "agent_slug": slug,
                "agent_name": str(manifest.get("name") or slug),
                "manifest": manifest,
                "config": config_data,
                "config_path": str(config_path),
            }
        )
    return discovered


def hostname() -> str:
    return socket.gethostname()


def execute_remote_task(
    *,
    config_path: Path,
    task_id: str,
    engine: str,
    input_value: Any,
    context_value: Dict[str, Any],
    run_id: str,
) -> Dict[str, Any]:
    config = ProjectConfig.from_file(config_path)
    spec = next((item for item in config.tasks if item.id == task_id), None)
    if spec is None:
        raise RuntimeError(f"Task '{task_id}' not found in {config_path}")
    spec.input = input_value
    spec.context = context_value

    started = time.perf_counter()
    capture = io.StringIO()
    output = ""
    metadata: Dict[str, Any] = {}
    with redirect_stdout(capture), redirect_stderr(capture):
        if spec.task_type == "tool_run":
            output, metadata = _run_tool_task(config, spec, run_id=run_id)
        elif engine == "legacy":
            orchestrator = Orchestrator(config)
            task_lookup = {task.id: task for task in orchestrator.tasks}
            task = task_lookup.get(task_id)
            if task is None:
                raise RuntimeError(f"Legacy task '{task_id}' not found in orchestrator")
            task.input = input_value
            task.context = context_value
            result = orchestrator.runner.run(task)
            output = result.output
        else:
            orchestrator = AutogenOrchestrator(config)
            output = orchestrator.run_task(spec)
    duration = time.perf_counter() - started
    console_text = capture.getvalue().strip()
    console = [console_text] if console_text else []
    payload: Dict[str, Any] = {
        "output": output,
        "duration": duration,
        "parsed_output": parse_output_text(output),
        "console": console,
    }
    if metadata:
        payload["metadata"] = metadata
    return payload


def _run_tool_task(config: ProjectConfig, spec: Any, *, run_id: str) -> tuple[str, Dict[str, Any]]:
    tool_registry = ToolRegistry()
    register_builtin_tools(tool_registry)
    tool_registry.configure_from_specs(config.tool_specs)
    tool_name = getattr(spec, "tool", None)
    if not tool_name:
        raise RuntimeError(f"Tool run task '{spec.id}' is missing a tool name")
    tool = tool_registry.get(tool_name)
    input_value = getattr(spec, "input", None)
    if isinstance(input_value, (dict, list)):
        input_text = json.dumps(input_value)
    elif input_value is None:
        input_text = ""
    else:
        input_text = str(input_value)
    result = tool.run(
        input_text=input_text,
        context=ToolContext(
            agent_name=getattr(spec, "agent", "tool"),
            task_id=spec.id,
            iteration=0,
            metadata={"tool": str(tool_name), "host": hostname(), "run_id": run_id},
        ),
    )
    return result.content, dict(result.metadata or {})
