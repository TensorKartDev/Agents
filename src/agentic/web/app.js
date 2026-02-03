'use strict';

const state = {
  ws: null,
  selectedConfig: null,
  currentRunId: null,
  runsInterval: null,
  runsByPath: {},
  tabs: {},
  activeTabId: null,
};

function getActiveTab() {
  return getTab(state.currentRunId) || getTab(state.activeTabId);
}

function getTab(runId) {
  return runId ? state.tabs[runId] : null;
}

function ensureTab(runId, label) {
  if (!runId) return null;
  if (state.tabs[runId]) return state.tabs[runId];
  const tabs = document.getElementById('mission-tabs');
  const contents = document.getElementById('mission-tab-contents');
  if (!tabs || !contents) return null;

  const tabId = `tab-${runId}`;
  const li = document.createElement('li');
  li.className = 'nav-item';
  const btn = document.createElement('button');
  btn.className = 'nav-link';
  btn.id = `${tabId}-btn`;
  btn.type = 'button';
  btn.role = 'tab';
  btn.textContent = label || `Run ${runId.slice(0, 8)}`;
  btn.onclick = () => activateTab(runId);
  li.appendChild(btn);
  tabs.appendChild(li);

  const pane = document.createElement('div');
  pane.className = 'tab-pane fade';
  pane.id = `${tabId}-pane`;
  pane.role = 'tabpanel';
  pane.dataset.runId = runId;
  pane.innerHTML = `
    <div class="row">
      <div class="col-lg-7 mb-3">
        <div class="glass-card p-3 h-100">
          <div class="d-flex justify-content-between align-items-center mb-2">
            <div>
              <div class="text-secondary small">Run: ${runId.slice(0, 8)}</div>
              <small class="text-secondary" data-role="run-summary">Ready</small>
            </div>
            <div class="text-end">
              <small class="text-secondary" data-role="engine-label"></small>
            </div>
          </div>
          <div class="console-log" data-role="log"></div>
        </div>
      </div>
      <div class="col-lg-5 mb-3">
        <div class="glass-card p-3 mb-3" data-role="plan-container" style="display:none;">
          <div class="d-flex justify-content-between align-items-center mb-3 flex-wrap gap-2">
            <div>
              <h2 data-role="project-title" class="h5 mb-0 text-info"></h2>
            </div>
            <div class="text-end">
              <span class="text-uppercase text-secondary small">Progress</span>
              <div class="progress progress-with-label" style="width: 220px; height: 10px;">
                <div data-role="global-progress" class="progress-bar bg-info" style="width: 0%"></div>
                <span data-role="global-progress-label" class="progress-label">0%</span>
              </div>
            </div>
          </div>
          <div data-role="tasks-list" class="vstack gap-3"></div>
        </div>
      </div>
      <div class="col-12 mb-3">
        <div class="glass-card p-3" data-role="outputs" style="display:none;">
          <h3 class="h6 text-info mb-3">Results</h3>
          <div data-role="output-list" class="row gy-3"></div>
        </div>
      </div>
    </div>
  `;
  contents.appendChild(pane);

  const refs = {
    tabButton: btn,
    pane,
    log: pane.querySelector('[data-role="log"]'),
    planContainer: pane.querySelector('[data-role="plan-container"]'),
    tasksList: pane.querySelector('[data-role="tasks-list"]'),
    outputs: pane.querySelector('[data-role="outputs"]'),
    outputList: pane.querySelector('[data-role="output-list"]'),
    globalProgress: pane.querySelector('[data-role="global-progress"]'),
    globalProgressLabel: pane.querySelector('[data-role="global-progress-label"]'),
    engineLabel: pane.querySelector('[data-role="engine-label"]'),
    projectTitle: pane.querySelector('[data-role="project-title"]'),
    runSummary: pane.querySelector('[data-role="run-summary"]'),
  };

  const tabState = {
    statusRows: {},
    totalTasks: 0,
    completedTasks: 0,
    elements: refs,
  };
  state.tabs[runId] = tabState;
  return tabState;
}

