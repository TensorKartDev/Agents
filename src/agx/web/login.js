'use strict';

let googleScriptPromise = null;

document.addEventListener('DOMContentLoaded', async () => {
  bindLoginEvents();
  await ensureLoggedOutView();
  await loadProviders();
});

function bindLoginEvents() {
  const form = document.getElementById('shared-login-form');
  if (form) form.addEventListener('submit', onLoginSubmit);

  const toggle = document.getElementById('toggle-password');
  if (toggle) {
    toggle.onclick = () => {
      const input = document.getElementById('shared-password');
      if (!input) return;
      const nextType = input.type === 'password' ? 'text' : 'password';
      input.type = nextType;
      toggle.textContent = nextType === 'password' ? 'Show' : 'Hide';
    };
  }
}

async function ensureLoggedOutView() {
  try {
    const response = await fetch('/api/auth/me');
    if (!response.ok) return;
    window.location.replace(nextPath());
  } catch (_) {}
}

async function loadProviders() {
  const container = document.getElementById('shared-provider-list');
  if (!container) return;
  try {
    const response = await fetch('/api/auth/providers');
    const data = await response.json();
    const providers = Array.isArray(data.providers) ? data.providers : [];
    container.innerHTML = '';
    for (const provider of providers) {
      if (provider.name === 'google' && provider.flow === 'fedcm') {
        await renderGoogleProvider(container, provider);
        continue;
      }
      renderProviderCard(container, provider);
    }
  } catch (error) {
    container.innerHTML = `<div class="text-danger small">${escapeHtml(String(error))}</div>`;
  }
}

function renderProviderCard(container, provider) {
  const wrapper = document.createElement('div');
  wrapper.className = 'login-provider-wrap';
  const node = document.createElement(provider.enabled === false ? 'div' : 'a');
  node.className = `login-provider-button ${provider.enabled === false ? 'disabled' : ''}`;
  node.innerHTML = providerIcon(provider.name);
  if (provider.enabled === false) {
    wrapper.appendChild(node);
    wrapper.appendChild(providerReason(provider.reason || 'Unavailable'));
  } else {
    node.href = `/auth/oauth/${encodeURIComponent(provider.name)}/login?next=${encodeURIComponent(nextPath())}`;
    wrapper.appendChild(node);
  }
  container.appendChild(wrapper);
}

async function renderGoogleProvider(container, provider) {
  const wrapper = document.createElement('div');
  wrapper.className = 'login-provider-wrap login-provider-google-wrap';
  container.appendChild(wrapper);

  if (provider.enabled === false) {
    const disabled = document.createElement('div');
    disabled.className = 'login-provider-button disabled';
    disabled.innerHTML = providerIcon('google');
    wrapper.appendChild(disabled);
    wrapper.appendChild(providerReason(provider.reason || 'Google sign-in unavailable'));
    return;
  }

  if (!browserSupportsFedCM()) {
    renderGoogleRedirectFallback(wrapper, provider, 'FedCM is not available in this browser. Using Google OAuth redirect.');
    return;
  }

  if (!provider.fedcm_enabled || !provider.client_id) {
    renderGoogleRedirectFallback(wrapper, provider, 'Google FedCM is not available on this deployment. Using Google OAuth redirect.');
    return;
  }

  const host = document.createElement('div');
  host.className = 'login-google-button-host';
  wrapper.appendChild(host);

  try {
    await ensureGoogleIdentityScript();
    if (!window.google || !window.google.accounts || !window.google.accounts.id) {
      throw new Error('Google Identity Services did not initialize');
    }
    window.google.accounts.id.initialize({
      client_id: provider.client_id,
      callback: onGoogleCredential,
      auto_select: false,
      cancel_on_tap_outside: true,
      use_fedcm_for_prompt: true,
    });
    window.google.accounts.id.renderButton(host, {
      type: 'standard',
      theme: 'outline',
      size: 'large',
      text: 'continue_with',
      shape: 'rectangular',
      logo_alignment: 'left',
      width: 240,
      use_fedcm_for_button: true,
    });
    try {
      window.google.accounts.id.prompt();
    } catch (_) {}
  } catch (error) {
    host.remove();
    renderGoogleRedirectFallback(wrapper, provider, `Google FedCM failed to load: ${String(error)}. Using Google OAuth redirect.`);
  }
}

function renderGoogleRedirectFallback(wrapper, provider, message) {
  if (provider.redirect_enabled) {
    const link = document.createElement('a');
    link.className = 'login-provider-button login-provider-google-fallback';
    link.href = `/auth/oauth/google/login?next=${encodeURIComponent(nextPath())}`;
    link.innerHTML = providerIcon('google');
    wrapper.appendChild(link);
    if (message) wrapper.appendChild(providerReason(message));
    return;
  }
  const disabled = document.createElement('div');
  disabled.className = 'login-provider-button disabled';
  disabled.innerHTML = providerIcon('google');
  wrapper.appendChild(disabled);
  wrapper.appendChild(providerReason(message || provider.reason || 'Google sign-in unavailable'));
}

function providerReason(text) {
  const reason = document.createElement('div');
  reason.className = 'small text-secondary mt-2 text-center';
  reason.textContent = text;
  return reason;
}

async function onLoginSubmit(event) {
  event.preventDefault();
  const status = document.getElementById('shared-login-status');
  try {
    const response = await fetch('/api/auth/login', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        username: valueOf('shared-username'),
        password: valueOf('shared-password'),
      }),
    });
    const data = await response.json();
    if (!response.ok) throw new Error(data.detail || 'Login failed');
    setStatus(`Authenticated as ${data.username}. Redirecting...`);
    window.location.replace(nextPath());
  } catch (error) {
    setStatus(String(error), true);
  }
}

