"""FastAPI web server that visualizes agent runs with live status updates."""

from __future__ import annotations

import asyncio
import json
import os
import time
import uuid
import re
import subprocess
import io
import contextlib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

import yaml
from fastapi import FastAPI, HTTPException, WebSocket, WebSocketDisconnect
from starlette.websockets import WebSocketState
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from ..agents.manifest import normalize_manifest, validate_manifest
from .. import __version__ as agx_version
from ..agents.orchestrator import Orchestrator
from ..autogen_runner import AutogenOrchestrator
from ..config import ProjectConfig
from ..persistence import PostgresRunStore


def _load_structured(text: Any) -> Any:
    if isinstance(text, (dict, list)):
        return text
    if not isinstance(text, str):
        return text
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        try:
            return yaml.safe_load(text)
        except yaml.YAMLError:
            return text


def _run_command(args: List[str], *, timeout: int = 120) -> Dict[str, Any]:
    t0 = time.perf_counter()
    try:
        proc = subprocess.run(args, capture_output=True, text=True, timeout=timeout)
        t1 = time.perf_counter()
        return {
            "code": proc.returncode,
            "stdout": proc.stdout.strip(),
            "stderr": proc.stderr.strip(),
            "duration": t1 - t0,
        }
    except FileNotFoundError:
        t1 = time.perf_counter()
        return {"code": 127, "stdout": "", "stderr": f"{args[0]} not found", "duration": t1 - t0}
    except subprocess.TimeoutExpired:
        t1 = time.perf_counter()
        return {"code": -1, "stdout": "", "stderr": f"timeout after {timeout}s", "duration": t1 - t0}
    except Exception as exc:
        t1 = time.perf_counter()
        return {"code": 1, "stdout": "", "stderr": f"failed to run {' '.join(args)}: {exc}", "duration": t1 - t0}


def _summarize(label: str, result: Dict[str, Any], *, limit: int = 1200) -> str:
    output = (result.get("stdout") or result.get("stderr") or "").strip()
    if not output:
        output = "<no output>"
    if len(output) > limit:
        output = output[:limit] + "\n...[truncated]..."
    status = "ok" if result.get("code") == 0 else f"exit {result.get('code')}"
    duration = result.get("duration")
    timing = f" in {duration:.2f}s" if isinstance(duration, (int, float)) else ""
    return f"{label} ({status}{timing}):\n{output}"


def _run_with_capture(fn, *args, **kwargs):
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf), contextlib.redirect_stderr(buf):
        result = fn(*args, **kwargs)
    return result, buf.getvalue()
from ..tasks.runner import TaskRunner
from ..tools.builtin import register_builtin_tools
from ..tools.registry import ToolRegistry
from ..tools.base import ToolContext


app = FastAPI(title="AGX Web Runner")
app.mount("/static", StaticFiles(directory=Path(__file__).parent), name="static")

BASE_DIR = Path(__file__).resolve().parents[3]  # repo root
AGENTS_DIR = BASE_DIR / "agents"
AGENT_REGISTRY = AGENTS_DIR / "agents.yaml"
STATIC_IMG = Path(__file__).parent / "img"
INDEX_HTML = Path(__file__).parent / "index.html"
RUNS_DIR = BASE_DIR / ".agx" / "runs"

if AGENTS_DIR.exists():
    # Mount so icons and assets inside agent folders can be served statically.
    app.mount("/agents", StaticFiles(directory=AGENTS_DIR), name="agents")

# Mount default static images (e.g., robot.svg) if present
if STATIC_IMG.exists():
    app.mount("/static/img", StaticFiles(directory=STATIC_IMG), name="static-img")


@app.on_event("startup")
async def init_run_store() -> None:
    global RUN_STORE
    db_url = os.getenv("AGX_DB_URL", "").strip()
    if not db_url:
        return
    RUN_STORE = PostgresRunStore(db_url)
    for record in RUN_STORE.list_runs():
        try:
            config = ProjectConfig.from_file(record.config_path)
        except Exception:
            continue
        state = RunState(
            config=config,
            engine=record.engine,
            config_path=record.config_path,
            requested_path=record.requested_path,
            total_tasks=record.total_tasks,
            completed_tasks=record.completed_tasks,
        )
        state.completed = record.completed
        state.stop_requested = record.stop_requested
        state.started_at = record.started_at
        events = RUN_STORE.list_events(record.run_id)
        state.history = list(events)
        state.event_seq = len(events)
        RUNS[record.run_id] = state


