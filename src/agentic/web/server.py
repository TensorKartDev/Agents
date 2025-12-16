"""FastAPI web server that visualizes agent runs with live status updates."""

from __future__ import annotations

import asyncio
import json
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List

from fastapi import FastAPI, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.responses import HTMLResponse
from pydantic import BaseModel

from ..agents.orchestrator import Orchestrator
from ..autogen_runner import AutogenOrchestrator
from ..config import ProjectConfig


app = FastAPI(title="Agentic Web Runner")


@dataclass
class RunState:
    config: ProjectConfig
    engine: str
    history: List[Dict[str, Any]] = field(default_factory=list)
    subscribers: List[asyncio.Queue] = field(default_factory=list)
    task: asyncio.Task | None = None
    completed: bool = False


RUNS: Dict[str, RunState] = {}


class RunRequest(BaseModel):
    config_path: str
    engine: str = "autogen"


HTML_PAGE = """<!doctype html>
<html lang="en">
  <head>
    <meta charset="utf-8" />
    <title>Agentic Runner</title>
    <link href="https://cdn.jsdelivr.net/npm/bootstrap@5.3.3/dist/css/bootstrap.min.css" rel="stylesheet" />
    <style>
      body { background: radial-gradient(circle at top, #0f172a 0%, #020617 55%, #01030a 100%); color: #e2e8f0; min-height: 100vh; }
      .glass-card { background: rgba(15,23,42,0.85); border-radius: 20px; border: 1px solid rgba(94,234,212,0.3); box-shadow: 0 25px 50px rgba(15,23,42,0.8); }
      .neon { color: #67e8f9; letter-spacing: 0.2rem; }
      .status-text { font-weight: 700; text-transform: uppercase; }
      .status-pending { color: #fbbf24; }
      .status-thinking { color: #38bdf8; animation: pulse 1.5s infinite; }
      .status-completed { color: #34d399; }
      .console-log { background: #020617; color: #67e8f9; font-family: "Roboto Mono", monospace; padding: 1rem; border-radius: 12px; height: 220px; overflow-y: auto; }
      .progress-bar { transition: width 0.6s ease-in-out; }
      @keyframes pulse { 0% {opacity: 0.4;} 50% {opacity: 1;} 100% {opacity: 0.4;} }
    </style>
  </head>
  <body class="py-4">
    <div class="container">
      <div class="glass-card p-4 mb-4">
        <div class="d-flex justify-content-between align-items-center">
          <div>
            <h1 class="display-6 fw-bold neon">AGENTIC CONTROL</h1>
            <p class="text-secondary mb-0">Autonomous mission orchestrator</p>
          </div>
          <div class="text-end">
            <span class="badge bg-info text-dark">LIVE</span>
          </div>
        </div>
        <hr class="border-primary opacity-25" />
        <form id="run-form" class="row g-3">
          <div class="col-md-8">
            <label for="config" class="form-label text-uppercase small">Config path</label>
            <input id="config" class="form-control form-control-lg" value="examples/configs/hardware_pen_test.yaml" />
          </div>
          <div class="col-md-3">
            <label for="engine" class="form-label text-uppercase small">Engine</label>
            <select id="engine" class="form-select form-select-lg">
              <option value="autogen" selected>Microsoft Autogen</option>
              <option value="legacy">Legacy JSON loop</option>
            </select>
          </div>
          <div class="col-md-1 d-flex align-items-end">
            <button type="submit" class="btn btn-lg btn-info w-100 fw-bold">Deploy</button>
          </div>
        </form>
      </div>

      <div class="row">
        <div class="col-lg-7 mb-4">
          <div class="glass-card p-4 h-100" id="plan-container" style="display:none;">
            <div class="d-flex justify-content-between align-items-center mb-3">
              <div>
                <h2 id="project-title" class="h4 mb-0 text-info"></h2>
                <small class="text-secondary" id="engine-label"></small>
              </div>
              <div class="text-end">
                <span class="text-uppercase text-secondary small">Progress</span>
                <div class="progress" style="width: 220px; height: 8px;">
                  <div id="global-progress" class="progress-bar bg-info" style="width: 0%"></div>
                </div>
              </div>
            </div>
            <div id="tasks-list" class="vstack gap-3"></div>
          </div>
        </div>
        <div class="col-lg-5 mb-4">
          <div class="glass-card p-4 h-100">
            <h3 class="h5 text-info">Mission Console</h3>
            <div class="console-log" id="log"></div>
          </div>
        </div>
      </div>

      <div class="glass-card p-4 mb-4" id="outputs" style="display:none;">
        <h3 class="h5 text-info mb-3">Results</h3>
        <div id="output-list" class="row gy-3"></div>
      </div>
    </div>

    <script src="https://cdn.jsdelivr.net/npm/bootstrap@5.3.3/dist/js/bootstrap.bundle.min.js"></script>
    <script>
      let statusRows = {};
      let ws;
      let logEl = null;
      let totalTasks = 0;
      let completedTasks = 0;

      document.getElementById('run-form').addEventListener('submit', async (event) => {
        event.preventDefault();
        resetUI();
        const configPath = document.getElementById('config').value;
        const engine = document.getElementById('engine').value;
        const res = await fetch('/api/run', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ config_path: configPath, engine })
        });
        if (!res.ok) {
          alert('Failed to start run: ' + (await res.text()));
          return;
        }
        const data = await res.json();
        connectWebSocket(data.run_id);
      });

      function resetUI() {
        document.getElementById('plan-container').style.display = 'none';
        document.getElementById('tasks-list').innerHTML = '';
        document.getElementById('outputs').style.display = 'none';
        document.getElementById('output-list').innerHTML = '';
        document.getElementById('global-progress').style.width = '0%';
        document.getElementById('engine-label').innerText = '';
        document.getElementById('log').innerHTML = '';
        statusRows = {};
        totalTasks = 0;
        completedTasks = 0;
        logEl = document.getElementById('log');
        if (ws) { ws.close(); }
      }

      function connectWebSocket(runId) {
        const protocol = window.location.protocol === 'https:' ? 'wss' : 'ws';
        ws = new WebSocket(`${protocol}://${window.location.host}/ws/${runId}`);
        ws.onmessage = (event) => {
          const payload = JSON.parse(event.data);
          handleEvent(payload);
        };
        ws.onclose = () => console.log('WebSocket closed');
      }

      function handleEvent(event) {
        if (event.type === 'plan') {
          renderPlan(event);
          log(`PLAN: Loaded ${event.tasks.length} tasks for ${event.project}`);
        } else if (event.type === 'status') {
          updateStatus(event);
          log(`STATUS: ${event.task_id} -> ${event.status}`);
        } else if (event.type === 'complete') {
          renderOutputs(event.results);
          log('RUN COMPLETE: outputs ready');
        } else if (event.type === 'error') {
          alert(event.message);
          log('ERROR: ' + event.message);
        }
      }

      function renderPlan(event) {
        document.getElementById('project-title').innerText = event.project;
        document.getElementById('engine-label').innerText = `Engine: ${event.engine}`;
        document.getElementById('plan-container').style.display = 'block';
        const list = document.getElementById('tasks-list');
        totalTasks = event.tasks.length;
        event.tasks.forEach(task => {
          const card = document.createElement('div');
          card.className = 'p-3 border border-info rounded-4 bg-dark bg-opacity-50';
          card.innerHTML = `
            <div class="d-flex justify-content-between">
              <div>
                <div class="fw-bold text-uppercase text-secondary small">${task.id}</div>
                <div class="text-light">${task.description}</div>
                <div class="text-info small">Agent: ${task.agent}</div>
              </div>
              <div class="text-end status-text status-pending" id="status-${task.id}">pending</div>
            </div>
            <div class="progress mt-3" style="height: 6px;">
              <div class="progress-bar bg-info" id="progress-${task.id}" style="width: 0%"></div>
            </div>
          `;
          list.appendChild(card);
          statusRows[task.id] = {
            label: card.querySelector(`#status-${task.id}`),
            bar: card.querySelector(`#progress-${task.id}`),
          };
        });
      }

      function updateStatus(event) {
        const row = statusRows[event.task_id];
        if (!row) return;
        row.label.classList.remove('status-pending', 'status-thinking', 'status-completed');
        let width = '0%';
        if (event.status === 'pending') {
          row.label.classList.add('status-pending');
          width = '0%';
        } else if (event.status === 'thinking') {
          row.label.classList.add('status-thinking');
          width = '60%';
        } else if (event.status === 'completed') {
          row.label.classList.add('status-completed');
          width = '100%';
          completedTasks += 1;
          const overall = Math.round((completedTasks / totalTasks) * 100);
          document.getElementById('global-progress').style.width = overall + '%';
        }
        row.label.innerText = event.status;
        row.bar.style.width = width;
      }

      function renderOutputs(results) {
        const container = document.getElementById('output-list');
        document.getElementById('outputs').style.display = 'block';
        Object.entries(results).forEach(([taskId, output]) => {
          const wrapper = document.createElement('div');
          wrapper.className = 'col-md-6';
          wrapper.innerHTML = `<div class="border border-info rounded-3 p-3 bg-dark bg-opacity-50">
            <h4 class="text-info">${taskId}</h4>
            <pre class="text-light mb-0">${output}</pre>
          </div>`;
          container.appendChild(wrapper);
        });
      }

      function log(message) {
        if (!logEl) return;
        const time = new Date().toLocaleTimeString();
        const entry = document.createElement('div');
        entry.textContent = `[${time}] ${message}`;
        logEl.appendChild(entry);
        logEl.scrollTop = logEl.scrollHeight;
      }
    </script>
  </body>
</html>
"""


