/** Station console: live incidents assigned to this station, with an alarm until they're accepted. */
(() => {
  'use strict';
  const { api, ask, esc, notify, beep, ago } = window.MesSec;
  const $ = s => document.querySelector(s);

  const NEXT = {
    assigned: { status: 'accepted', label: 'Accept' },
    accepted: { status: 'en_route', label: 'Mark en route' },
    en_route: { status: 'on_scene', label: 'Mark on scene' },
    on_scene: { status: 'resolved', label: 'Mark resolved' },
  };

  const page = $('#station-console');
  const records = new Map();
  const markers = new Map();
  let soundOn = localStorage.getItem('messec.station.sound') !== '0';

  const map = L.map('station-map').setView([Number(page.dataset.lat), Number(page.dataset.lng)], 12);
  L.tileLayer('https://tile.openstreetmap.org/{z}/{x}/{y}.png', {
    maxZoom: 19, className: 'tiles-dark',
    attribution: '&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a>',
  }).addTo(map);
  L.circleMarker([Number(page.dataset.lat), Number(page.dataset.lng)], { radius: 7, color: '#a7b4ff', weight: 2 })
    .addTo(map).bindTooltip('Your station');

  function citizenBlock(citizen) {
    if (!citizen) return '';
    const contacts = citizen.emergency_contacts.map(c =>
      `<li>${esc(c.name)}${c.relationship ? ` (${esc(c.relationship)})` : ''}: <a href="tel:${esc(c.phone)}">${esc(c.phone)}</a></li>`).join('');
    return `
      <div class="person">
        <strong>${esc(citizen.name)}</strong> · <a href="tel:${esc(citizen.phone)}">${esc(citizen.phone)}</a>
        ${citizen.address ? `<div>${esc(citizen.address)}</div>` : ''}
        ${citizen.blood_group ? `<div>Blood group: ${esc(citizen.blood_group)}</div>` : ''}
        ${citizen.medical_notes ? `<div>Medical notes: ${esc(citizen.medical_notes)}</div>` : ''}
        ${contacts ? `<div class="person-sub">Emergency contacts</div><ul>${contacts}</ul>` : ''}
      </div>`;
  }

  function card({ signal: s, assignment: a }) {
    const next = NEXT[s.status];
    const directions = `https://www.openstreetmap.org/directions?to=${s.lat},${s.lng}`;
    return `
      <article class="console-card sev-${esc(s.severity)} ${s.status === 'assigned' ? 'is-alarm' : ''}" data-id="${esc(s.id)}">
        <header>
          <div>
            <h3>${esc(s.type)}${s.is_simulated ? ' <span class="sim-tag">SIMULATED</span>' : ''}</h3>
            <div class="muted"><a href="/incidents/${encodeURIComponent(s.id)}">${esc(s.id)}</a> · <span data-ago="${esc(s.timestamp)}">${ago(s.timestamp)}</span>${a?.method === 'auto' ? ' · auto-assigned' : ''}</div>
          </div>
          <span class="sev ${esc(s.severity)}">${esc(s.severity)}</span>
        </header>
        <div class="console-facts">
          <div><span>Status</span><strong>${esc(s.status_label)}</strong></div>
          <div><span>Distance</span><strong>${a?.distance_km ?? '—'} km</strong></div>
          <div><span>Est. ETA</span><strong>${a?.eta_minutes ?? '—'} min</strong></div>
          <div><span>Location</span><strong><a href="${directions}" target="_blank" rel="noopener">${s.lat.toFixed(5)}, ${s.lng.toFixed(5)}</a></strong></div>
        </div>
        ${s.description ? `<p class="console-desc">${esc(s.description)}</p>` : ''}
        ${s.citizen ? citizenBlock(s.citizen) : s.caller_id ? `<div class="person">Caller: <a href="tel:${esc(s.caller_id)}">${esc(s.caller_id)}</a></div>` : ''}
        <div class="console-actions">
          ${next ? `<button class="btn btn-primary" data-status="${next.status}">${next.label}</button>` : ''}
          ${['assigned', 'accepted'].includes(s.status) ? '<button class="btn btn-ghost" data-decline>Decline</button>' : ''}
          <button class="btn btn-ghost" data-locate>Show on map</button>
          <a class="btn btn-ghost" href="/incidents/${encodeURIComponent(s.id)}">Details and notes</a>
        </div>
      </article>`;
  }

  function render() {
    const all = [...records.values()].sort((x, y) => (x.signal.id < y.signal.id ? 1 : -1));
    const open = all.filter(r => r.signal.status !== 'resolved');
    const done = all.filter(r => r.signal.status === 'resolved');
    $('#open-list').innerHTML = open.map(card).join('')
      || '<div class="empty-state">No open incidents. New assignments appear here straight away.</div>';
    $('#open-count').textContent = open.length;
    $('#done-list').innerHTML = done.slice(0, 20).map(({ signal: s }) => `
      <li><a href="/incidents/${encodeURIComponent(s.id)}">${esc(s.id)}</a> ${esc(s.type)} <span class="muted">${ago(s.resolved_at || s.timestamp)}</span></li>`).join('')
      || '<li class="muted">Nothing resolved yet.</li>';

    const waiting = open.filter(r => r.signal.status === 'assigned').length;
    document.title = waiting ? `(${waiting}) New incident: MesSec` : 'Station console: MesSec';
    $('#alarm-bar').hidden = waiting === 0;

    markers.forEach((marker, id) => {
      if (!records.has(id) || records.get(id).signal.status === 'resolved') { marker.remove(); markers.delete(id); }
    });
    open.forEach(({ signal: s }) => {
      if (!markers.has(s.id)) markers.set(s.id, L.marker([s.lat, s.lng]).addTo(map).bindTooltip(s.id));
    });
  }

  function setSound(on) {
    soundOn = on;
    localStorage.setItem('messec.station.sound', on ? '1' : '0');
    $('#sound-toggle').textContent = on ? 'Alarm sound: on' : 'Alarm sound: off';
    if (on) beep('LOW');
  }

  async function load() {
    const data = await api('/api/station/incidents');
    records.clear();
    data.records.forEach(r => records.set(r.signal.id, r));
    render();
  }

  $('#sound-toggle').textContent = soundOn ? 'Alarm sound: on' : 'Alarm sound: off';
  $('#sound-toggle').addEventListener('click', () => setSound(!soundOn));

  $('#open-list').addEventListener('click', async e => {
    const article = e.target.closest('[data-id]');
    if (!article) return;
    const id = article.dataset.id;
    const base = `/api/signals/${encodeURIComponent(id)}`;
    try {
      if (e.target.closest('[data-status]')) {
        e.target.disabled = true;
        await api(`${base}/status`, { status: e.target.closest('[data-status]').dataset.status });
      } else if (e.target.closest('[data-decline]')) {
        const reason = await ask({ title: `Decline ${id}`, label: 'Why can’t your station take it?', placeholder: 'For example: no vehicles available', confirm: 'Decline', danger: true });
        if (reason) await api(`${base}/decline`, { reason });
      } else if (e.target.closest('[data-locate]')) {
        const { signal } = records.get(id);
        map.flyTo([signal.lat, signal.lng], 15, { duration: 0.6 });
        markers.get(id)?.openTooltip();
      }
    } catch (err) {
      notify(err.message, 'error');
      load().catch(() => {});
    }
  });

  const socket = io();
  const conn = $('#conn');
  let everConnected = false;
  socket.on('connect', () => {
    conn.dataset.state = 'live'; $('#conn-label').textContent = 'Live';
    if (everConnected) load().catch(() => {});
    everConnected = true;
  });
  socket.on('disconnect', () => { conn.dataset.state = 'offline'; $('#conn-label').textContent = 'Offline: reconnecting'; });
  socket.on('incident_update', record => {
    const isNew = !records.has(record.signal.id);
    records.set(record.signal.id, record);
    if (isNew) notify(`New incident: ${record.signal.type}`, 'warn');
    render();
    if (isNew && soundOn) beep(record.signal.severity);
  });
  socket.on('incident_removed', ({ signal_id }) => {
    if (records.delete(signal_id)) notify(`${signal_id} was moved off your station.`, 'info');
    render();
  });

  // Keep sounding until someone accepts; a single beep is easy to miss across a room.
  setInterval(() => {
    const waiting = [...records.values()].filter(r => r.signal.status === 'assigned');
    if (soundOn && waiting.length) beep('CRITICAL');
  }, 15000);
  setInterval(() => {
    document.querySelectorAll('[data-ago]').forEach(el => { el.textContent = ago(el.dataset.ago); });
  }, 1000);

  load().catch(err => notify(err.message, 'error'));
})();