@dataclass
class AgentInfo:
    id: str
    name: str
    description: str
    icon: str
    config_path: str
    llm_host: Optional[str] = None
    tool_host: Optional[str] = None
    inputs: Optional[Any] = None
    outputs: Optional[Any] = None
    capabilities: Optional[List[str]] = None
    version: Optional[str] = None
    compatibility: Optional[Dict[str, Any]] = None
    pricing: Optional[Dict[str, Any]] = None


def _load_manifest(path: Path, *, validate: bool = True) -> Optional[Dict[str, Any]]:
    try:
        with path.open() as f:
            data = yaml.safe_load(f)
            if not isinstance(data, dict):
                return None
            if validate:
                errors = validate_manifest(data)
                if errors:
                    msg = "; ".join(errors)
                    print(f"[agx] Invalid agent manifest {path}: {msg}")
                    return None
                return normalize_manifest(data)
            return data
    except Exception:
        return None


def scan_for_agents() -> List[AgentInfo]:
    """Scan the 'agents' directory for agent packages."""
    agents: List[AgentInfo] = []
    if not AGENTS_DIR.exists():
        return agents

    registry_path = AGENT_REGISTRY
    if not registry_path.exists():
        alt = AGENTS_DIR / "Agents.yaml"
        if alt.exists():
            registry_path = alt
        else:
            alt = AGENTS_DIR / "Agents.YAML"
            if alt.exists():
                registry_path = alt
            else:
                alt = AGENTS_DIR / "registry.yaml"
                if alt.exists():
                    registry_path = alt
    registry_data = _load_manifest(registry_path, validate=False) if registry_path.exists() else None
    allowed = set(registry_data.get("agents", [])) if registry_data else set()
    if not allowed:
        return agents

    for agent_dir in AGENTS_DIR.iterdir():
        if not agent_dir.is_dir():
            continue
        if agent_dir.name not in allowed:
            continue

        manifest_path = agent_dir / "agent.yaml"
        if not manifest_path.exists():
            manifest_path = agent_dir / "agent.yml"
        if not manifest_path.exists():
            continue

        manifest = _load_manifest(manifest_path)
        if not manifest:
            continue

        icon_field = manifest.get("icon")
        icon_candidate = agent_dir / icon_field if icon_field else None
        icon_path = (
            f"/agents/{agent_dir.name}/{icon_field}"
            if icon_candidate and icon_candidate.exists()
            else "/static/img/robot.svg"
        )

        config_path_value = manifest.get("config_path") or manifest.get("config")
        config_path = (agent_dir / config_path_value) if config_path_value else manifest_path
        # Prefer an absolute path for downstream consumers (/api/run expects a real file)
        config_path = config_path.resolve()
        if not config_path.exists():
            # Skip invalid entries; keep UI clean for creators
            continue

        agents.append(
            AgentInfo(
                id=agent_dir.name,
                name=manifest.get("name", agent_dir.name.replace("_", " ").title()),
                description=manifest.get("description", ""),
                icon=icon_path,
                config_path=str(config_path),
                llm_host=manifest.get("llm_host"),
                tool_host=manifest.get("tool_host"),
                inputs=manifest.get("inputs"),
                outputs=manifest.get("outputs"),
                capabilities=manifest.get("capabilities"),
                version=manifest.get("version"),
                compatibility=manifest.get("compatibility"),
                pricing=manifest.get("pricing"),
            )
        )

    return agents


@app.get("/api/agents")
async def list_agents() -> JSONResponse:
    """Return a list of discoverable agents."""
    agents = scan_for_agents()
    return JSONResponse([agent.__dict__ for agent in agents])


@app.get("/api/meta")
async def meta() -> Dict[str, Any]:
    return {"name": "AGX Framework", "version": agx_version}


@dataclass
class RunState:
    config: ProjectConfig
    engine: str
    config_path: str
    history: List[Dict[str, Any]] = field(default_factory=list)
    subscribers: List[asyncio.Queue] = field(default_factory=list)
    task: asyncio.Task | None = None
    completed: bool = False
    total_tasks: int = 0
    completed_tasks: int = 0
    started_at: float = field(default_factory=time.time)
    stop_requested: bool = False
    requested_path: str = ""
    pending_input: "PendingInput | None" = None
    pending_approval: "PendingApproval | None" = None
    event_seq: int = 0