function activateTab(runId) {
  const tab = ensureTab(runId);
  if (!tab) return;
  const tabs = document.getElementById('mission-tabs');
  const contents = document.getElementById('mission-tab-contents');
  if (tabs && contents) {
    tabs.querySelectorAll('.nav-link').forEach(btn => btn.classList.remove('active'));
    contents.querySelectorAll('.tab-pane').forEach(p => p.classList.remove('show', 'active'));
  }
  tab.elements.tabButton.classList.add('active');
  tab.elements.pane.classList.add('show', 'active');
  state.activeTabId = runId;
  state.currentRunId = runId;
  state.logEl = tab.elements.log;
}

function setTabLabel(runId, label) {
  const tab = getTab(runId);
  if (tab && label) {
    tab.elements.tabButton.textContent = label;
  }
}

async function fetchAndRenderAgents() {
  try {
    const response = await fetch("/api/agents");
    const agents = await response.json();
    const list = Array.isArray(agents) ? agents : [];
    renderConfigCards(list);
  } catch (error) {
    console.error("Error fetching agents:", error);
    renderConfigCards([]);
  }
}

window.addEventListener("error", (e) => {
  try {
    console.error("Uncaught error", e);
  } catch (err) {}
});
window.addEventListener("unhandledrejection", (e) => {
  try {
    console.error("Unhandled rejection", e);
  } catch (err) {}
});

document.addEventListener("DOMContentLoaded", () => {
  state.logEl = null;
  fetchAndRenderAgents();
  startRunsPolling();
});

function renderConfigCards(agents) {
  const container = document.getElementById("config-cards-container");
  if (!container) return;
  container.innerHTML = ""; // Clear existing cards
  agents.forEach((agent) => {
    const col = document.createElement("div");
    col.className = "col-sm-6 col-lg-3";

    const card = document.createElement("div");
    card.className = "config-card";
    card.dataset.config = agent.config_path;
    card.onclick = () => selectConfig(agent, card, true);

    const icon = document.createElement("div");
    icon.className = "config-card-icon";
    const img = document.createElement("img");
    img.src = agent.icon;
    img.style.width = "48px";
    img.style.height = "48px";
    icon.appendChild(img);

    const title = document.createElement("div");
    title.className = "config-card-title";
    title.textContent = agent.name;

    const desc = document.createElement("div");
    desc.className = "config-card-desc";
    desc.textContent = agent.description;

    const badge = document.createElement("div");
    badge.className = "config-card-agent-badge";
    badge.textContent = agent.id;

    const actions = document.createElement("div");
    actions.className = "w-100";
    const startBtn = document.createElement("button");
    startBtn.className = "btn btn-outline-primary w-100 start-stop-btn";
    startBtn.textContent = "Start";
    startBtn.dataset.configPath = agent.config_path;
    startBtn.dataset.state = "ready";
    startBtn.onclick = (evt) => {
      evt.stopPropagation();
      selectConfig(agent, card, false);
      toggleRun(agent.config_path, startBtn);
    };
    actions.appendChild(startBtn);

    card.appendChild(icon);
    card.appendChild(title);
    card.appendChild(desc);
    card.appendChild(badge);
    card.appendChild(actions);
    col.appendChild(card);
    container.appendChild(col);
  });
}

function selectConfig(agent, cardEl, refreshRuns = true) {
  state.selectedConfig = agent.config_path;
  document.querySelectorAll(".config-card").forEach((c) => c.classList.remove("active"));
  if (cardEl) cardEl.classList.add("active");
  if (refreshRuns) {
    refreshRunsAndAttach(agent.config_path);
  } else {
    attachToRunByConfig(agent.config_path);
  }
}

function toggleRun(configPath, button) {
  if (!button) return;
  const stateValue = button.dataset.state;
  if (stateValue === 'running' && button.dataset.runId) {
    stopRun(button.dataset.runId, configPath, button);
  } else {
    deployRun(configPath, button);
  }
}

function setButtonRunning(button, runId) {
  if (!button) return;
  button.dataset.state = 'running';
  button.dataset.runId = runId || '';
  button.textContent = 'Stop';
  button.classList.remove('btn-outline-primary');
  button.classList.add('btn-danger');
}

