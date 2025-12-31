"""FastAPI web server that visualizes agent runs with live status updates."""

from __future__ import annotations

import asyncio
import json
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List

from fastapi import FastAPI, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.staticfiles import StaticFiles
from fastapi.responses import HTMLResponse
from pydantic import BaseModel

from ..agents.orchestrator import Orchestrator
from ..autogen_runner import AutogenOrchestrator
from ..config import ProjectConfig


app = FastAPI(title="Agentic Web Runner")
app.mount("/static", StaticFiles(directory=Path(__file__).parent), name="static")

# Prefer a locally downloaded bootstrap bundle if present, otherwise fall back to CDN
_local_bootstrap_js = Path(__file__).parent / "bootstrap.bundle.min.js"
if _local_bootstrap_js.exists():
  BOOTSTRAP_JS_SRC = "/static/bootstrap.bundle.min.js"
else:
  BOOTSTRAP_JS_SRC = "https://cdn.jsdelivr.net/npm/bootstrap@5.3.3/dist/js/bootstrap.bundle.min.js"


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


HTML_PAGE = r"""<!doctype html>
<html lang="en">
  <head>
    <meta charset="utf-8" />
    <title>Agentic Runner</title>
    <link href="/static/bootstrap.css" rel="stylesheet" />
    <style>
    body { background: linear-gradient(180deg, #f8fafc 0%, #e2e8f0 100%); color: #0f172a; min-height: 100vh; }
      .glass-card { background: rgba(255,255,255,0.9); border-radius: 20px; border: 1px solid rgba(59,130,246,0.2); box-shadow: 0 25px 40px rgba(15,23,42,0.1); }
      .neon { color: #2563eb; letter-spacing: 0.2rem; }
      .status-text { font-weight: 700; text-transform: uppercase; }
      .status-pending { color: #f97316; }
      .status-thinking { color: #0ea5e9; animation: pulse 1.5s infinite; }
      .status-completed { color: #10b981; }
      .console-log { background: #e2e8f0; color: #0f172a; font-family: "Roboto Mono", monospace; padding: 1rem; border-radius: 12px; flex: 1 1 auto; min-height: 0; overflow-y: auto; }
      .progress-bar { transition: width 0.6s ease-in-out; }
    /* Output cards */
    .output-card { border: 1px solid rgba(59,130,246,0.12); border-radius: 12px; padding: 1rem; background: rgba(255,255,255,0.85); }
    .output-header { display:flex; justify-content:space-between; align-items:center; gap:0.5rem; }
    .output-meta { font-size: .9rem; color: #0f172a; opacity:0.8; }
    .output-actions button { margin-left:0.4rem; }
    .output-pre { background: rgba(15,23,42,0.03); padding:.75rem; border-radius:8px; max-height:220px; overflow:auto; white-space:pre-wrap; font-family: "Roboto Mono", monospace; }
    /* Mission console styles */
    #mission-control { display: flex; flex-direction: column; min-height: calc(100vh - 200px); max-height: calc(100vh - 120px); }
    .log-entry { display:flex; gap:0.75rem; align-items:flex-start; padding:0.5rem; border-radius:8px; background: rgba(2,6,23,0.02); }
    .log-entry .badge { flex: 0 0 auto; }
    .log-content { white-space: pre-wrap; font-family: system-ui, -apple-system, 'Segoe UI', Roboto, 'Helvetica Neue', Arial; color: #0f172a; }
    .log-meta { font-size: .8rem; color:#6b7280; }
      @keyframes pulse { 0% {opacity: 0.4;} 50% {opacity: 1;} 100% {opacity: 0.4;} }
    /* Syntax highlight styles for JSON */
    .hl-key { color: #7c3aed; }
    .hl-string { color: #065f46; }
    .hl-number { color: #944; }
    .hl-boolean { color: #b45309; }
    .hl-null { color: #6b7280; font-style: italic; }
    /* Config cards styles */
    .config-card { cursor: pointer; transition: all 0.3s ease; border: 2px solid rgba(59,130,246,0.2); border-radius: 12px; padding: 1.5rem; background: rgba(255,255,255,0.7); text-align: center; display: flex; flex-direction: column; align-items: center; gap: 0.75rem; }
    .config-card:hover { border-color: rgba(59,130,246,0.5); background: rgba(255,255,255,0.9); transform: translateY(-4px); box-shadow: 0 12px 24px rgba(59,130,246,0.2); }
    .config-card.active { border-color: #2563eb; background: rgba(59,130,246,0.15); box-shadow: 0 0 0 3px rgba(59,130,246,0.3); animation: pulse-active 0.6s ease-in-out; }
    .config-card-icon { font-size: 2.5rem; line-height: 1; }
    .config-card-title { font-weight: 700; color: #0f172a; margin: 0; font-size: 1rem; text-transform: uppercase; letter-spacing: 0.05em; }
    .config-card-desc { font-size: 0.8rem; color: #6b7280; margin: 0.25rem 0 0 0; }
    .config-card-agent-badge { display: inline-block; background: rgba(37, 99, 235, 0.1); color: #2563eb; padding: 0.25rem 0.75rem; border-radius: 20px; font-size: 0.7rem; font-weight: 600; text-transform: uppercase; letter-spacing: 0.05em; border: 1px solid rgba(37, 99, 235, 0.3); margin-top: 0.5rem; }
    @keyframes pulse-active { 0%, 100% { box-shadow: 0 0 0 3px rgba(59,130,246,0.3); } 50% { box-shadow: 0 0 0 6px rgba(59,130,246,0.15); } }
    </style>
  </head>
  <body class="py-4">
    <div class="container">
      <div class="glass-card p-4 mb-4">
        <div class="d-flex justify-content-between align-items-center">
          <div>
            <h1 class="display-6 fw-bold neon">AGENTIC CONTROL PANEL</h1>
            <p class="text-secondary mb-0">Autonomous mission orchestrator — Powered by MAF &amp; llama3.2</p>
          </div>
          <div class="text-end">
            <span class="badge bg-info text-dark">LIVE</span>
          </div>
        </div>
        <hr class="border-primary opacity-25" />
        <div class="mb-3">
          <label class="form-label text-uppercase small">Select Agent Workflow</label>
          <div class="row g-2" id="config-cards-container">
            <!-- Config cards will be inserted here by JS -->
          </div>
        </div>
        <div class="mb-0">
          <label for="engine" class="form-label text-uppercase small">Engine</label>
          <select id="engine" class="form-select form-select-lg">
            <option value="autogen" selected>Microsoft Agent Framework (MAF)</option>
            <option value="legacy">Legacy JSON loop</option>
          </select>
        </div>
      </div>

      <div class="row">
        <div class="col-lg-7 mb-4">
          <div class="glass-card p-4 h-100" id="mission-control">
            <div class="d-flex justify-content-between align-items-center mb-3">
              <div>
                <h3 class="h5 text-info mb-0">Mission Console</h3>
                <small class="text-secondary" id="mission-sub">Live updates and sanitized logs</small>
              </div>
              <div class="text-end">
                <span id="run-summary" class="text-secondary small">Ready</span>
              </div>
            </div>
            <div class="console-log" id="log"></div>
          </div>
        </div>
        <div class="col-lg-5 mb-4">
          <div class="glass-card p-4 mb-3" id="plan-container" style="display:none;">
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
      </div>

      <div class="row">
        <div class="col-12 mb-4">
          <div class="glass-card p-4" id="outputs" style="display:none;">
            <h3 class="h5 text-info mb-3">Results</h3>
            <div id="output-list" class="row gy-3"></div>
          </div>
        </div>
      </div>
    </div>

    <script src="{BOOTSTRAP_JS_SRC}"></script>
    <script>
      // Small helper to escape HTML for safe innerHTML usage
      function _escapeHtml(str) {
        return str.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');
      }

      // Lightweight JSON syntax highlighter. Accepts a pretty-printed JSON string.
      function highlightJSON(jsonStr) {
        const esc = _escapeHtml(jsonStr);
        // keys (simpler pattern that avoids \u escapes)
        let out = esc.replace(/("([^"\\]|\\.)*")(?=\s*:)/g, '<span class="hl-key">$1</span>');
        // strings
        out = out.replace(/(:\s*)("([^"\\]|\\.)*")/g, '$1<span class="hl-string">$2</span>');
        // numbers
        out = out.replace(/(:\s*)(-?\d+(?:\.\d+)?(?:[eE][+\-]?\d+)?)/g, '$1<span class="hl-number">$2</span>');
        // booleans
        out = out.replace(/(:\s*)(\btrue\b|\bfalse\b)/g, '$1<span class="hl-boolean">$2</span>');
        // null
        out = out.replace(/(:\s*)(\bnull\b)/g, '$1<span class="hl-null">$2</span>');
        return out;
      }
    </script>
    <script>
      // Global JS error handlers and helpful logs
      window.addEventListener('error', function (e) {
        try { console.error('Uncaught error', e); } catch (er) {}
        try { const logEl = document.getElementById('log'); if (logEl) { const div = document.createElement('div'); div.textContent = '[JS ERROR] ' + (e && e.message ? e.message : String(e)); logEl.appendChild(div); } } catch (er) {}
      });
      window.addEventListener('unhandledrejection', function (e) {
        try { console.error('Unhandled rejection', e); } catch (er) {}
        try { const logEl = document.getElementById('log'); if (logEl) { const div = document.createElement('div'); div.textContent = '[Promise Rejection] ' + (e && e.reason ? (e.reason.message || String(e.reason)) : String(e)); logEl.appendChild(div); } } catch (er) {}
      });
      document.addEventListener('DOMContentLoaded', function () {
        console.log('DOM ready');
      });
    </script>
    <script>
      let statusRows = {};
      let ws;
      let logEl = null;
      let totalTasks = 0;
      let completedTasks = 0;
      let selectedConfig = null;

      // Config presets
      const configPresets = [
        { name: 'Edge Inference Agent', path: 'examples/configs/edge_inference.yaml', desc: 'Edge inference workflow', icon: '🤖', keyword: 'ML Agent' },
        { name: 'Firmware Penetration Testing Agent', path: 'examples/configs/firmware_workflow.yaml', desc: 'Firmware security testing', icon: '⚙️', keyword: 'Security Agent' },
        { name: 'Hardware Penetration Testing Agent', path: 'examples/configs/hardware_pen_test.yaml', desc: 'Hardware security testing', icon: '🔍', keyword: 'Security Agent' },
        { name: 'Sales Order Investigation Agent', path: 'examples/configs/sales_order_investigation.yaml', desc: 'Sales order analysis', icon: '📊', keyword: 'Business Agent' }
      ];

      // Render config cards on page load
      document.addEventListener('DOMContentLoaded', function () {
        console.log('DOM ready');
        renderConfigCards();
      });

      function renderConfigCards() {
        const container = document.getElementById('config-cards-container');
        if (!container) return;
        configPresets.forEach(cfg => {
          const col = document.createElement('div');
          col.className = 'col-md-6 col-lg-3';
          
          const card = document.createElement('div');
          card.className = 'config-card';
          card.dataset.config = cfg.path;
          card.onclick = () => selectAndDeploy(cfg);
          
          const icon = document.createElement('div');
          icon.className = 'config-card-icon';
          icon.textContent = cfg.icon;
          
          const title = document.createElement('div');
          title.className = 'config-card-title';
          title.textContent = cfg.name;
          
          const desc = document.createElement('div');
          desc.className = 'config-card-desc';
          desc.textContent = cfg.desc;
          
          const badge = document.createElement('div');
          badge.className = 'config-card-agent-badge';
          badge.textContent = cfg.keyword;
          
          card.appendChild(icon);
          card.appendChild(title);
          card.appendChild(desc);
          card.appendChild(badge);
          col.appendChild(card);
          container.appendChild(col);
        });
      }

      function selectAndDeploy(cfg) {
        selectedConfig = cfg.path;
        // Update active card styling
        document.querySelectorAll('.config-card').forEach(c => c.classList.remove('active'));
        event.currentTarget.classList.add('active');
        // Auto-deploy
        deployRun();
      }

      function deployRun() {
        if (!selectedConfig) {
          alert('Please select a config first');
          return;
        }
        resetUI();
        const engine = document.getElementById('engine').value;
        console.log('Deploying', { configPath: selectedConfig, engine });
        try {
          fetch('/api/run', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ config_path: selectedConfig, engine })
          }).then(res => {
            console.log('Server response status:', res.status);
            if (!res.ok) {
              return res.text().then(text => {
                alert('Failed to start run: ' + text);
              });
            }
            return res.json().then(data => {
              connectWebSocket(data.run_id);
            });
          }).catch(err => {
            console.error('Error starting run', err);
            alert('Error starting run: ' + (err && err.message ? err.message : String(err)));
          });
        } catch (err) {
          console.error('Error starting run', err);
          alert('Error starting run: ' + (err && err.message ? err.message : String(err)));
        }
      }

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
          appendLog('plan', `Loaded ${event.tasks.length} tasks for ${event.project}`);
        } else if (event.type === 'status') {
          updateStatus(event);
          appendLog('status', `${event.task_id} -> ${event.status}`);
          // If a status event includes an output with FINAL:, also log it (sanitized)
          try {
            if (event.output && typeof event.output === 'string' && event.output.includes('FINAL:')) {
              appendLog('final', event.output);
            }
          } catch (e) {
            // ignore
          }
        } else if (event.type === 'complete') {
          renderOutputs(event.results);
          // show overall duration in header if provided
          if (event.duration !== undefined) {
            const runSummary = document.getElementById('run-summary');
            if (runSummary) runSummary.innerText = `Duration: ${Number(event.duration).toFixed(2)}s`;
          }
          appendLog('complete', 'Run complete — results available');
        } else if (event.type === 'console') {
          // server-side console messages (e.g. FINAL: summaries)
          if (event.message) appendLog('console', event.message);
        } else if (event.type === 'error') {
          alert(event.message);
          appendLog('error', event.message);
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
          card.className = 'p-3 border border-info rounded-4 bg-white bg-opacity-75';
          card.innerHTML = `
            <div class="d-flex justify-content-between">
              <div>
                <div class="fw-bold text-uppercase text-secondary small">${task.id}</div>
                <div class="text-dark">${task.description}</div>
                <div class="text-info small">Agent: ${task.agent}</div>
                <div class="text-secondary small mt-1" id="duration-${task.id}">Duration: —</div>
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
          // Update per-task duration if provided
          try {
            if (event.duration !== undefined) {
              const durEl = document.getElementById(`duration-${event.task_id}`);
              if (durEl) durEl.textContent = `Duration: ${Number(event.duration).toFixed(2)}s`;
            }
          } catch (e) {
            // ignore
          }
        }
        row.label.innerText = event.status;
        row.bar.style.width = width;
      }

      function renderOutputs(results) {
        const container = document.getElementById('output-list');
        document.getElementById('outputs').style.display = 'block';
        Object.entries(results).forEach(([taskId, output]) => {
          const wrapper = document.createElement('div');
          wrapper.className = 'col-md-6 mb-3';

          const card = document.createElement('div');
          card.className = 'output-card';

          const header = document.createElement('div');
          header.className = 'output-header mb-2';

          const title = document.createElement('div');
          const h = document.createElement('h4');
          h.className = 'text-info mb-0';
          h.textContent = taskId;
          title.appendChild(h);

          const actions = document.createElement('div');
          actions.className = 'output-actions';

          const copyBtn = document.createElement('button');
          copyBtn.className = 'btn btn-sm btn-outline-secondary';
          copyBtn.textContent = 'Copy';
          copyBtn.onclick = () => navigator.clipboard.writeText(output).catch(()=>{});

          const dlBtn = document.createElement('button');
          dlBtn.className = 'btn btn-sm btn-outline-secondary';
          dlBtn.textContent = 'Download';
          dlBtn.onclick = () => {
            const blob = new Blob([output], {type: 'text/plain'});
            const url = URL.createObjectURL(blob);
            const a = document.createElement('a');
            a.href = url;
            a.download = `${taskId}.txt`;
            document.body.appendChild(a);
            a.click();
            a.remove();
            URL.revokeObjectURL(url);
          };

          const toggleBtn = document.createElement('button');
          toggleBtn.className = 'btn btn-sm btn-info text-white';
          toggleBtn.textContent = 'Show full';

          actions.appendChild(copyBtn);
          actions.appendChild(dlBtn);
          actions.appendChild(toggleBtn);

          header.appendChild(title);
          header.appendChild(actions);

          const meta = document.createElement('div');
          meta.className = 'output-meta mb-2';
          // Show duration and size metadata when provided
          let durationText = '';
          let sizeText = '';
          if (output && typeof output === 'object' && output.output !== undefined) {
            if (output.duration !== undefined) {
              durationText = `Duration: ${output.duration.toFixed(2)}s`;
            }
            const raw = output.output == null ? '' : String(output.output);
            sizeText = `Size: ${raw.length} bytes`;
          } else {
            const raw = output == null ? '' : String(output);
            sizeText = `Size: ${raw.length} bytes`;
          }
          meta.textContent = [durationText, sizeText].filter(Boolean).join(' \u007F ');

          const pre = document.createElement('pre');
          pre.className = 'output-pre mb-0';

          // Try to pretty-print JSON if possible. Normalize `output` shape.
          let rawOutput = output && typeof output === 'object' && output.output !== undefined ? output.output : output;
          let display = rawOutput == null ? '' : String(rawOutput);
          let isJson = false;
          try {
            const parsed = JSON.parse(display);
            display = JSON.stringify(parsed, null, 2);
            isJson = true;
          } catch (e) {
            // not JSON, keep as-is
          }

          const TRUNC = 800;
          let fullShown = false;
          if (display.length > TRUNC) {
            pre.textContent = display.slice(0, TRUNC) + '\n\n... (truncated)';
            toggleBtn.textContent = 'Show full';
          } else {
            if (isJson) {
              pre.innerHTML = highlightJSON(display);
            } else {
              pre.textContent = display;
            }
            toggleBtn.style.display = 'none';
          }

          toggleBtn.onclick = () => {
            if (!fullShown) {
              if (isJson) {
                pre.innerHTML = highlightJSON(display);
              } else {
                pre.textContent = display;
              }
              toggleBtn.textContent = 'Collapse';
              fullShown = true;
            } else {
              if (display.length > TRUNC) {
                pre.textContent = display.slice(0, TRUNC) + '\n\n... (truncated)';
              } else {
                if (isJson) pre.innerHTML = highlightJSON(display); else pre.textContent = display;
              }
              toggleBtn.textContent = 'Show full';
              fullShown = false;
            }
          };

          card.appendChild(header);
          card.appendChild(meta);
          card.appendChild(pre);
          wrapper.appendChild(card);
          container.appendChild(wrapper);
        });
      }

      // Sanitize message content: redact JSON-like payloads
      function sanitizeMessage(msg) {
        if (msg === null || msg === undefined) return '';
        try {
          // If it's an object/array, produce a short summary instead of raw JSON
          if (typeof msg === 'object') {
            try {
              if (Array.isArray(msg)) {
                return `[ARRAY] length=${msg.length}`;
              }
              const keys = Object.keys(msg);
              const sampleKeys = keys.slice(0, 6).join(', ');
              return `[OBJECT] keys=${keys.length} (${sampleKeys}${keys.length>6? ', ...':''})`;
            } catch (err) {
              return '[OBJECT] (uninspectable)';
            }
          }
          const s = String(msg);
          // Special formatting: if this looks like a Task/Task Input/Expected tools block, extract and format
          if (s.includes('Task:') || s.includes('Task Input') || s.includes('Expected tools')) {
            try {
              const taskMatch = s.match(/Task:\s*([^\n\r]+)/i);
              const inputMatch = s.match(/Task Input:\s*\{([^}]*)\}/i);
              const toolsMatch = s.match(/Expected tools:\s*\[([^\]]*)\]/i);
              const taskLine = taskMatch ? taskMatch[1].trim() : '';
              const inputLine = inputMatch ? inputMatch[1].trim() : '';
              const toolsLine = toolsMatch ? toolsMatch[1].trim() : '';
              let out = '';
              if (taskLine) out += `Task: ${taskLine}\n`;
              if (inputLine) out += `Input: {${inputLine}}\n`;
              if (toolsLine) out += `Tools: [${toolsLine}]`;
              return out || s.replace(/\s+/g, ' ').slice(0, 300);
            } catch (err) {
              // fallthrough
            }
          }
          // If it contains obvious JSON structure and is long, redact and show a short snippet
          const looksLikeJson = s.trim().startsWith('{') || s.trim().startsWith('[') || /"\s*:\s*/.test(s);
          if (looksLikeJson && s.length > 120) {
            // try to extract a short human-friendly first line or key
            const oneLine = s.replace(/\s+/g, ' ').slice(0, 160);
            return '[REDACTED JSON] ' + oneLine + '...';
          }
          // Otherwise return the string as-is, truncated mildly
          if (s.length > 500) return s.slice(0, 500) + '...';
          return s;
        } catch (e) {
          return '';
        }
      }

      function appendLog(kind, message) {
        if (!logEl) logEl = document.getElementById('log');
        if (!logEl) return;
        const ts = new Date().toLocaleTimeString();
        const entry = document.createElement('div');
        entry.className = 'log-entry';

        const badge = document.createElement('span');
        // Map kinds to bootstrap badge colors
        const kindMap = {
          'PLAN': 'bg-primary',
          'STATUS': 'bg-info',
          'FINAL': 'bg-dark',
          'FINAL': 'bg-primary',
          'COMPLETE': 'bg-success',
          'ERROR': 'bg-danger',
          'CONSOLE': 'bg-secondary',
          'DEFAULT': 'bg-info'
        };
        const k = (kind || 'info').toUpperCase();
        const cls = kindMap[k] || kindMap['DEFAULT'];
        badge.className = `badge ${cls} text-white`;
        badge.textContent = k;

        const meta = document.createElement('div');
        meta.className = 'log-meta';
        meta.textContent = ts;

        const content = document.createElement('div');
        content.className = 'log-content';
        // If the message contains a Tools: [...] section, convert tool names to links
        let sanitized = sanitizeMessage(message);
        if (sanitized.includes('Tools:')) {
          // safe linkify only the tools list
          sanitized = linkifyTools(sanitized);
          content.innerHTML = sanitized.replace(/\n/g, '<br/>');
        } else {
          content.textContent = sanitized;
        }

        const left = document.createElement('div');
        left.style.display = 'flex';
        left.style.flexDirection = 'column';
        left.style.alignItems = 'flex-start';
        left.appendChild(badge);
        left.appendChild(meta);

        entry.appendChild(left);
        entry.appendChild(content);

        // prepend so newest messages appear at the top
        logEl.prepend(entry);
      }

      // Convert 'Tools: [a, b]' into clickable links. Returns safe HTML string.
      function linkifyTools(text) {
        try {
          // find Tools: [ ... ]
          const m = text.match(/Tools:\s*\[([^\]]*)\]/i);
          if (!m) return escapeHtml(text);
          const list = m[1].split(',').map(s=>s.trim()).filter(Boolean);
          const links = list.map(name => `<a href="#" class="tool-link" data-tool="${escapeHtml(name)}">${escapeHtml(name)}</a>`).join(', ');
          const before = escapeHtml(text.slice(0, m.index));
          const after = escapeHtml(text.slice(m.index + m[0].length));
          return `${before}Tools: [${links}]${after}`;
        } catch (e) {
          return escapeHtml(text);
        }
      }

      function escapeHtml(unsafe) {
        return String(unsafe).replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;');
      }
    </script>
  </body>
</html>
""".replace("{BOOTSTRAP_JS_SRC}", BOOTSTRAP_JS_SRC)


@app.get("/", response_class=HTMLResponse)
async def root() -> HTMLResponse:
    return HTMLResponse(HTML_PAGE)


@app.post("/api/run")
async def start_run(request: RunRequest) -> Dict[str, Any]:
  print(f"[server] /api/run called with config_path={request.config_path} engine={request.engine}")
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
    results: Dict[str, Any] = {}
    run_start = time.perf_counter()

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
      t0 = time.perf_counter()
      output = await asyncio.to_thread(run_single, spec)
      t1 = time.perf_counter()
      duration = t1 - t0
      results[spec.id] = {"output": output, "duration": duration}
      await broadcast(
        {
          "type": "status",
          "task_id": spec.id,
          "status": "completed",
          "output": output,
          "duration": duration,
        }
      )

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

    await broadcast({"type": "complete", "results": results, "duration": overall})
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