RUNS: Dict[str, RunState] = {}
RUN_STORE: PostgresRunStore | None = None


class RunRequest(BaseModel):
    config_path: str
    engine: str = "autogen"


class InputSubmit(BaseModel):
    fields: Dict[str, Any]


class ApprovalSubmit(BaseModel):
    approved: bool
    reason: Optional[str] = None


@dataclass
class PendingInput:
    task_id: str
    spec: Any
    event: asyncio.Event = field(default_factory=asyncio.Event)
    response: Optional[Dict[str, Any]] = None


@dataclass
class PendingApproval:
    task_id: str
    reason: str = ""
    event: asyncio.Event = field(default_factory=asyncio.Event)
    approved: Optional[bool] = None
    response_reason: Optional[str] = None


def _run_dir(run_id: str) -> Path:
    return RUNS_DIR / run_id


def _artifacts_dir(run_id: str) -> Path:
    return _run_dir(run_id) / "artifacts"


def _manifest_path(run_id: str) -> Path:
    return _run_dir(run_id) / "manifest.json"


def _ensure_run_dirs(run_id: str) -> None:
    run_dir = _run_dir(run_id)
    artifacts = _artifacts_dir(run_id)
    artifacts.mkdir(parents=True, exist_ok=True)
    manifest_path = _manifest_path(run_id)
    if not manifest_path.exists():
        manifest = {
            "run_id": run_id,
            "created_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "inputs": [],
            "approvals": [],
        }
        manifest_path.write_text(json.dumps(manifest, indent=2))


def _append_manifest_entry(run_id: str, key: str, entry: Dict[str, Any]) -> None:
    manifest_path = _manifest_path(run_id)
    if not manifest_path.exists():
        _ensure_run_dirs(run_id)
    data = json.loads(manifest_path.read_text())
    items = data.get(key)
    if not isinstance(items, list):
        data[key] = []
    data[key].append(entry)
    manifest_path.write_text(json.dumps(data, indent=2))


def _extract_json_payload(text: str) -> Any:
    if not isinstance(text, str):
        return text
    stripped = text.strip()
    if not stripped:
        return None
    # Remove leading FINAL: marker if present
    if stripped.upper().startswith("FINAL:"):
        stripped = stripped[6:].strip()
    # Try direct JSON
    try:
        return json.loads(stripped)
    except Exception:
        pass
    # Try to locate first JSON object/array in text
    for start in ("{", "["):
        idx = stripped.find(start)
        if idx != -1:
            try:
                return json.loads(stripped[idx:])
            except Exception:
                continue
    return None


@app.get("/")
async def root() -> FileResponse:
    if not INDEX_HTML.exists():
        raise HTTPException(status_code=500, detail="UI not found")
    return FileResponse(INDEX_HTML)


@app.post("/api/run")
async def start_run(request: RunRequest) -> Dict[str, Any]:
  print(f"[server] /api/run called with config_path={request.config_path} engine={request.engine}")
  config_path = Path(request.config_path)
  if not config_path.exists():
    raise HTTPException(status_code=400, detail=f"Config not found: {config_path}")
  resolved_path = str(config_path.resolve())
  # If a run for the same config is already active, reuse it instead of starting a duplicate
  for existing_id, existing_state in RUNS.items():
    if not existing_state.completed and existing_state.config_path == resolved_path:
      return {"run_id": existing_id, "project": existing_state.config.name, "already_running": True}
  config = ProjectConfig.from_file(str(config_path))
  run_id = str(uuid.uuid4())
  _ensure_run_dirs(run_id)
  state = RunState(
      config=config,
      engine=request.engine,
      config_path=resolved_path,
      requested_path=request.config_path,
      total_tasks=len(config.tasks),
      completed_tasks=0,
  )
  RUNS[run_id] = state
  if RUN_STORE is not None:
    RUN_STORE.create_run(
        run_id=run_id,
        project=config.name,
        engine=request.engine,
        config_path=resolved_path,
        requested_path=request.config_path,
        total_tasks=len(config.tasks),
        completed_tasks=0,
        completed=False,
        stop_requested=False,
        started_at=state.started_at,
    )
  state.task = asyncio.create_task(execute_run(run_id, config, request.engine))
  return {"run_id": run_id, "project": config.name}


