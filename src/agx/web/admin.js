'use strict';

const adminState = {
  user: null,
  activeTab: 'runs',
  runs: [],
  selectedRunId: null,
  selectedRun: null,
  packages: [],
};

document.addEventListener('DOMContentLoaded', async () => {
  bindAdminEvents();
  loadAuthProviders();
  await restoreSession();
});

function bindAdminEvents() {
  const loginForm = document.getElementById('login-form');
  if (loginForm) loginForm.addEventListener('submit', onLoginSubmit);

  const logoutButton = document.getElementById('logout-button');
  if (logoutButton) logoutButton.onclick = () => logout();

  document.querySelectorAll('[data-admin-tab]').forEach((btn) => {
    btn.addEventListener('click', () => setActiveTab(btn.dataset.adminTab || 'runs'));
  });

  const runInputs = ['filter-run-id', 'filter-project', 'filter-engine', 'filter-completed'];
  runInputs.forEach((id) => bindRefreshOnInput(id, loadRuns));
  bindRefreshOnInput('filter-event-type', loadEvents);
  bindRefreshOnInput('filter-event-query', loadEvents);

  clickBind('runs-refresh', loadRuns);
  clickBind('events-refresh', loadEvents);
  clickBind('delete-run', deleteSelectedRun);
  clickBind('packages-refresh', loadPackages);
  clickBind('users-refresh', loadUsers);

  const uploadForm = document.getElementById('package-upload-form');
  if (uploadForm) uploadForm.addEventListener('submit', onPackageUpload);

  const userForm = document.getElementById('user-create-form');
  if (userForm) userForm.addEventListener('submit', onUserCreate);
}

async function restoreSession() {
  try {
    const response = await fetch('/api/auth/me');
    if (!response.ok) {
      renderLoggedOut('Authentication required.');
      return;
    }
    adminState.user = await response.json();
    renderLoggedIn();
    await refreshAll();
  } catch (error) {
    renderLoggedOut(String(error));
  }
}

async function loadAuthProviders() {
  const container = document.getElementById('oauth-provider-list');
  if (!container) return;
  try {
    const response = await fetch('/api/auth/providers');
    const data = await response.json();
    const providers = Array.isArray(data.providers) ? data.providers : [];
    if (!providers.length) {
      container.innerHTML = '<div class="text-secondary small">No external providers are configured.</div>';
      return;
    }
    container.innerHTML = '';
    providers.forEach((provider) => {
      const item = document.createElement(provider.enabled === false ? 'div' : 'a');
      item.className = `btn ${provider.enabled === false ? 'btn-outline-secondary disabled text-start' : 'btn-outline-dark text-start'} w-100`;
      item.innerHTML = `<i class="bi bi-box-arrow-in-right"></i> Continue with ${escapeHtml(provider.label || provider.name)}`;
      if (provider.enabled === false) {
        const reason = document.createElement('div');
        reason.className = 'small text-secondary mt-1';
        reason.textContent = provider.reason || 'Not available.';
        container.appendChild(item);
        container.appendChild(reason);
      } else {
        item.href = `/auth/oauth/${encodeURIComponent(provider.name)}/login`;
        container.appendChild(item);
      }
    });
  } catch (error) {
    container.innerHTML = `<div class="text-danger small">${escapeHtml(String(error))}</div>`;
  }
}

async function refreshAll() {
  await Promise.all([loadRuns(), loadPackages(), loadUsers()]);
}

async function onLoginSubmit(event) {
  event.preventDefault();
  const status = document.getElementById('login-status');
  try {
    const response = await fetch('/api/auth/login', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        username: valueOf('login-username'),
        password: valueOf('login-password'),
      }),
    });
    const data = await response.json();
    if (!response.ok) {
      throw new Error(data.detail || 'Login failed');
    }
    adminState.user = data;
    renderLoggedIn();
    if (status) status.textContent = `Authenticated as ${data.username}.`;
    await refreshAll();
  } catch (error) {
    renderLoggedOut(String(error));
  }
}

async function logout() {
  await fetch('/api/auth/logout', { method: 'POST' }).catch(() => {});
  adminState.user = null;
  adminState.runs = [];
  adminState.selectedRun = null;
  adminState.selectedRunId = null;
  adminState.packages = [];
  renderLoggedOut('Logged out.');
}