function setButtonReady(button) {
  if (!button) return;
  button.dataset.state = 'ready';
  button.dataset.runId = '';
  button.textContent = 'Start';
  button.classList.add('btn-outline-primary');
  button.classList.remove('btn-danger');
}

function deployRun(configPath, button) {
  const cfgPath = configPath || state.selectedConfig;
  if (!cfgPath) {
    alert('Please select a config first');
    return;
  }
  state.selectedConfig = cfgPath;
  const engine = document.getElementById('engine').value;
  const btn = button;
  if (btn) { btn.disabled = true; btn.textContent = 'Starting...'; }
  resetUI();
  fetch('/api/run', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ config_path: cfgPath, engine }),
  }).then(res => {
    if (!res.ok) {
      return res.text().then(text => {
        throw new Error(text || 'Failed to start run');
      });
    }
    return res.json().then(data => {
      const tab = ensureTab(data.run_id, data.project || cfgPath);
      activateTab(data.run_id);
      state.currentRunConfig = cfgPath;
      if (tab && tab.elements.runSummary) {
        tab.elements.runSummary.innerText = data.already_running ? 'Attached to existing run' : 'Starting...';
      }
      if (btn) setButtonRunning(btn, data.run_id);
      connectWebSocket(data.run_id);
    });
  }).catch(err => {
    console.error('Error starting run', err);
    alert('Error starting run: ' + (err && err.message ? err.message : String(err)));
  }).finally(() => {
    if (btn) { btn.disabled = false; if (btn.dataset.state !== 'running') btn.textContent = 'Start'; }
  });
}

function stopRun(runId, configPath, button) {
  if (!runId) return;
  const btn = button;
  if (btn) { btn.disabled = true; btn.textContent = 'Stopping...'; }
  fetch(`/api/run/${runId}/stop`, { method: 'POST' })
    .then(res => {
      if (!res.ok) return res.text().then(t => { throw new Error(t || 'Failed to stop run'); });
      return res.json();
    })
    .then(() => {
      if (btn) setButtonReady(btn);
      if (state.ws) state.ws.close();
      const tab = getTab(runId);
      if (tab && tab.elements.runSummary) tab.elements.runSummary.innerText = 'Stopped';
      state.currentRunId = null;
      state.currentRunConfig = null;
    })
    .catch(err => {
      console.error('Error stopping run', err);
      alert('Error stopping run: ' + (err && err.message ? err.message : String(err)));
    })
    .finally(() => {
      if (btn) { btn.disabled = false; if (btn.dataset.state !== 'running') btn.textContent = 'Start'; }
    });
}

function resetUI() {
  if (state.ws) { state.ws.close(); state.ws = null; }
}

function startRunsPolling() {
  if (state.runsInterval) clearInterval(state.runsInterval);
  fetchActiveRuns();
  state.runsInterval = setInterval(fetchActiveRuns, 4000);
}

function fetchActiveRuns() {
  fetch('/api/runs').then(res => res.json()).then(renderActiveRuns).catch(() => {});
}

function refreshRunsAndAttach(configPath) {
  fetch('/api/runs')
    .then(res => res.json())
    .then(data => {
      renderActiveRuns(data);
      attachToRunByConfig(configPath);
    })
    .catch(() => attachToRunByConfig(configPath));
}

