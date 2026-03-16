'use strict';

const adminState = {
  runs: [],
  selectedRunId: null,
  selectedRun: null,
};

document.addEventListener('DOMContentLoaded', () => {
  bindAdminEvents();
  fetchMeta();
  loadRuns();
});

function bindAdminEvents() {
  const runInputs = ['filter-run-id', 'filter-project', 'filter-engine', 'filter-completed'];
  runInputs.forEach((id) => {
    const el = document.getElementById(id);
    if (!el) return;
    el.addEventListener('input', debounce(loadRuns, 200));
    el.addEventListener('change', () => loadRuns());
  });

  const eventInputs = ['filter-event-type', 'filter-event-query'];
  eventInputs.forEach((id) => {
    const el = document.getElementById(id);
    if (!el) return;
    el.addEventListener('input', debounce(loadEvents, 200));
    el.addEventListener('change', () => loadEvents());
  });

  const refreshRuns = document.getElementById('runs-refresh');
  if (refreshRuns) refreshRuns.onclick = () => loadRuns();

  const refreshEvents = document.getElementById('events-refresh');
  if (refreshEvents) refreshEvents.onclick = () => loadEvents();

  const deleteBtn = document.getElementById('delete-run');
  if (deleteBtn) deleteBtn.onclick = () => deleteSelectedRun();
}

function fetchMeta() {
  const el = document.getElementById('admin-version');
  if (!el) return;
  fetch('/api/meta')
    .then((res) => res.ok ? res.json() : null)
    .then((data) => {
      if (data && data.version) {
        el.textContent = `v${data.version}`;
      }
    })
    .catch(() => {});
}

async function loadRuns() {
  const params = new URLSearchParams();
  appendParam(params, 'run_id', valueOf('filter-run-id'));
  appendParam(params, 'project', valueOf('filter-project'));
  appendParam(params, 'engine', valueOf('filter-engine'));
  appendParam(params, 'completed', valueOf('filter-completed'));
  const selectedEventType = valueOf('filter-event-type');
  if (selectedEventType) {
    appendParam(params, 'event_type', selectedEventType);
  }

  try {
    const response = await fetch(`/api/admin/runs?${params.toString()}`);
    const data = await response.json();
    adminState.runs = Array.isArray(data.runs) ? data.runs : [];
    if (!adminState.runs.some((run) => run.run_id === adminState.selectedRunId)) {
      adminState.selectedRunId = adminState.runs[0] ? adminState.runs[0].run_id : null;
    }
    adminState.selectedRun = adminState.runs.find((run) => run.run_id === adminState.selectedRunId) || null;
    renderRuns(data.total || adminState.runs.length);
    if (adminState.selectedRunId) {
      await loadEvents();
    } else {
      renderEvents([], []);
    }
  } catch (error) {
    renderRunsError(error);
    renderEvents([], []);
  }
}

function renderRuns(total) {
  const container = document.getElementById('admin-run-list');
  const summary = document.getElementById('runs-summary');
  if (summary) {
    summary.textContent = `${adminState.runs.length} run(s) shown, ${total} matched current filters.`;
  }
  if (!container) return;
  container.innerHTML = '';
  if (!adminState.runs.length) {
    container.innerHTML = '<div class="text-secondary small">No runs match the current filters.</div>';
    updateSelectedRunMeta();
    return;
  }
  adminState.runs.forEach((run) => {
    const item = document.createElement('button');
    item.type = 'button';
    item.className = `admin-run-item ${run.run_id === adminState.selectedRunId ? 'active' : ''}`;
    item.onclick = () => {
      adminState.selectedRunId = run.run_id;
      adminState.selectedRun = run;
      renderRuns(total);
      loadEvents();
    };
    const status = run.completed ? 'Completed' : 'Active';
    const eventTypes = Array.isArray(run.event_types) && run.event_types.length ? run.event_types.join(', ') : 'none';
    item.innerHTML = `
      <div class="d-flex justify-content-between align-items-start gap-2">
        <div>
          <div class="admin-run-title">${escapeHtml(run.project || '(unknown)')}</div>
          <div class="admin-run-meta">${escapeHtml(run.run_id)}</div>
        </div>
        <span class="badge ${run.completed ? 'bg-success-subtle text-success-emphasis' : 'bg-warning-subtle text-warning-emphasis'}">${status}</span>
      </div>
      <div class="admin-run-grid">
        <span><strong>Engine:</strong> ${escapeHtml(run.engine || 'unknown')}</span>
        <span><strong>Events:</strong> ${Number(run.event_count || 0)}</span>
        <span><strong>Artifacts:</strong> ${run.has_artifacts ? 'yes' : 'no'}</span>
        <span><strong>Source:</strong> ${escapeHtml(run.source || 'runtime')}</span>
      </div>
      <div class="admin-run-tags">${escapeHtml(eventTypes)}</div>
    `;
    container.appendChild(item);
  });
  updateSelectedRunMeta();
}