function renderLoggedIn() {
  toggleClass('login-card', 'd-none', true);
  toggleClass('admin-shell', 'd-none', false);
  toggleClass('logout-button', 'd-none', false);
  const badge = document.getElementById('auth-user-badge');
  if (badge && adminState.user) {
    badge.classList.remove('d-none');
    badge.textContent = `${adminState.user.display_name || adminState.user.username} • ${adminState.user.role}`;
  }
  const usersTab = document.getElementById('users-tab-item');
  if (usersTab) {
    usersTab.classList.toggle('d-none', adminState.user.role !== 'admin');
  }
  if (adminState.activeTab === 'users' && adminState.user.role !== 'admin') {
    setActiveTab('runs');
  } else {
    setActiveTab(adminState.activeTab);
  }
}

function renderLoggedOut(message) {
  toggleClass('login-card', 'd-none', false);
  toggleClass('admin-shell', 'd-none', true);
  toggleClass('logout-button', 'd-none', true);
  const badge = document.getElementById('auth-user-badge');
  if (badge) {
    badge.classList.add('d-none');
    badge.textContent = '';
  }
  textSet('login-status', message || 'Authentication required.');
  textSet('runs-summary', 'Authentication required.');
  renderRuns([]);
  renderEvents([], []);
  renderPackages([]);
  renderUsers([]);
}

function setActiveTab(tabName) {
  adminState.activeTab = tabName;
  document.querySelectorAll('[data-admin-tab]').forEach((btn) => {
    btn.classList.toggle('active', btn.dataset.adminTab === tabName);
  });
  document.querySelectorAll('[data-admin-panel]').forEach((panel) => {
    panel.classList.toggle('d-none', panel.dataset.adminPanel !== tabName);
  });
}

async function loadRuns() {
  if (!adminState.user) return;
  const params = new URLSearchParams();
  appendParam(params, 'run_id', valueOf('filter-run-id'));
  appendParam(params, 'project', valueOf('filter-project'));
  appendParam(params, 'engine', valueOf('filter-engine'));
  appendParam(params, 'completed', valueOf('filter-completed'));
  appendParam(params, 'event_type', valueOf('filter-event-type'));
  try {
    const response = await fetch(`/api/admin/runs?${params.toString()}`);
    const data = await response.json();
    if (!response.ok) throw new Error(data.detail || 'Failed to load runs');
    adminState.runs = Array.isArray(data.runs) ? data.runs : [];
    if (!adminState.runs.some((run) => run.run_id === adminState.selectedRunId)) {
      adminState.selectedRunId = adminState.runs[0] ? adminState.runs[0].run_id : null;
    }
    adminState.selectedRun = adminState.runs.find((run) => run.run_id === adminState.selectedRunId) || null;
    textSet('runs-summary', `${adminState.runs.length} run(s) visible to ${adminState.user.username}.`);
    renderRuns(adminState.runs);
    await loadEvents();
  } catch (error) {
    textSet('runs-summary', 'Failed to load runs.');
    renderRuns([]);
  }
}

function renderRuns(runs) {
  const container = document.getElementById('admin-run-list');
  if (!container) return;
  container.innerHTML = '';
  if (!runs.length) {
    container.innerHTML = '<div class="text-secondary small">No runs available for this user and filter set.</div>';
    updateSelectedRunMeta();
    return;
  }
  runs.forEach((run) => {
    const item = document.createElement('button');
    item.type = 'button';
    item.className = `admin-run-item ${run.run_id === adminState.selectedRunId ? 'active' : ''}`;
    item.onclick = () => {
      adminState.selectedRunId = run.run_id;
      adminState.selectedRun = run;
      renderRuns(runs);
      loadEvents();
    };
    item.innerHTML = `
      <div class="d-flex justify-content-between align-items-start gap-2">
        <div>
          <div class="admin-run-title">${escapeHtml(run.project || '(unknown)')}</div>
          <div class="admin-run-meta">${escapeHtml(run.run_id)}</div>
        </div>
        <span class="badge ${run.completed ? 'bg-success-subtle text-success-emphasis' : 'bg-warning-subtle text-warning-emphasis'}">${run.completed ? 'Completed' : 'Active'}</span>
      </div>
      <div class="admin-run-grid">
        <span><strong>Engine:</strong> ${escapeHtml(run.engine || 'unknown')}</span>
        <span><strong>Events:</strong> ${Number(run.event_count || 0)}</span>
        <span><strong>Artifacts:</strong> ${run.has_artifacts ? 'yes' : 'no'}</span>
        <span><strong>Owner:</strong> ${escapeHtml(run.owner_username || 'anonymous')}</span>
      </div>
    `;
    container.appendChild(item);
  });
  updateSelectedRunMeta();
}