@app.get("/", response_class=HTMLResponse)
async def root() -> HTMLResponse:
    return HTMLResponse(HTML_PAGE)


@app.post("/api/run")
async def start_run(request: RunRequest) -> Dict[str, Any]:
    config_path = Path(request.config_path)
    if not config_path.exists():
        raise HTTPException(status_code=400, detail=f"Config not found: {config_path}")
    config = ProjectConfig.from_file(str(config_path))
    run_id = str(uuid.uuid4())
    state = RunState(config=config, engine=request.engine)
    RUNS[run_id] = state
    state.task = asyncio.create_task(execute_run(run_id, config, request.engine))
    return {"run_id": run_id, "project": config.name}


async def execute_run(run_id: str, config: ProjectConfig, engine: str) -> None:
    state = RUNS[run_id]

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
    results: Dict[str, str] = {}

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

    for spec in task_specs:
        await broadcast({"type": "status", "task_id": spec.id, "status": "thinking"})
        output = await asyncio.to_thread(run_single, spec)
        results[spec.id] = output
        await broadcast(
            {"type": "status", "task_id": spec.id, "status": "completed", "output": output}
        )

    await broadcast({"type": "complete", "results": results})
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
        while True:
            event = await queue.get()
            await websocket.send_text(json.dumps(event))
            if event.get("type") == "complete":
                break
    except WebSocketDisconnect:
        pass
    finally:
        if queue in state.subscribers:
            state.subscribers.remove(queue)
        if state.completed and not state.subscribers:
            RUNS.pop(run_id, None)
        await websocket.close()
