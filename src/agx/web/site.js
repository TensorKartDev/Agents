'use strict';

document.addEventListener('DOMContentLoaded', () => {
  hydrateVersionBadges();
});

function hydrateVersionBadges() {
  const nodes = Array.from(document.querySelectorAll('[data-agx-version]'));
  if (!nodes.length) return;
  fetch('/api/meta')
    .then((res) => (res.ok ? res.json() : null))
    .then((data) => {
      const version = data && data.version ? `v${data.version}` : 'v0.0.0';
      nodes.forEach((node) => {
        node.textContent = version;
      });
    })
    .catch(() => {});
}