async function loadEvents() {
  const refreshEvents = document.getElementById('events-refresh');
  if (refreshEvents) refreshEvents.disabled = !adminState.selectedRunId;
  if (!adminState.user || !adminState.selectedRunId) {
    renderEvents([], []);
    return;
  }
  const params = new URLSearchParams();
  appendParam(params, 'event_type', valueOf('filter-event-type'));
  appendParam(params, 'q', valueOf('filter-event-query'));
  try {
    const response = await fetch(`/api/admin/runs/${encodeURIComponent(adminState.selectedRunId)}/events?${params.toString()}`);
    const data = await response.json();
    if (!response.ok) throw new Error(data.detail || 'Failed to load events');
    textSet('events-summary', `${data.total || 0} event(s) matched for ${adminState.selectedRunId}.`);
    renderEvents(Array.isArray(data.events) ? data.events : [], Array.isArray(data.event_types) ? data.event_types : []);
  } catch (error) {
    textSet('events-summary', String(error));
    renderEvents([], []);
  }
}

function renderEvents(events, eventTypes) {
  const container = document.getElementById('admin-event-list');
  if (!container) return;
  container.innerHTML = '';
  syncEventTypeFilter(eventTypes);
  updateSelectedRunMeta();
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
    item.innerHTML = `
      <div class="d-flex justify-content-between align-items-center gap-2 mb-2">
        <span class="badge bg-info-subtle text-info-emphasis">${escapeHtml(String(event.type || 'event'))}</span>
        <span class="admin-run-meta">${escapeHtml(String(event.timestamp || `#${index + 1}`))}</span>
      </div>
      <pre class="admin-event-pre">${escapeHtml(JSON.stringify(event, null, 2))}</pre>
    `;
    container.appendChild(item);
  });
}

function updateSelectedRunMeta() {
  const run = adminState.selectedRun;
  textSet('selected-run-meta', run ? `${run.project || '(unknown)'} • ${run.run_id} • owner ${run.owner_username || 'anonymous'}` : 'Select a run to inspect events');
  const deleteBtn = document.getElementById('delete-run');
  if (deleteBtn) deleteBtn.disabled = !run || !run.completed;
}

async function deleteSelectedRun() {
  if (!adminState.selectedRun) return;
  if (!window.confirm(`Delete run ${adminState.selectedRun.run_id}?`)) return;
  const response = await fetch(`/api/admin/runs/${encodeURIComponent(adminState.selectedRun.run_id)}`, { method: 'DELETE' });
  const data = await response.json();
  if (!response.ok) {
    window.alert(data.detail || 'Delete failed');
    return;
  }
  adminState.selectedRunId = null;
  adminState.selectedRun = null;
  await loadRuns();
}

async function onPackageUpload(event) {
  event.preventDefault();
  const input = document.getElementById('package-file');
  const status = document.getElementById('package-upload-status');
  if (!input || !input.files || !input.files[0]) {
    textSet('package-upload-status', 'Choose a zip file first.');
    return;
  }
  const form = new FormData();
  form.append('package', input.files[0]);
  textSet('package-upload-status', 'Uploading package...');
  try {
    const response = await fetch('/api/admin/packages/upload', {
      method: 'POST',
      body: form,
    });
    const data = await response.json();
    if (!response.ok) throw new Error(data.detail || 'Upload failed');
    textSet('package-upload-status', `Installed ${data.slug} for ${data.owner_username}.`);
    renderPackagePreview(data.preview || data);
    input.value = '';
    await loadPackages();
  } catch (error) {
    textSet('package-upload-status', String(error));
  }
}

async function loadPackages() {
  if (!adminState.user) return;
  try {
    const response = await fetch('/api/admin/packages');
    const data = await response.json();
    if (!response.ok) throw new Error(data.detail || 'Failed to load packages');
    adminState.packages = Array.isArray(data.packages) ? data.packages : [];
    renderPackages(adminState.packages);
  } catch (error) {
    renderPackages([]);
    renderPackagePreview({ error: String(error) });
  }
}

function renderPackages(packages) {
  const container = document.getElementById('package-list');
  if (!container) return;
  container.innerHTML = '';
  if (!packages.length) {
    container.innerHTML = '<div class="text-secondary small">No packages visible for this user yet.</div>';
    return;
  }
  packages.forEach((item) => {
    const preview = item.preview || {};
    const node = document.createElement('div');
    node.className = 'admin-event-item';
    node.innerHTML = `
      <div class="d-flex justify-content-between align-items-center gap-2 mb-2">
        <div>
          <div class="admin-run-title">${escapeHtml(item.name || item.slug)}</div>
          <div class="admin-run-meta">${escapeHtml(item.slug)} • ${escapeHtml(item.owner_username || '')}</div>
        </div>
        <span class="badge bg-primary-subtle text-primary-emphasis">${escapeHtml(item.status || 'active')}</span>
      </div>
      <div class="admin-run-grid">
        <span><strong>Version:</strong> ${escapeHtml(item.version || '-')}</span>
        <span><strong>Traffic:</strong> ${Number(item.traffic_count || 0)}</span>
        <span><strong>Restarts:</strong> ${Number(item.restart_count || 0)}</span>
        <span><strong>Up Since:</strong> ${escapeHtml(item.uploaded_at || '-')}</span>
        <span><strong>Last Run:</strong> ${escapeHtml(item.last_run_at || '-')}</span>
        <span><strong>Capabilities:</strong> ${escapeHtml((preview.capabilities || []).join(', ') || 'none')}</span>
      </div>
      <pre class="admin-event-pre">${escapeHtml(JSON.stringify(item, null, 2))}</pre>
    `;
    container.appendChild(node);
  });
}

function renderPackagePreview(preview) {
  const container = document.getElementById('package-preview');
  if (!container) return;
  container.innerHTML = `<pre class="admin-event-pre">${escapeHtml(JSON.stringify(preview, null, 2))}</pre>`;
}

async function loadUsers() {
  if (!adminState.user || adminState.user.role !== 'admin') {
    renderUsers([]);
    return;
  }
  try {
    const response = await fetch('/api/admin/users');
    const data = await response.json();
    if (!response.ok) throw new Error(data.detail || 'Failed to load users');
    renderUsers(Array.isArray(data.users) ? data.users : []);
  } catch (error) {
    renderUsers([{ username: 'error', role: String(error) }]);
  }
}

function renderUsers(users) {
  const container = document.getElementById('user-list');
  if (!container) return;
  container.innerHTML = '';
  if (!users.length) {
    container.innerHTML = '<div class="text-secondary small">No users to show.</div>';
    return;
  }
  users.forEach((user) => {
    const node = document.createElement('div');
    node.className = 'admin-event-item';
    node.innerHTML = `
      <div class="admin-run-title">${escapeHtml(user.display_name || user.username || '')}</div>
      <div class="admin-run-grid">
        <span><strong>Tenant:</strong> ${escapeHtml(user.tenant_name || '')}</span>
        <span><strong>Username:</strong> ${escapeHtml(user.username || '')}</span>
        <span><strong>Email:</strong> ${escapeHtml(user.email || '')}</span>
        <span><strong>Role:</strong> ${escapeHtml(user.role || '')}</span>
        <span><strong>Created:</strong> ${escapeHtml(user.created_at || '')}</span>
        <span><strong>Active:</strong> ${String(user.active ?? true)}</span>
      </div>
    `;
    container.appendChild(node);
  });
}

async function onUserCreate(event) {
  event.preventDefault();
  const payload = {
    tenant_name: valueOf('new-tenant-name'),
    username: valueOf('new-username'),
    email: valueOf('new-email'),
    display_name: valueOf('new-display-name'),
    password: valueOf('new-password'),
    role: valueOf('new-role') || 'developer',
  };
  try {
    const response = await fetch('/api/admin/users', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(payload),
    });
    const data = await response.json();
    if (!response.ok) throw new Error(data.detail || 'Failed to create user');
    textSet('user-create-status', `Created user ${data.username}.`);
    event.target.reset();
    await loadUsers();
  } catch (error) {
    textSet('user-create-status', String(error));
  }
}

function bindRefreshOnInput(id, fn) {
  const el = document.getElementById(id);
  if (!el) return;
  el.addEventListener('input', debounce(fn, 200));
  el.addEventListener('change', () => fn());
}

function clickBind(id, fn) {
  const el = document.getElementById(id);
  if (el) el.onclick = () => fn();
}

function syncEventTypeFilter(eventTypes) {
  const select = document.getElementById('filter-event-type');
  if (!select) return;
  const current = select.value;
  const values = [''].concat(eventTypes || []);
  select.innerHTML = values.map((value) => `<option value="${escapeHtml(value)}">${escapeHtml(value || 'All')}</option>`).join('');
  if (values.includes(current)) select.value = current;
}

function toggleClass(id, className, enabled) {
  const el = document.getElementById(id);
  if (!el) return;
  el.classList.toggle(className, enabled);
}

function textSet(id, value) {
  const el = document.getElementById(id);
  if (el) el.textContent = value;
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