@app.post("/api/run/{run_id}/stop")
async def stop_run(run_id: str) -> Dict[str, Any]:
    state = RUNS.get(run_id)
    if not state:
        raise HTTPException(status_code=404, detail=f"Run not found: {run_id}")
    state.stop_requested = True
    state.completed = True
    if state.task and not state.task.done():
        state.task.cancel()
    if RUN_STORE is not None:
        RUN_STORE.update_run(
            run_id=run_id,
            completed_tasks=state.completed_tasks,
            completed=True,
            stop_requested=True,
        )
    return {"run_id": run_id, "stopped": True}


@app.post("/api/run/{run_id}/input/{task_id}")
async def submit_input(run_id: str, task_id: str, payload: InputSubmit) -> Dict[str, Any]:
    state = RUNS.get(run_id)
    if not state:
        raise HTTPException(status_code=404, detail=f"Run not found: {run_id}")
    pending = state.pending_input
    if not pending or pending.task_id != task_id:
        raise HTTPException(status_code=409, detail="No pending input for this task")

    fields = payload.fields or {}
    ui = getattr(pending.spec, "ui", None) or {}
    field_defs = ui.get("fields") if isinstance(ui, dict) else None
    field_defs = field_defs if isinstance(field_defs, list) else []
    errors = []
    for field in field_defs:
        if not isinstance(field, dict):
            continue
        field_id = field.get("id")
        if not field_id:
            continue
        required = bool(field.get("required", False))
        kind = str(field.get("kind") or "text").lower()
        value = fields.get(field_id)
        if required and (value is None or (isinstance(value, str) and not value.strip())):
            errors.append(f"{field_id} is required")
            continue
        if value is None or value == "":
            continue
        if kind in {"path", "file", "folder", "dir", "directory"} and field.get("must_exist", True):
            path = Path(str(value)).expanduser()
            if not path.exists():
                errors.append(f"{field_id} path not found")
    if errors:
        raise HTTPException(status_code=400, detail="; ".join(errors))

    pending.response = fields
    _append_manifest_entry(
        run_id,
        "inputs",
        {
            "task_id": task_id,
            "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "fields": fields,
            "ui": ui,
        },
    )
    pending.event.set()
    return {"run_id": run_id, "task_id": task_id, "received": True}


@app.post("/api/run/{run_id}/approve/{task_id}")
async def submit_approval(run_id: str, task_id: str, payload: ApprovalSubmit) -> Dict[str, Any]:
    state = RUNS.get(run_id)
    if not state:
        raise HTTPException(status_code=404, detail=f"Run not found: {run_id}")
    pending = state.pending_approval
    if not pending or pending.task_id != task_id:
        raise HTTPException(status_code=409, detail="No pending approval for this task")

    pending.approved = bool(payload.approved)
    pending.response_reason = payload.reason
    _append_manifest_entry(
        run_id,
        "approvals",
        {
            "task_id": task_id,
            "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "approved": pending.approved,
            "reason": payload.reason or pending.reason,
        },
    )
    pending.event.set()
    return {"run_id": run_id, "task_id": task_id, "approved": pending.approved}