function renderRunsError(error) {
  const container = document.getElementById('admin-run-list');
  const summary = document.getElementById('runs-summary');
  if (summary) summary.textContent = 'Failed to load runs.';
  if (container) {
    container.innerHTML = `<div class="text-danger small">${escapeHtml(String(error))}</div>`;
  }
  adminState.selectedRun = null;
  adminState.selectedRunId = null;
  updateSelectedRunMeta();
}

async function loadEvents() {
  const summary = document.getElementById('events-summary');
  const refreshEvents = document.getElementById('events-refresh');
  if (refreshEvents) refreshEvents.disabled = !adminState.selectedRunId;
  if (!adminState.selectedRunId) {
    renderEvents([], []);
    return;
  }

  const params = new URLSearchParams();
  appendParam(params, 'event_type', valueOf('filter-event-type'));
  appendParam(params, 'q', valueOf('filter-event-query'));

  try {
    const response = await fetch(`/api/admin/runs/${encodeURIComponent(adminState.selectedRunId)}/events?${params.toString()}`);
    const data = await response.json();
    renderEvents(Array.isArray(data.events) ? data.events : [], Array.isArray(data.event_types) ? data.event_types : []);
    if (summary) {
      summary.textContent = `${data.total || 0} event(s) matched for ${adminState.selectedRunId}.`;
    }
  } catch (error) {
    if (summary) summary.textContent = 'Failed to load events.';
    renderEvents([{ type: 'error', message: String(error) }], []);
  }
}

function renderEvents(events, eventTypes) {
  const container = document.getElementById('admin-event-list');
  if (container) {
    container.innerHTML = '';
  }
  syncEventTypeFilter(eventTypes);
  updateSelectedRunMeta();
  if (!container) return;
  if (!adminState.selectedRunId) {
    container.innerHTML = '<div class="text-secondary small">No run selected.</div>';
    return;
  }
  if (!events.length) {
    container.innerHTML = '<div class="text-secondary small">No events match the current filters.</div>';
    return;
  }

  events.forEach((event, index) => {
    const item = document.createElement('div');
    item.className = 'admin-event-item';
    const eventType = escapeHtml(String(event.type || 'event'));
    const timestamp = escapeHtml(String(event.timestamp || ''));
    item.innerHTML = `
      <div class="d-flex justify-content-between align-items-center gap-2 mb-2">
        <span class="badge bg-info-subtle text-info-emphasis">${eventType}</span>
        <span class="admin-run-meta">${timestamp || `#${index + 1}`}</span>
      </div>
      <pre class="admin-event-pre">${escapeHtml(JSON.stringify(event, null, 2))}</pre>
    `;
    container.appendChild(item);
  });
}

function syncEventTypeFilter(eventTypes) {
  const select = document.getElementById('filter-event-type');
  if (!select) return;
  const current = select.value;
  const values = [''].concat(eventTypes);
  select.innerHTML = values.map((value) => {
    const label = value || 'All';
    return `<option value="${escapeHtml(value)}">${escapeHtml(label)}</option>`;
  }).join('');
  if (values.includes(current)) {
    select.value = current;
  }
}

function updateSelectedRunMeta() {
  const meta = document.getElementById('selected-run-meta');
  const deleteBtn = document.getElementById('delete-run');
  if (!meta || !deleteBtn) return;
  const run = adminState.selectedRun;
  if (!run) {
    meta.textContent = 'Select a run to inspect events';
    deleteBtn.disabled = true;
    return;
  }
  meta.textContent = `${run.project || '(unknown)'} • ${run.run_id} • ${run.event_count || 0} event(s)`;
  deleteBtn.disabled = !run.completed;
}

async function deleteSelectedRun() {
  const run = adminState.selectedRun;
  if (!run) return;
  if (!run.completed) {
    window.alert('Only completed runs can be deleted.');
    return;
  }
  const confirmed = window.confirm(`Delete run ${run.run_id} and its persisted events/artifacts?`);
  if (!confirmed) return;

  try {
    const response = await fetch(`/api/admin/runs/${encodeURIComponent(run.run_id)}`, { method: 'DELETE' });
    const data = await response.json();
    if (!response.ok) {
      throw new Error(data.detail || 'Delete failed');
    }
    adminState.selectedRunId = null;
    adminState.selectedRun = null;
    await loadRuns();
  } catch (error) {
    window.alert(String(error));
  }
}

function appendParam(params, key, value) {
  if (value) params.set(key, value);
}

function valueOf(id) {
  const el = document.getElementById(id);
  return el ? String(el.value || '').trim() : '';
}

function debounce(fn, wait) {
  let timer = null;
  return () => {
    window.clearTimeout(timer);
    timer = window.setTimeout(() => fn(), wait);
  };
}

function escapeHtml(value) {
  return String(value)
    .replaceAll('&', '&amp;')
    .replaceAll('<', '&lt;')
    .replaceAll('>', '&gt;')
    .replaceAll('"', '&quot;')
    .replaceAll("'", '&#39;');
}