function renderActiveRuns(data) {
  const container = document.getElementById('active-runs-list');
  if (!container) return;
  const runs = (data && data.runs) ? data.runs : [];
  const runMap = {};
  runs.forEach(run => {
    const keys = [run.request_path, run.config_path].filter(Boolean);
    keys.forEach(k => { runMap[k] = run; });
  });
  state.runsByPath = runMap;
  if (!runs.length) {
    container.textContent = 'No workflows yet.';
    syncCardButtons([]);
    return;
  }
  container.innerHTML = '';
  runs.sort((a, b) => b.started_at - a.started_at).forEach(run => {
    const chip = document.createElement('div');
    chip.className = 'run-chip';
    chip.tabIndex = 0;
    chip.onclick = () => attachToRun(run);
    chip.onkeydown = e => {
      if (e.key === 'Enter' || e.key === ' ') {
        e.preventDefault();
        attachToRun(run);
      }
    };

    const left = document.createElement('div');
    const title = document.createElement('div');
    title.className = 'run-title';
    title.textContent = run.project;
    const meta = document.createElement('div');
    meta.className = 'run-meta';
    const statusText = run.completed ? 'Completed' : 'Running';
    meta.textContent = `${run.tasks_completed}/${run.tasks_total} tasks • ${run.engine} • ${statusText}`;
    left.appendChild(title);
    left.appendChild(meta);

    const right = document.createElement('div');
    right.className = 'run-progress-wrap';
    const barWrap = document.createElement('div');
    barWrap.className = 'progress progress-with-label';
    barWrap.style.height = '10px';
    const bar = document.createElement('div');
    bar.className = `progress-bar ${run.completed ? 'bg-success' : 'bg-info'}`;
    const pct = Number.isFinite(run.progress) ? Math.round(run.progress) : 0;
    bar.style.width = `${pct}%`;
    const barLabel = document.createElement('span');
    barLabel.className = 'progress-label';
    barLabel.textContent = `${pct}%`;
    barWrap.appendChild(bar);
    barWrap.appendChild(barLabel);

    const badge = document.createElement('span');
    badge.className = `badge ${run.completed ? 'bg-success' : 'bg-primary'} ms-2`;
    badge.textContent = run.completed ? 'Completed' : 'Live';

    right.appendChild(barWrap);
    right.appendChild(badge);

    chip.appendChild(left);
    chip.appendChild(right);
    container.appendChild(chip);
  });
  syncCardButtons(runs);
}

function syncCardButtons(activeRuns) {
  const activeMap = {};
  activeRuns.filter(r => !r.completed).forEach(run => {
    const keys = [run.request_path, run.config_path].filter(Boolean);
    keys.forEach(k => { activeMap[k] = run.run_id; });
  });
  const buttons = document.querySelectorAll('.start-stop-btn');
  buttons.forEach(btn => {
    const cfgPath = btn.dataset.configPath;
    let runId = activeMap[cfgPath];
    if (!runId) {
      const match = Object.entries(activeMap).find(([key]) => key && key.endsWith(cfgPath));
      if (match) runId = match[1];
    }
    if (runId) {
      setButtonRunning(btn, runId);
    } else if (btn.dataset.state === 'running') {
      setButtonReady(btn);
    }
  });
}

function connectWebSocket(runId) {
  activateTab(runId);
  const protocol = window.location.protocol === 'https:' ? 'wss' : 'ws';
  state.ws = new WebSocket(`${protocol}://${window.location.host}/ws/${runId}`);
  state.ws.onmessage = event => {
    const payload = JSON.parse(event.data);
    handleEvent(payload);
  };
  state.ws.onclose = () => console.log('WebSocket closed');
}

function handleEvent(event) {
  if (event.type === 'plan') {
    renderPlan(event);
    appendLog('plan', `Loaded ${event.tasks.length} tasks for ${event.project}`);
  } else if (event.type === 'status') {
    updateStatus(event);
    appendLog('status', `${event.task_id} -> ${event.status}`);
    if (event.output && typeof event.output === 'string' && event.output.includes('FINAL:')) {
      appendLog('final', event.output);
    }
  } else if (event.type === 'complete') {
    renderOutputs(event.results);
    if (event.duration !== undefined) {
      const tab = getTab(state.currentRunId);
      if (tab && tab.elements.runSummary) tab.elements.runSummary.innerText = `Duration: ${Number(event.duration).toFixed(2)}s`;
    }
    appendLog('complete', event.stopped ? 'Run stopped' : 'Run complete — results available');
    markRunFinished();
  } else if (event.type === 'console') {
    if (event.message) appendLog('console', event.message);
  } else if (event.type === 'error') {
    alert(event.message);
    appendLog('error', event.message);
    markRunFinished();
  }
}

