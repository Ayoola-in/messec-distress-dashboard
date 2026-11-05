/** Incident detail page: map, status actions, notes. */
(() => {
  'use strict';
  const { api, ask, notify } = window.MesSec;
  const page = document.getElementById('incident');
  if (!page) return;
  const id = page.dataset.id;
  const base = `/api/signals/${encodeURIComponent(id)}`;

  const map = L.map('incident-map').setView([Number(page.dataset.lat), Number(page.dataset.lng)], 14);
  L.tileLayer('https://tile.openstreetmap.org/{z}/{x}/{y}.png', {
    maxZoom: 19, className: 'tiles-dark',
    attribution: '&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a>',
  }).addTo(map);
  L.marker([Number(page.dataset.lat), Number(page.dataset.lng)]).addTo(map);

  async function run(action) {
    try {
      await action();
      window.location.reload();
    } catch (err) {
      notify(err.message, 'error');
    }
  }

  page.addEventListener('click', async e => {
    const button = e.target.closest('button[data-action]');
    if (!button) return;
    const action = button.dataset.action;
    if (action === 'assign') {
      MesSecAssign.open(id, { onAssigned: () => window.location.reload() });
    } else if (action === 'status') {
      run(() => api(`${base}/status`, { status: button.dataset.status }));
    } else if (action === 'decline') {
      const reason = await ask({ title: 'Decline this incident', label: 'Why can’t your station take it?', confirm: 'Decline', danger: true });
      if (reason) run(() => api(`${base}/decline`, { reason }).then(() => { window.location.href = '/station'; }));
    } else if (action === 'close') {
      const note = await ask({ title: 'Close this incident', label: 'Why is it being closed?', placeholder: 'For example: false alarm, confirmed by phone', confirm: 'Close incident', danger: true });
      if (note) run(() => api(`${base}/close`, { note }));
    }
  });

  document.getElementById('note-form')?.addEventListener('submit', e => {
    e.preventDefault();
    const message = e.target.elements.message.value.trim();
    if (message) run(() => api(`${base}/notes`, { message }));
  });
})();
