"""FastAPI web server that visualizes agent runs with live status updates."""

from __future__ import annotations

import asyncio
import json
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

import yaml
from fastapi import FastAPI, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from ..agents.orchestrator import Orchestrator
from ..autogen_runner import AutogenOrchestrator
from ..config import ProjectConfig


import yaml
from fastapi.responses import JSONResponse


app = FastAPI(title="Agentic Web Runner")
app.mount("/static", StaticFiles(directory=Path(__file__).parent), name="static")

BASE_DIR = Path(__file__).resolve().parents[2]  # repo root
AGENTS_DIR = BASE_DIR / "agents"
STATIC_IMG = Path(__file__).parent / "img"
INDEX_HTML = Path(__file__).parent / "index.html"

if AGENTS_DIR.exists():
    # Mount so icons and assets inside agent folders can be served statically.
    app.mount("/agents", StaticFiles(directory=AGENTS_DIR), name="agents")

# Mount default static images (e.g., robot.svg) if present
if STATIC_IMG.exists():
    app.mount("/static/img", StaticFiles(directory=STATIC_IMG), name="static-img")


@dataclass
class AgentInfo:
    id: str
    name: str
    description: str
    icon: str
    config_path: str


def _load_manifest(path: Path) -> Optional[Dict[str, Any]]:
    try:
        with path.open() as f:
            data = yaml.safe_load(f)
            if not isinstance(data, dict):
                return None
            return data
    except Exception:
        return None


def scan_for_agents() -> List[AgentInfo]:
    """Scan the 'agents' directory for agent packages."""
    agents: List[AgentInfo] = []
    if not AGENTS_DIR.exists():
        return agents

    for agent_dir in AGENTS_DIR.iterdir():
        if not agent_dir.is_dir():
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
            )
        )

    return agents


@app.get("/api/agents")
async def list_agents() -> JSONResponse:
    """Return a list of discoverable agents."""
    agents = scan_for_agents()
    return JSONResponse([agent.__dict__ for agent in agents])


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


RUNS: Dict[str, RunState] = {}


class RunRequest(BaseModel):
    config_path: str
    engine: str = "autogen"


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
  state = RunState(
      config=config,
      engine=request.engine,
      config_path=resolved_path,
      requested_path=request.config_path,
      total_tasks=len(config.tasks),
      completed_tasks=0,
  )
  RUNS[run_id] = state
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
    return {"run_id": run_id, "stopped": True}


async def execute_run(run_id: str, config: ProjectConfig, engine: str) -> None:
    state = RUNS[run_id]
    state.total_tasks = len(config.tasks)
    state.completed_tasks = 0
    state.stop_requested = False

    async def broadcast(event: Dict[str, Any]) -> None:
        state.history.append(event)
        for queue in list(state.subscribers):
            await queue.put(event)

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

    task_specs = list(config.tasks)
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

    for spec in task_specs:
      await broadcast({"type": "status", "task_id": spec.id, "status": "pending"})

    try:
      for spec in task_specs:
        if state.stop_requested:
          stopped_early = True
          break
        await broadcast({"type": "status", "task_id": spec.id, "status": "thinking"})
        t0 = time.perf_counter()
        output = await asyncio.to_thread(run_single, spec)
        t1 = time.perf_counter()
        duration = t1 - t0
        results[spec.id] = {"output": output, "duration": duration}
        state.completed_tasks += 1
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


@app.websocket("/ws/{run_id}")
async def websocket_endpoint(websocket: WebSocket, run_id: str) -> None:
    if run_id not in RUNS:
        await websocket.close(code=1008)
        return
    state = RUNS[run_id]
    queue: asyncio.Queue = asyncio.Queue()
    state.subscribers.append(queue)
    await websocket.accept()
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
        pass
    finally:
        if queue in state.subscribers:
            state.subscribers.remove(queue)
        await websocket.close()


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