async def execute_run(run_id: str, config: ProjectConfig, engine: str) -> None:
    state = RUNS[run_id]
    state.total_tasks = len(config.tasks)
    state.completed_tasks = 0
    state.stop_requested = False
    tool_registry = ToolRegistry()
    register_builtin_tools(tool_registry)
    tool_registry.configure_from_specs(config.tool_specs)
    input_store: Dict[str, Dict[str, Any]] = {}
    result_store: Dict[str, Dict[str, Any]] = {}

    async def broadcast(event: Dict[str, Any]) -> None:
        state.history.append(event)
        state.event_seq += 1
        if RUN_STORE is not None:
            RUN_STORE.append_event(run_id, state.event_seq, event)
        for queue in list(state.subscribers):
            await queue.put(event)

    def persist_run() -> None:
        if RUN_STORE is None:
            return
        RUN_STORE.update_run(
            run_id=run_id,
            completed_tasks=state.completed_tasks,
            completed=state.completed,
            stop_requested=state.stop_requested,
        )

    await broadcast(
        {
            "type": "plan",
            "project": config.name,
            "engine": engine,
            "tasks": [
                {"id": task.id, "agent": task.agent, "description": task.description}
                for task in config.tasks
            ],
        }
    )

    task_specs = TaskRunner.order_tasks(list(config.tasks))
    results: Dict[str, Any] = {}
    run_start = time.perf_counter()
    stopped_early = False

    if engine == "legacy":
        orchestrator = Orchestrator(config)
        task_lookup = {task.id: task for task in orchestrator.tasks}

        def run_single(task_spec):
            task_obj = task_lookup[task_spec.id]
            return orchestrator.runner.run(task_obj).output

    else:
        orchestrator = AutogenOrchestrator(config)

        def run_single(task_spec):
            return orchestrator.run_task(task_spec)

    def resolve_input(value: Any) -> Any:
        if value is None:
            return value
        if isinstance(value, dict):
            return {k: resolve_input(v) for k, v in value.items()}
        if isinstance(value, list):
            return [resolve_input(v) for v in value]
        if isinstance(value, str):
            out = value
            for task_id, fields in input_store.items():
                for key, val in fields.items():
                    token = f"{{{{inputs.{task_id}.{key}}}}}"
                    if token in out:
                        out = out.replace(token, str(val))
            for task_id, fields in result_store.items():
                for key, val in fields.items():
                    token = f"{{{{results.{task_id}.{key}}}}}"
                    if token in out:
                        out = out.replace(token, str(val))
            return out
        return value

    for spec in task_specs:
      await broadcast({"type": "status", "task_id": spec.id, "status": "pending"})

    try:
      for spec in task_specs:
        if state.stop_requested:
          stopped_early = True
          break

        task_type = getattr(spec, "task_type", None)
        if task_type == "human_input":
          wait_msg = "WAITING_INPUT"
          state.pending_input = PendingInput(task_id=spec.id, spec=spec)
          await broadcast(
            {
              "type": "status",
              "task_id": spec.id,
              "status": "WAITING_HUMAN",
              "output": wait_msg,
              "duration": 0,
            }
          )
          await broadcast(
            {
              "type": "input_request",
              "task_id": spec.id,
              "title": getattr(spec, "description", "Input required"),
              "description": getattr(spec, "description", ""),
              "ui": getattr(spec, "ui", None),
            }
          )
          await state.pending_input.event.wait()
          if state.stop_requested:
            stopped_early = True
            break
          payload = state.pending_input.response or {}
          input_store[spec.id] = dict(payload)
          output = json.dumps(payload)
          duration = 0
          result_store[spec.id] = {"output": output, "duration": duration, "input": payload}
          results[spec.id] = result_store[spec.id]
          state.completed_tasks += 1
          persist_run()
          await broadcast(
            {
              "type": "status",
              "task_id": spec.id,
              "status": "completed",
              "output": output,
              "duration": duration,
            }
          )
          state.pending_input = None
          continue

        if task_type == "human_approval":
          reason = getattr(spec, "reason", "") or getattr(spec, "description", "")
          state.pending_approval = PendingApproval(task_id=spec.id, reason=reason)
          wait_msg = f"WAITING_HUMAN: {reason}".strip()
          await broadcast(
            {
              "type": "status",
              "task_id": spec.id,
              "status": "WAITING_HUMAN",
              "output": wait_msg,
              "duration": 0,
            }
          )
          await broadcast(
            {
              "type": "approval_request",
              "task_id": spec.id,
              "title": "Approval required",
              "reason": reason,
            }
          )
          await state.pending_approval.event.wait()
          if state.stop_requested:
            stopped_early = True
            break
          approved = bool(state.pending_approval.approved)
          output = "Approved" if approved else "Rejected"
          duration = 0
          result_store[spec.id] = {"output": output, "duration": duration, "approved": approved}
          results[spec.id] = result_store[spec.id]
          if approved:
            state.completed_tasks += 1
            persist_run()
            await broadcast(
              {
                "type": "status",
                "task_id": spec.id,
                "status": "completed",
                "output": output,
                "duration": duration,
              }
            )
          else:
            await broadcast(
              {
                "type": "status",
                "task_id": spec.id,
                "status": "failed",
                "output": output,
                "duration": duration,
              }
            )
            stopped_early = True
            state.pending_approval = None
            break
          state.pending_approval = None
          continue

        if task_type == "action_approval":
          source_task = getattr(spec, "source_task", None)
          if not source_task:
            await broadcast({"type": "error", "message": f"Action approval task {spec.id} missing source_task"})
            stopped_early = True
            break
          source_result = result_store.get(source_task, {})
          raw_output = source_result.get("output") if isinstance(source_result, dict) else None
          parsed = _extract_json_payload(raw_output or "")
          actions = []
          if isinstance(parsed, dict):
            actions = parsed.get("proposed_actions") or parsed.get("actions") or []
          if not isinstance(actions, list):
            actions = []
          if not actions and isinstance(raw_output, str):
            text = raw_output
            if "FINAL:" in text:
              text = text.split("FINAL:", 1)[1]
            pattern = r"(Delete|Remove|Compress)\\s+`?([^`\\n]+?)`?\\s*\\((\\d+(?:\\.\\d+)?)\\s*(MiB|MB)\\)"
            matches = re.findall(pattern, text, flags=re.IGNORECASE)
            for action_type, path, size, unit in matches:
              size_mb = float(size)
              actions.append(
                {
                  "type": action_type.lower(),
                  "path": path.strip(),
                  "size_mb": size_mb,
                  "reason": "Parsed from recommendations text",
                }
              )

          fields = []
          for idx, action in enumerate(actions):
            if not isinstance(action, dict):
              continue
            path = action.get("path") or action.get("target") or f"action_{idx}"
            action_type = action.get("type") or "action"
            size = action.get("size_mb") or action.get("size") or ""
            reason = action.get("reason") or ""
            label_parts = [str(action_type), str(path)]
            if size:
              label_parts.append(f"({size} MiB)")
            if reason:
              label_parts.append(f"- {reason}")
            fields.append(
              {
                "id": f"action_{idx}",
                "label": " ".join(label_parts),
                "kind": "consent",
                "required": False,
              }
            )

          state.pending_input = PendingInput(task_id=spec.id, spec=spec)
          await broadcast(
            {
              "type": "status",
              "task_id": spec.id,
              "status": "WAITING_HUMAN",
              "output": "WAITING_ACTION_APPROVAL",
              "duration": 0,
            }
          )
          await broadcast(
            {
              "type": "input_request",
              "task_id": spec.id,
              "title": "Approve cleanup actions",
              "description": "Select the actions you want to approve.",
              "ui": {"fields": fields},
            }
          )
          await state.pending_input.event.wait()
          if state.stop_requested:
            stopped_early = True
            break
          payload = state.pending_input.response or {}
          approvals = []
          for idx, action in enumerate(actions):
            if payload.get(f"action_{idx}"):
              approvals.append(action)
          output = json.dumps({"approved_actions": approvals}, indent=2)
          duration = 0
          result_store[spec.id] = {"output": output, "duration": duration, "approved_actions": approvals}
          results[spec.id] = result_store[spec.id]
          state.completed_tasks += 1
          persist_run()
          await broadcast(
            {
              "type": "status",
              "task_id": spec.id,
              "status": "completed",
              "output": output,
              "duration": duration,
            }
          )
          state.pending_input = None
          continue

        if task_type == "tool_run":
          tool_name = getattr(spec, "tool", None)
          if not tool_name:
            await broadcast({"type": "error", "message": f"Tool run task {spec.id} missing tool name"})
            stopped_early = True
            break
          try:
            tool = tool_registry.get(tool_name)
          except Exception as exc:
            await broadcast({"type": "error", "message": f"Tool {tool_name} not found: {exc}"})
            stopped_early = True
            break
          await broadcast({"type": "status", "task_id": spec.id, "status": "thinking"})
          t0 = time.perf_counter()
          resolved_input = resolve_input(getattr(spec, "input", None))
          if isinstance(resolved_input, (dict, list)):
            input_text = json.dumps(resolved_input)
          elif resolved_input is None:
            input_text = ""
          else:
            input_text = str(resolved_input)

          if tool_name == "disk_usage_triage":
            payload = _load_structured(input_text) or {}
            if not isinstance(payload, dict):
              payload = {}
            path_value = payload.get("path") or "/"
            timeout = int(payload.get("timeout", 40))
            min_mb = int(payload.get("min_mb", 100))
            top_n = int(payload.get("top_n", 5))
            top_files = int(payload.get("top_files", 10))

            output_sections: List[str] = []
            df_cmd = ["df", "-P", "-k", str(path_value)]
            await broadcast({"type": "console", "message": f"Running: {' '.join(df_cmd)}"})
            df_result = await asyncio.to_thread(_run_command, df_cmd, timeout=timeout)
            output_sections.append(_summarize("df -P -k", df_result))
            await broadcast({"type": "console", "message": _summarize("df -P -k", df_result, limit=400)})

            percent_used: Optional[int] = None
            stdout_lines = df_result.get("stdout", "").splitlines()
            if len(stdout_lines) >= 2:
              for line in stdout_lines[1:]:
                columns = [col for col in line.split() if col]
                percent_column = next((col for col in columns if col.endswith("%")), None)
                if percent_column:
                  try:
                    percent_used = int(percent_column.strip("%"))
                  except ValueError:
                    percent_used = None
                if percent_used is not None:
                  break

            status = "unknown"
            if percent_used is not None:
              if percent_used >= 85:
                status = "critical"
              elif percent_used >= 70:
                status = "warning"
              else:
                status = "ok"

            if status in {"warning", "critical"}:
              du_cmd = ["du", "-x", "-k", "-d", "1", str(path_value)]
              await broadcast({"type": "console", "message": f"Running: {' '.join(du_cmd)}"})
              du_result = await asyncio.to_thread(_run_command, du_cmd, timeout=timeout)
              output_sections.append(_summarize("du -x -k -d 1", du_result))
              await broadcast({"type": "console", "message": _summarize("du -x -k -d 1", du_result, limit=400)})
              largest: List[tuple[int, str]] = []
              for line in du_result.get("stdout", "").splitlines():
                parts = line.split(None, 1)
                if len(parts) != 2:
                  continue
                try:
                  size_kb = int(parts[0])
                except ValueError:
                  continue
                path = parts[1]
                if path == str(path_value):
                  continue
                largest.append((size_kb, path))
              largest.sort(key=lambda item: item[0], reverse=True)
              if largest:
                report_lines = []
                for size_kb, path in largest[:top_n]:
                  size_mb = size_kb / 1024
                  report_lines.append(f"- {path}: {size_mb:.1f} MiB")
                output_sections.append("Top directories by size:\n" + "\n".join(report_lines))
            else:
              output_sections.append("Disk usage within acceptable thresholds; no cleanup required.")

            find_cmd = [
              "find",
              str(path_value),
              "-xdev",
              "-type",
              "f",
              "-size",
              f"+{min_mb}M",
              "-printf",
              "%s %p\\n",
            ]
            await broadcast({"type": "console", "message": f"Running: {' '.join(find_cmd)}"})
            find_result = await asyncio.to_thread(_run_command, find_cmd, timeout=timeout)
            output_sections.append(_summarize(f"find files > {min_mb}M", find_result))
            await broadcast({"type": "console", "message": _summarize(f"find files > {min_mb}M", find_result, limit=400)})

            large_files: List[tuple[int, str]] = []
            if find_result.get("code") == 0:
              for line in find_result.get("stdout", "").splitlines():
                parts = line.split(" ", 1)
                if len(parts) != 2:
                  continue
                try:
                  size_bytes = int(parts[0])
                except ValueError:
                  continue
                large_files.append((size_bytes, parts[1]))
            else:
              output_sections.append(
                f"find -printf unavailable; falling back to Python scan for files > {min_mb} MiB."
              )
              try:
                root_dev = Path(path_value).stat().st_dev
                for root, _, files in os.walk(path_value):
                  try:
                    if Path(root).stat().st_dev != root_dev:
                      continue
                  except OSError:
                    continue
                  for fname in files:
                    fpath = Path(root) / fname
                    try:
                      stat = fpath.stat()
                    except OSError:
                      continue
                    if stat.st_dev != root_dev:
                      continue
                    if stat.st_size >= min_mb * 1024 * 1024:
                      large_files.append((stat.st_size, str(fpath)))
              except Exception:
                large_files = []
            large_files.sort(key=lambda item: item[0], reverse=True)
            if large_files:
              lines = []
              for size_bytes, path in large_files[:top_files]:
                size_mb = size_bytes / (1024 * 1024)
                lines.append(f"- {path}: {size_mb:.1f} MiB")
              output_sections.append(f"Largest files (> {min_mb} MiB):\n" + "\n".join(lines))
            else:
              output_sections.append(f"No files larger than {min_mb} MiB found under {path_value}.")

            output = "\n\n".join(output_sections)
          else:
            def run_tool():
              result = tool.run(
                input_text=input_text,
                context=ToolContext(
                  agent_name=getattr(spec, "agent", "tool"),
                  task_id=spec.id,
                  iteration=0,
                  metadata={"tool": tool_name, "host": "local"},
                ),
              )
              return result.content
            output = await asyncio.to_thread(run_tool)
          t1 = time.perf_counter()
          duration = t1 - t0
          result_store[spec.id] = {"output": output, "duration": duration}
          results[spec.id] = result_store[spec.id]
          state.completed_tasks += 1
          persist_run()
          await broadcast(
            {
              "type": "status",
              "task_id": spec.id,
              "status": "completed",
              "output": output,
              "duration": duration,
            }
          )
          continue

        await broadcast({"type": "status", "task_id": spec.id, "status": "thinking"})
        t0 = time.perf_counter()
        if getattr(spec, "input", None) is not None:
          spec.input = resolve_input(spec.input)
        output, captured = await asyncio.to_thread(_run_with_capture, run_single, spec)
        if captured and engine != "autogen":
          await broadcast({"type": "console", "message": captured})
        t1 = time.perf_counter()
        duration = t1 - t0
        result_store[spec.id] = {"output": output, "duration": duration}
        results[spec.id] = result_store[spec.id]
        state.completed_tasks += 1
        persist_run()
        await broadcast(
          {
            "type": "status",
            "task_id": spec.id,
            "status": "completed",
            "output": output,
            "duration": duration,
          }
        )
    except asyncio.CancelledError:
      stopped_early = True
    except Exception as exc:  # safety: still surface errors
      stopped_early = True
      await broadcast({"type": "error", "message": f"Run failed: {exc}"})

    # If any task output contains a FINAL: summary, also broadcast it as a console message
    for task_id, obj in results.items():
        raw = obj["output"] if isinstance(obj, dict) and "output" in obj else obj
        try:
            if raw and isinstance(raw, str) and "FINAL:" in raw:
                await broadcast({"type": "console", "message": raw, "task_id": task_id})
        except Exception:
            pass

    run_end = time.perf_counter()
    overall = run_end - run_start

    await broadcast({"type": "complete", "results": results, "duration": overall, "stopped": stopped_early or state.stop_requested})
    state.completed = True
    persist_run()