async function onGoogleCredential(response) {
  if (!response || !response.credential) {
    setStatus('Google sign-in did not return a credential.', true);
    return;
  }
  try {
    const loginResponse = await fetch('/api/auth/google/fedcm', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        credential: response.credential,
        select_by: response.select_by || '',
      }),
    });
    const data = await loginResponse.json();
    if (!loginResponse.ok) throw new Error(data.detail || 'Google sign-in failed');
    const name = data.display_name || data.username || 'Google user';
    setStatus(`Authenticated as ${name}. Redirecting...`);
    window.location.replace(nextPath());
  } catch (error) {
    setStatus(String(error), true);
  }
}

function ensureGoogleIdentityScript() {
  if (window.google && window.google.accounts && window.google.accounts.id) {
    return Promise.resolve();
  }
  if (googleScriptPromise) {
    return googleScriptPromise;
  }
  googleScriptPromise = new Promise((resolve, reject) => {
    const existing = document.querySelector('script[data-agx-google-gsi="true"]');
    if (existing) {
      existing.addEventListener('load', () => resolve(), { once: true });
      existing.addEventListener('error', () => reject(new Error('Failed to load Google script')), { once: true });
      return;
    }
    const script = document.createElement('script');
    script.src = 'https://accounts.google.com/gsi/client';
    script.async = true;
    script.defer = true;
    script.dataset.agxGoogleGsi = 'true';
    script.onload = () => resolve();
    script.onerror = () => reject(new Error('Failed to load Google script'));
    document.head.appendChild(script);
  });
  return googleScriptPromise;
}

function browserSupportsFedCM() {
  return typeof window !== 'undefined'
    && typeof navigator !== 'undefined'
    && typeof navigator.credentials !== 'undefined'
    && typeof navigator.credentials.get === 'function'
    && typeof window.IdentityCredential !== 'undefined';
}

function nextPath() {
  const params = new URLSearchParams(window.location.search);
  const next = params.get('next') || '/';
  return next.startsWith('/') ? next : '/';
}

function valueOf(id) {
  const el = document.getElementById(id);
  return el ? String(el.value || '').trim() : '';
}

function setStatus(message, isError = false) {
  const status = document.getElementById('shared-login-status');
  if (!status) return;
  status.textContent = message;
  status.classList.toggle('text-danger', isError);
  status.classList.toggle('text-secondary', !isError);
}

function providerIcon(name) {
  if (name === 'google') {
    return `
      <svg class="login-provider-svg" viewBox="0 0 24 24" aria-hidden="true">
        <path fill="#EA4335" d="M12 10.2v3.9h5.5c-.2 1.3-1.5 3.9-5.5 3.9-3.3 0-6-2.7-6-6s2.7-6 6-6c1.9 0 3.1.8 3.8 1.5l2.6-2.5C16.8 3.5 14.7 2.5 12 2.5A9.5 9.5 0 1 0 12 21.5c5.5 0 9.1-3.8 9.1-9.2 0-.6-.1-1.1-.2-1.6z"/>
        <path fill="#34A853" d="M3.9 7.9l3.2 2.3C8 8.1 9.8 6.9 12 6.9c1.9 0 3.1.8 3.8 1.5l2.6-2.5C16.8 3.5 14.7 2.5 12 2.5c-3.6 0-6.8 2-8.4 5.4z"/>
        <path fill="#FBBC05" d="M12 21.5c2.6 0 4.7-.8 6.3-2.3l-2.9-2.4c-.8.6-1.9 1.1-3.4 1.1-3.9 0-5.2-2.6-5.5-3.8l-3.2 2.5c1.6 3.3 4.9 4.9 8.7 4.9z"/>
        <path fill="#4285F4" d="M3.5 12c0-1 .2-2 .5-2.9L.8 6.6A9.7 9.7 0 0 0 0 12c0 1.9.5 3.7 1.3 5.3l3.2-2.5A6 6 0 0 1 3.5 12z"/>
      </svg>
    `;
  }
  if (name === 'github') {
    return `
      <svg class="login-provider-svg" viewBox="0 0 24 24" aria-hidden="true">
        <path fill="currentColor" d="M12 .5a12 12 0 0 0-3.8 23.4c.6.1.8-.3.8-.6v-2.2c-3.3.7-4-1.4-4-1.4-.6-1.4-1.3-1.8-1.3-1.8-1.1-.7.1-.7.1-.7 1.2.1 1.9 1.2 1.9 1.2 1.1 1.8 2.8 1.3 3.5 1 .1-.8.4-1.3.8-1.7-2.7-.3-5.5-1.3-5.5-6A4.7 4.7 0 0 1 6.6 8c-.1-.3-.5-1.5.1-3.1 0 0 1-.3 3.3 1.2a11.4 11.4 0 0 1 6 0c2.3-1.5 3.3-1.2 3.3-1.2.6 1.6.2 2.8.1 3.1a4.7 4.7 0 0 1 1.2 3.2c0 4.7-2.8 5.7-5.5 6 .5.4.9 1.1.9 2.3v3.4c0 .3.2.7.8.6A12 12 0 0 0 12 .5z"/>
      </svg>
    `;
  }
  return '<i class="bi bi-person-circle"></i>';
}

function escapeHtml(value) {
  return String(value)
    .replaceAll('&', '&amp;')
    .replaceAll('<', '&lt;')
    .replaceAll('>', '&gt;')
    .replaceAll('"', '&quot;')
    .replaceAll("'", '&#39;');
}