function renderPlan(event) {
  const tab = getActiveTab();
  if (!tab) return;
  setTabLabel(state.currentRunId || state.activeTabId, event.project);
  const { projectTitle, engineLabel, planContainer, tasksList, globalProgressLabel } = tab.elements;
  if (projectTitle) projectTitle.innerText = event.project;
  if (engineLabel) engineLabel.innerText = `Engine: ${event.engine}`;
  if (planContainer) planContainer.style.display = 'block';
  const list = tasksList;
  if (!list) return;
  list.innerHTML = '';
  tab.statusRows = {};
  tab.totalTasks = event.tasks.length;
  tab.completedTasks = 0;
  if (globalProgressLabel) globalProgressLabel.textContent = '0%';
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
    tab.statusRows[task.id] = {
      label: card.querySelector(`#status-${task.id}`),
      bar: card.querySelector(`#progress-${task.id}`),
    };
  });
}

function updateStatus(event) {
  const tab = getActiveTab();
  if (!tab) return;
  const row = tab.statusRows[event.task_id];
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
    tab.completedTasks += 1;
    const overall = tab.totalTasks ? Math.round((tab.completedTasks / tab.totalTasks) * 100) : 0;
    if (tab.elements.globalProgress) tab.elements.globalProgress.style.width = overall + '%';
    const globalLabel = tab.elements.globalProgressLabel;
    if (globalLabel) globalLabel.textContent = `${overall}%`;
    if (event.duration !== undefined) {
      const durEl = tab.elements.pane.querySelector(`#duration-${event.task_id}`);
      if (durEl) durEl.textContent = `Duration: ${Number(event.duration).toFixed(2)}s`;
    }
  }
  row.label.innerText = event.status;
  row.bar.style.width = width;
}