@app.websocket("/ws/{run_id}")
async def websocket_endpoint(websocket: WebSocket, run_id: str) -> None:
    if run_id not in RUNS:
        await websocket.close(code=1008)
        return
    state = RUNS[run_id]
    queue: asyncio.Queue = asyncio.Queue()
    state.subscribers.append(queue)
    await websocket.accept()
    closed = False
    try:
        for event in state.history:
            await websocket.send_text(json.dumps(event))
        completed = state.completed
        while not completed:
            event = await queue.get()
            await websocket.send_text(json.dumps(event))
            if event.get("type") == "complete":
                completed = True
    except WebSocketDisconnect:
        closed = True
    finally:
        if queue in state.subscribers:
            state.subscribers.remove(queue)
        if not closed and websocket.client_state != WebSocketState.DISCONNECTED:
            try:
                await websocket.close()
            except RuntimeError:
                pass


@app.get("/api/runs")
async def list_runs() -> Dict[str, Any]:
    summary = []
    for run_id, state in RUNS.items():
        progress = 0
        if state.total_tasks:
            progress = int((state.completed_tasks / state.total_tasks) * 100)
        summary.append(
            {
                "run_id": run_id,
                "project": state.config.name,
                "engine": state.engine,
                "completed": state.completed,
                "progress": progress,
                "tasks_total": state.total_tasks,
                "tasks_completed": state.completed_tasks,
                "started_at": state.started_at,
                "config_path": state.config_path,
                "request_path": state.requested_path or state.config_path,
            }
        )
    return {"runs": summary}