function renderOutputs(results) {
  const tab = getActiveTab();
  if (!tab || !tab.elements.outputList || !tab.elements.outputs) return;
  const container = tab.elements.outputList;
  tab.elements.outputs.style.display = 'block';
  Object.entries(results || {}).forEach(([taskId, output]) => {
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
    copyBtn.onclick = () => navigator.clipboard.writeText(output && output.output !== undefined ? output.output : output).catch(()=>{});

    const dlBtn = document.createElement('button');
    dlBtn.className = 'btn btn-sm btn-outline-secondary';
    dlBtn.textContent = 'Download';
    dlBtn.onclick = () => {
      const raw = output && output.output !== undefined ? output.output : output;
      const blob = new Blob([raw ?? ''], {type: 'text/plain'});
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
    let durationText = '';
    let sizeText = '';
    if (output && typeof output === 'object' && output.output !== undefined) {
      if (output.duration !== undefined) durationText = `Duration: ${output.duration.toFixed(2)}s`;
      const raw = output.output == null ? '' : String(output.output);
      sizeText = `Size: ${raw.length} bytes`;
    } else {
      const raw = output == null ? '' : String(output);
      sizeText = `Size: ${raw.length} bytes`;
    }
    meta.textContent = [durationText, sizeText].filter(Boolean).join(' \u007F ');

    const pre = document.createElement('pre');
    pre.className = 'output-pre mb-0';

    let rawOutput = output && typeof output === 'object' && output.output !== undefined ? output.output : output;
    let display = rawOutput == null ? '' : String(rawOutput);
    let isJson = false;
    try {
      const parsed = JSON.parse(display);
      display = JSON.stringify(parsed, null, 2);
      isJson = true;
    } catch (e) {
      // not JSON
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

function appendLog(kind, message) {
  if (!state.logEl) {
    const tab = getTab(state.currentRunId) || getTab(state.activeTabId);
    if (tab) state.logEl = tab.elements.log;
  }
  if (!state.logEl) return;
  const ts = new Date().toLocaleTimeString();
  const entry = document.createElement('div');
  entry.className = 'log-entry';

  const badge = document.createElement('span');
  const kindMap = {
    'PLAN': 'bg-primary',
    'STATUS': 'bg-info',
    'FINAL': 'bg-primary',
    'COMPLETE': 'bg-success',
    'ERROR': 'bg-danger',
    'CONSOLE': 'bg-secondary',
    'DEFAULT': 'bg-info',
  };
  const k = (kind || 'info').toUpperCase();
  const cls = kindMap[k] || kindMap.DEFAULT;
  badge.className = `badge ${cls} text-white`;
  badge.textContent = k;

  const meta = document.createElement('div');
  meta.className = 'log-meta';
  meta.textContent = ts;

  const content = document.createElement('div');
  content.className = 'log-content';
  let sanitized = sanitizeMessage(message);
  if (sanitized.includes('Tools:')) {
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
  state.logEl.prepend(entry);
}

function getButtonForConfig(configPath) {
  const buttons = document.querySelectorAll('.start-stop-btn');
  for (const btn of buttons) {
    if (btn.dataset.configPath === configPath) return btn;
  }
  return null;
}

function markRunFinished() {
  if (!state.currentRunConfig) return;
  const btn = getButtonForConfig(state.currentRunConfig);
  if (btn) setButtonReady(btn);
   const tab = getTab(state.currentRunId);
   if (tab && tab.elements.runSummary) tab.elements.runSummary.innerText = 'Completed';
  state.currentRunConfig = null;
  state.currentRunId = null;
}

function attachToRunByConfig(configPath) {
  const run = findRunByConfig(configPath);
  if (run) attachToRun(run);
}

function findRunByConfig(configPath) {
  if (!configPath || !state.runsByPath) return null;
  if (state.runsByPath[configPath]) return state.runsByPath[configPath];
  const entry = Object.entries(state.runsByPath).find(([key]) => key && (key === configPath || key.endsWith(configPath)));
  return entry ? entry[1] : null;
}

function attachToRun(run) {
  if (!run || run.run_id === state.currentRunId) return;
  resetUI();
  const configPath = run.request_path || run.config_path;
  ensureTab(run.run_id, run.project || configPath);
  activateTab(run.run_id);
  state.currentRunConfig = configPath || '';
  state.selectedConfig = configPath || state.selectedConfig;
  const tab = getTab(run.run_id);
  if (tab && tab.elements.runSummary) tab.elements.runSummary.innerText = run.completed ? 'Viewing completed run' : 'Attaching to run...';
  const btn = getButtonForConfig(configPath) || getButtonForConfig(run.config_path);
  if (btn) {
    if (run.completed) setButtonReady(btn); else setButtonRunning(btn, run.run_id);
  }
  connectWebSocket(run.run_id);
}

function sanitizeMessage(msg) {
  if (msg === null || msg === undefined) return '';
  try {
    if (typeof msg === 'object') {
      try {
        if (Array.isArray(msg)) return `[ARRAY] length=${msg.length}`;
        const keys = Object.keys(msg);
        const sampleKeys = keys.slice(0, 6).join(', ');
        return `[OBJECT] keys=${keys.length} (${sampleKeys}${keys.length>6? ', ...':''})`;
      } catch (err) {
        return '[OBJECT] (uninspectable)';
      }
    }
    const s = String(msg);
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
    const looksLikeJson = s.trim().startsWith('{') || s.trim().startsWith('[') || /"\s*:\s*/.test(s);
    if (looksLikeJson && s.length > 120) {
      const oneLine = s.replace(/\s+/g, ' ').slice(0, 160);
      return '[REDACTED JSON] ' + oneLine + '...';
    }
    if (s.length > 500) return s.slice(0, 500) + '...';
    return s;
  } catch (e) {
    return '';
  }
}

function highlightJSON(jsonStr) {
  const esc = escapeHtml(jsonStr);
  let out = esc.replace(/("([^"\\]|\\.)*")(?=\s*:)/g, '<span class="hl-key">$1</span>');
  out = out.replace(/(:\s*)("([^"\\]|\\.)*")/g, '$1<span class="hl-string">$2</span>');
  out = out.replace(/(:\s*)(-?\d+(?:\.\d+)?(?:[eE][+\-]?\d+)?)/g, '$1<span class="hl-number">$2</span>');
  out = out.replace(/(:\s*)(\btrue\b|\bfalse\b)/g, '$1<span class="hl-boolean">$2</span>');
  out = out.replace(/(:\s*)(\bnull\b)/g, '$1<span class="hl-null">$2</span>');
  return out;
}

function linkifyTools(text) {
  try {
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
