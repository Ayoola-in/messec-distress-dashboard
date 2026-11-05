/**
 * MesSec — Emergency Signal Command · dashboard.js
 * GET  /api/state               initial snapshot
 * POST /api/signals/simulate     inject simulated signal (demo mode)
 * ws   incident_update | stations_update | auto_assign_failed
 */
(() => {
  'use strict';

  // ── Constants ────────────────────────────────────────────────────────────
  const SEV = ['CRITICAL', 'HIGH', 'MEDIUM', 'LOW'];
  const SEV_COLOR = { CRITICAL: '#ff4d5e', HIGH: '#ff9f43', MEDIUM: '#4cc3ff', LOW: '#3ddc97' };
  const SEV_LABEL = { CRITICAL: 'Critical', HIGH: 'High', MEDIUM: 'Medium', LOW: 'Low' };
  const MAX_RECORDS = 400;
  const HOURS = 12;

  const CLOSED = 'resolved';
  const isOpen = s => s.status !== CLOSED;
  const isActive = s => s.status !== CLOSED && s.status !== 'unassigned';
  const statusClass = s => (s.status === CLOSED ? 'resolved' : s.status === 'unassigned' ? 'pending' : 'assigned');
  const show = (value, unit = '') => (value === null || value === undefined ? '—' : `${value}${unit}`);

  const $ = (s, r = document) => r.querySelector(s);
  const $$ = (s, r = document) => [...r.querySelectorAll(s)];
  const esc = v => String(v ?? '').replace(/[&<>"']/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
  const icon = id => `<svg class="ic"><use href="#i-${id}"/></svg>`;
  const fmtTime = iso => new Date(iso).toLocaleTimeString('en-GB', { hour: '2-digit', minute: '2-digit', second: '2-digit' });
  const ago = iso => {
    const s = Math.max(0, Math.round((Date.now() - new Date(iso).getTime()) / 1000));
    if (s < 60) return `${s}s ago`;
    const m = Math.floor(s / 60);
    if (m < 60) return `${m}m ago`;
    return `${Math.floor(m / 60)}h ${m % 60}m ago`;
  };

  // ── State ────────────────────────────────────────────────────────────────
  const store = {
    records: new Map(),       // signal_id -> { signal, assignment }
    units: [],
    stats: { total_signals: 0, unassigned: 0, active: 0, resolved: 0 },
    ackSeconds: 120,
    filter: 'all',
    query: '',
    focusId: null,
    newIds: new Set(),
    showRoutes: true,
    showUnits: true,
    sound: localStorage.getItem('messec.sound') === '1',
    lastCritical: null,
  };

  // ── Map ──────────────────────────────────────────────────────────────────
  const map = L.map('map', { zoomControl: false, attributionControl: true, preferCanvas: false })
    .setView([6.5244, 3.3792], 11);

  L.tileLayer('https://tile.openstreetmap.org/{z}/{x}/{y}.png', {
    maxZoom: 19, className: 'tiles-dark',
    attribution: '&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a>',
  }).addTo(map);
  L.control.zoom({ position: 'bottomright' }).addTo(map);
  map.attributionControl.setPrefix(false);

  const layers = {
    signals: L.layerGroup().addTo(map),
    routes: L.layerGroup().addTo(map),
    units: L.layerGroup().addTo(map),
  };
  const markers = { signal: {}, route: {}, unit: {} };

  const signalIcon = (sev, focus) => L.divIcon({
    className: '',
    html: `<div class="mk sev-${sev}${focus ? ' is-focus' : ''}"><i class="mk-ring"></i><i class="mk-ring r2"></i><i class="mk-dot"></i></div>`,
    iconSize: [28, 28], iconAnchor: [14, 14], popupAnchor: [0, -14],
  });
  const unitIcon = status => L.divIcon({
    className: '',
    html: `<div class="mk-unit ${status === 'available' ? '' : 'busy'}"><b></b></div>`,
    iconSize: [26, 26], iconAnchor: [13, 13], popupAnchor: [0, -14],
  });

  function signalPopup({ signal: s, assignment: a }) {
    const sp = a?.station;
    return `
      <div class="pop-head">
        <div class="pop-kicker"><span class="sev ${esc(s.severity)}">${esc(s.severity)}</span>${esc(s.id)}</div>
        <div class="pop-title">${esc(s.type)}</div>
      </div>
      <div class="pop-body">
        <div><span>Assigned</span><strong>${esc(sp?.name ?? 'Unassigned')}</strong></div>
        <div><span>Est. ETA</span><strong>${show(a?.eta_minutes, ' min')}</strong></div>
        <div><span>Distance</span><strong>${show(a?.distance_km, ' km')}</strong></div>
        <div><span>Caller</span><strong>${esc(s.citizen?.name ?? s.caller_id ?? '—')}</strong></div>
        <div><span>Frequency</span><strong>${show(s.frequency, ' MHz')}</strong></div>
        <div><span>Strength</span><strong>${show(s.signal_strength, '%')}</strong></div>
        <div style="grid-column:1/-1"><span>GPS</span><strong>${s.lat.toFixed(5)}, ${s.lng.toFixed(5)}</strong></div>
      </div>`;
  }

  function unitPopup(u) {
    const busy = u.status !== 'available';
    const active = [...store.records.values()].find(r => isActive(r.signal) && r.assignment?.station?.id === u.id);
    return `
      <div class="pop-head">
        <div class="pop-kicker"><span class="tag ${busy ? 'busy' : 'ok'}">${busy ? 'Responding' : 'Available'}</span>${esc(u.code)}</div>
        <div class="pop-title">${esc(u.name)}</div>
      </div>
      <div class="pop-body">
        <div><span>Type</span><strong>${esc(u.type_label)}</strong></div>
        <div><span>Zone</span><strong>${esc(u.zone)}</strong></div>
        <div><span>Phone</span><strong>${esc(u.phone)}</strong></div>
        <div><span>Tasking</span><strong>${active ? esc(active.signal.id) : '—'}</strong></div>
        <div style="grid-column:1/-1"><span>Position</span><strong>${u.lat.toFixed(4)}, ${u.lng.toFixed(4)}</strong></div>
      </div>`;
  }

  function drawSignal(rec) {
    const { signal: s, assignment: a } = rec;
    if (s.status === 'resolved') { eraseSignal(s.id); return; }
    if (!markers.signal[s.id]) {
      const m = L.marker([s.lat, s.lng], { icon: signalIcon(s.severity), zIndexOffset: s.severity === 'CRITICAL' ? 1000 : 0 })
        .bindPopup(() => signalPopup(rec), { closeButton: true, autoPanPadding: [40, 40] })
        .on('click', () => setFocus(s.id, { fly: false }))
        .addTo(layers.signals);
      markers.signal[s.id] = m;
    }
    // The route follows the station currently responsible, so redraw it on reassignment.
    if (markers.route[s.id] && markers.route[s.id].stationId !== a?.station?.id) {
      layers.routes.removeLayer(markers.route[s.id]);
      delete markers.route[s.id];
    }
    if (a?.station && !markers.route[s.id]) {
      const sp = a.station;
      markers.route[s.id] = L.polyline([[sp.lat, sp.lng], [s.lat, s.lng]], {
        color: SEV_COLOR[s.severity], weight: 1.6, opacity: 0.75, dashArray: '6 5', className: 'route-line',
      }).addTo(layers.routes);
      markers.route[s.id].stationId = sp.id;
    }
    markers.signal[s.id].setPopupContent(signalPopup(rec));
  }

  function eraseSignal(id) {
    if (markers.signal[id]) { layers.signals.removeLayer(markers.signal[id]); delete markers.signal[id]; }
    if (markers.route[id]) { layers.routes.removeLayer(markers.route[id]); delete markers.route[id]; }
  }

  function drawUnits() {
    const known = new Set(store.units.map(u => String(u.id)));
    Object.keys(markers.unit).forEach(id => {
      if (!known.has(id)) { layers.units.removeLayer(markers.unit[id]); delete markers.unit[id]; }
    });
    store.units.forEach(u => {
      const m = markers.unit[u.id];
      if (m) { m.setIcon(unitIcon(u.status)); m.setPopupContent(unitPopup(u)); return; }
      markers.unit[u.id] = L.marker([u.lat, u.lng], { icon: unitIcon(u.status), zIndexOffset: -100 })
        .bindPopup(unitPopup(u))
        .bindTooltip(u.code, { permanent: true, direction: 'right', offset: [10, 0], className: 'mk-label' })
        .addTo(layers.units);
    });
  }

  function fitAll() {
    const pts = [...store.units.map(u => [u.lat, u.lng]),
      ...[...store.records.values()].filter(r => isOpen(r.signal)).map(r => [r.signal.lat, r.signal.lng])];
    if (pts.length) map.flyToBounds(L.latLngBounds(pts).pad(0.12), { duration: 0.8, maxZoom: 13 });
  }

  // ── Records ──────────────────────────────────────────────────────────────
  function upsert(signal, assignment) {
    const existing = store.records.get(signal.id);
    if (existing) { existing.signal = signal; existing.assignment = assignment; }
    else store.records.set(signal.id, { signal, assignment });
    if (store.records.size > MAX_RECORDS) {
      const oldest = store.records.keys().next().value;
      eraseSignal(oldest);
      store.records.delete(oldest);
    }
  }
  const allRecords = () => [...store.records.values()].reverse(); // newest first

  function matches(rec) {
    const { signal: s, assignment: a } = rec;
    const f = store.filter;
    if (f === 'unassigned' && s.status !== 'unassigned') return false;
    if (f === 'active' && !isActive(s)) return false;
    if (f === 'resolved' && s.status !== CLOSED) return false;
    if (SEV.includes(f) && s.severity !== f) return false;
    if (store.query) {
      const hay = `${s.id} ${s.type} ${s.severity} ${s.caller_id} ${s.citizen?.name ?? ''} ${a?.station?.name ?? ''} ${a?.station?.zone ?? ''} ${a?.station?.code ?? ''}`.toLowerCase();
      if (!hay.includes(store.query)) return false;
    }
    return true;
  }

  // ── Queue ────────────────────────────────────────────────────────────────
  function progressOf(rec) {
    const { signal: s, assignment: a } = rec;
    if (s.status === CLOSED) return 100;
    if (!a?.eta_minutes) return 0;
    const elapsed = (Date.now() - new Date(a.assigned_at).getTime()) / 1000;
    return Math.min(96, (elapsed / (a.eta_minutes * 60)) * 100);
  }

  function ackOverdue(rec) {
    const { signal: s, assignment: a } = rec;
    if (s.status !== 'assigned' || !a?.assigned_at) return false;
    return Date.now() - new Date(a.assigned_at).getTime() > store.ackSeconds * 1000;
  }

  function countdownText(iso) {
    const seconds = Math.ceil((new Date(iso).getTime() - Date.now()) / 1000);
    return seconds > 0 ? `Auto-assigns in ${seconds}s` : 'Auto-assigning…';
  }

  function incNotice(rec) {
    const s = rec.signal;
    if (s.auto_assign_failed) return `<div class="inc-notice crit">${icon('alert')}No station available to take this. Register or approve a station.</div>`;
    if (s.auto_assign_at) return `<div class="inc-notice warn">${icon('clock')}<span data-countdown="${esc(s.auto_assign_at)}">${countdownText(s.auto_assign_at)}</span></div>`;
    if (ackOverdue(rec)) return `<div class="inc-notice warn">${icon('alert')}No response from the station yet. Phone them or reassign.</div>`;
    return '';
  }

  function incCard(rec) {
    const { signal: s, assignment: a } = rec;
    const sp = a?.station;
    const resolved = s.status === CLOSED;
    const unassigned = s.status === 'unassigned';
    const cls = ['inc', `sev-${s.severity}`, resolved && 'is-resolved', store.newIds.has(s.id) && 'is-new', store.focusId === s.id && 'is-focus'].filter(Boolean).join(' ');
    return `
      <article class="${cls}" data-id="${esc(s.id)}" tabindex="0">
        <div class="inc-top">
          <div>
            <div class="inc-title">${esc(s.type)}${s.is_simulated ? ' <span class="sim-tag">SIMULATED</span>' : ''}</div>
            <div class="inc-sub">${esc(s.id)} · ${esc(s.citizen?.name ?? s.caller_id ?? 'Unknown caller')}</div>
          </div>
          <span class="sev ${esc(s.severity)}">${esc(s.severity)}</span>
        </div>
        <div class="inc-grid">
          <div><span>Station</span><strong>${esc(sp?.name ?? 'Unassigned')}</strong></div>
          <div><span>Distance</span><strong class="mono">${show(a?.distance_km, ' km')}</strong></div>
          <div><span>Est. ETA</span><strong class="mono">${show(a?.eta_minutes, ' min')}</strong></div>
        </div>
        ${unassigned ? '' : `<div class="progress"><span data-progress="${esc(s.id)}" style="width:${progressOf(rec)}%"></span></div>`}
        ${incNotice(rec)}
        <div class="inc-foot">
          <span class="where">${icon('pin')}${esc(sp?.zone ?? `${s.lat.toFixed(3)}, ${s.lng.toFixed(3)}`)}</span>
          <span data-ago="${esc(s.timestamp)}">${ago(s.timestamp)}</span>
          <span class="status ${statusClass(s)}">${esc(s.status_label)}</span>
        </div>
        <div class="inc-actions">
          ${resolved ? '' : `<button class="btn-sm ${unassigned ? 'btn-sm-primary' : ''}" type="button" data-assign="${esc(s.id)}">${unassigned ? 'Assign station' : 'Reassign'}</button>`}
          <a class="btn-sm" href="/incidents/${encodeURIComponent(s.id)}">Details</a>
        </div>
      </article>`;
  }

  function renderQueue() {
    // Incidents nobody has taken come first; within that, the most severe, then the newest.
    const rank = r => (r.signal.status === 'unassigned' ? 0 : isOpen(r.signal) ? 1 : 2);
    const list = allRecords().filter(matches).sort((x, y) =>
      rank(x) - rank(y) || SEV.indexOf(x.signal.severity) - SEV.indexOf(y.signal.severity));
    const q = $('#queue');
    // Keep keyboard focus on the same button when the list is rebuilt.
    const focused = document.activeElement;
    const refocus = q.contains(focused) && focused.dataset.assign ? focused.dataset.assign : null;
    q.innerHTML = list.length
      ? list.map(incCard).join('')
      : `<div class="empty">${icon('signal')}<p>${store.records.size ? 'No signals match this view.' : 'Listening for distress signals…'}</p></div>`;
    $('#queue-count').textContent = list.length;
    if (refocus) q.querySelector(`[data-assign="${CSS.escape(refocus)}"]`)?.focus();
  }

  // ── Log table ────────────────────────────────────────────────────────────
  function strength(v) {
    if (v === null || v === undefined) return '—';
    const bars = v >= 85 ? 4 : v >= 70 ? 3 : v >= 55 ? 2 : 1;
    return `<span class="str">${[1, 2, 3, 4].map(i => `<i class="${i <= bars ? 'on' : ''}"></i>`).join('')}</span>${v}`;
  }

  function renderLog() {
    const list = allRecords().filter(matches);
    $('#log-body').innerHTML = list.map(({ signal: s, assignment: a }) => {
      const resolved = s.status === CLOSED;
      const cls = [resolved && 'is-resolved', store.newIds.has(s.id) && 'is-new', store.focusId === s.id && 'is-focus'].filter(Boolean).join(' ');
      return `<tr class="${cls}" data-id="${esc(s.id)}">
        <td class="id">${esc(s.id)}</td>
        <td><span class="sev ${esc(s.severity)}">${esc(s.severity)}</span></td>
        <td>${esc(s.type)}${s.is_simulated ? ' <span class="sim-tag">SIM</span>' : ''}</td>
        <td class="dim">${esc(a?.station?.name ?? '—')}</td>
        <td class="num">${show(a?.distance_km, ' km')}</td>
        <td class="num">${show(a?.eta_minutes, ' min')}</td>
        <td class="num">${show(s.frequency)}</td>
        <td class="num">${strength(s.signal_strength)}</td>
        <td><span class="status ${statusClass(s)}">${esc(s.status_label)}</span></td>
        <td class="num">${fmtTime(s.timestamp)}</td>
      </tr>`;
    }).join('');
    $('#log-count').textContent = list.length;
  }

  // ── Units panel ──────────────────────────────────────────────────────────
  function renderUnits() {
    $('#unit-list').innerHTML = store.units.map(u => {
      const busy = u.status !== 'available';
      return `<div class="unit ${busy ? 'busy' : ''}" data-unit="${esc(u.id)}" tabindex="0">
        <div class="unit-ava">${esc(u.code.replace('ST-', ''))}</div>
        <div>
          <div class="unit-name">${esc(u.name)}</div>
          <div class="unit-zone">${icon('pin')}${esc(u.type_label)} · ${esc(u.zone)}</div>
        </div>
        <span class="tag ${busy ? 'busy' : 'ok'}">${busy ? `Busy (${u.active_count})` : 'Available'}</span>
      </div>`;
    }).join('') || `<div class="empty"><p>No approved stations yet.</p></div>`;
    $('#units-count').textContent = store.units.length;
    $('#unit-pips').innerHTML = store.units.map(u => `<i class="${u.status === 'available' ? '' : 'busy'}" title="${esc(u.name)}"></i>`).join('');
  }

  function renderSevMix() {
    const counts = Object.fromEntries(SEV.map(k => [k, 0]));
    store.records.forEach(r => { if (r.signal.severity in counts) counts[r.signal.severity]++; });
    const total = store.records.size;
    const max = Math.max(1, ...Object.values(counts));
    $('#sev-mix').innerHTML = SEV.map(k => `
      <div class="sev-row" style="--c:${SEV_COLOR[k]}">
        <span class="name"><i></i>${SEV_LABEL[k]}</span>
        <span class="bar"><span style="width:${(counts[k] / max) * 100}%"></span></span>
        <span class="val">${counts[k]}</span>
      </div>`).join('');
    $('#sev-total').textContent = `${total} signal${total === 1 ? '' : 's'}`;
  }

  // ── KPIs ─────────────────────────────────────────────────────────────────
  const tweens = {};
  function tween(id, to) {
    const el = document.getElementById(id);
    if (!el) return;
    const from = parseFloat(el.dataset.v ?? el.textContent) || 0;
    el.dataset.v = to;
    if (from === to) { el.textContent = to; return; }
    cancelAnimationFrame(tweens[id]);
    const t0 = performance.now(), dur = 600;
    const step = now => {
      const p = Math.min(1, (now - t0) / dur), e = 1 - Math.pow(1 - p, 3);
      el.textContent = Math.round(from + (to - from) * e);
      if (p < 1) tweens[id] = requestAnimationFrame(step);
    };
    tweens[id] = requestAnimationFrame(step);
  }

  function hourBuckets() {
    const now = new Date();
    const labels = [], keys = [];
    for (let i = HOURS - 1; i >= 0; i--) {
      const d = new Date(now); d.setMinutes(0, 0, 0); d.setHours(d.getHours() - i);
      labels.push(`${String(d.getHours()).padStart(2, '0')}:00`);
      keys.push(d.getTime());
    }
    const series = Object.fromEntries(SEV.map(k => [k, new Array(HOURS).fill(0)]));
    store.records.forEach(({ signal: s }) => {
      const t = new Date(s.timestamp); t.setMinutes(0, 0, 0);
      const idx = keys.indexOf(t.getTime());
      if (idx !== -1 && series[s.severity]) series[s.severity][idx]++;
    });
    const totals = keys.map((_, i) => SEV.reduce((n, k) => n + series[k][i], 0));
    return { labels, series, totals };
  }

  function renderSpark(totals) {
    const svg = $('#spark-total');
    const max = Math.max(1, ...totals);
    const pts = totals.map((v, i) => [(i / (totals.length - 1)) * 100, 26 - (v / max) * 22]);
    const d = pts.map((p, i) => `${i ? 'L' : 'M'}${p[0].toFixed(1)},${p[1].toFixed(1)}`).join(' ');
    svg.innerHTML = `
      <defs><linearGradient id="sparkGrad" x1="0" y1="0" x2="0" y2="1">
        <stop offset="0" stop-color="#ff7a59" stop-opacity=".35"/><stop offset="1" stop-color="#ff7a59" stop-opacity="0"/>
      </linearGradient></defs>
      <path class="area" d="${d} L100,28 L0,28 Z"/>
      <path class="line" d="${d}"/>`;
  }

  function renderKpis() {
    const recs = [...store.records.values()];
    const open = recs.filter(r => isOpen(r.signal));
    const critical = open.filter(r => r.signal.severity === 'CRITICAL').length;
    const total = store.stats.total_signals;
    const resolved = store.stats.resolved;
    const openCount = store.stats.unassigned + store.stats.active;
    const free = store.units.filter(u => u.status === 'available').length;

    tween('kpi-total', total);
    tween('kpi-active', openCount);
    tween('kpi-resolved', resolved);
    tween('kpi-units', free);
    $('#kpi-units-of').textContent = `/ ${store.units.length}`;

    $('#kpi-critical').innerHTML = `<b>${store.stats.unassigned}</b> unassigned · <b>${critical}</b> critical`;
    $('#meter-active').style.width = `${store.units.length ? Math.min(100, (openCount / store.units.length) * 100) : 0}%`;
    const rate = total ? Math.round((resolved / total) * 100) : 0;
    $('#kpi-rate').innerHTML = `<b>${rate}%</b> resolution rate`;
    $('#meter-resolved').style.width = `${rate}%`;

    const withEta = open.filter(r => r.assignment?.eta_minutes != null);
    if (withEta.length) {
      const avgEta = withEta.reduce((n, r) => n + r.assignment.eta_minutes, 0) / withEta.length;
      const avgDist = withEta.reduce((n, r) => n + (r.assignment.distance_km || 0), 0) / withEta.length;
      $('#kpi-eta').textContent = avgEta.toFixed(1);
      $('#kpi-dist').innerHTML = `<b>${avgDist.toFixed(1)} km</b> avg distance`;
    } else {
      $('#kpi-eta').textContent = '—';
      $('#kpi-dist').innerHTML = '— km avg distance';
    }

    const { totals } = hourBuckets();
    $('#kpi-total-delta').innerHTML = `<b>+${totals[totals.length - 1]}</b> this hour`;
    renderSpark(totals);
  }

  // ── Chart ────────────────────────────────────────────────────────────────
  Chart.defaults.font.family = "'Geist', system-ui, sans-serif";
  Chart.defaults.color = '#6a7385';
  const chart = new Chart($('#chart'), {
    type: 'bar',
    data: {
      labels: [],
      datasets: SEV.slice().reverse().map(k => ({
        label: SEV_LABEL[k], data: [], stack: 's',
        backgroundColor: SEV_COLOR[k] + 'cc', hoverBackgroundColor: SEV_COLOR[k],
        borderRadius: 3, borderSkipped: false, barPercentage: 0.62, categoryPercentage: 0.9,
      })),
    },
    options: {
      responsive: true, maintainAspectRatio: false, animation: { duration: 500 },
      interaction: { mode: 'index', intersect: false },
      plugins: {
        legend: { display: false },
        tooltip: {
          backgroundColor: 'rgba(16,20,29,0.96)', borderColor: 'rgba(255,255,255,0.1)', borderWidth: 1,
          titleColor: '#eceef3', bodyColor: '#a3abba', padding: 10, cornerRadius: 10, boxPadding: 4,
          titleFont: { family: "'Geist Mono', monospace", size: 11, weight: '500' }, bodyFont: { size: 12 },
          usePointStyle: true,
          filter: i => i.raw > 0,
        },
      },
      scales: {
        x: { stacked: true, grid: { display: false }, border: { display: false }, ticks: { font: { family: "'Geist Mono', monospace", size: 10 }, maxRotation: 0, autoSkipPadding: 8 } },
        y: { stacked: true, beginAtZero: true, grid: { color: 'rgba(255,255,255,0.05)' }, border: { display: false }, ticks: { precision: 0, font: { size: 10 }, maxTicksLimit: 5 } },
      },
    },
  });

  function renderChart() {
    const { labels, series } = hourBuckets();
    chart.data.labels = labels;
    SEV.slice().reverse().forEach((k, i) => { chart.data.datasets[i].data = series[k]; });
    chart.update();
  }

  // ── Render orchestration ─────────────────────────────────────────────────
  let raf = 0;
  function render() {
    if (raf) return;
    raf = requestAnimationFrame(() => {
      raf = 0;
      renderQueue(); renderLog(); renderUnits(); renderSevMix(); renderKpis(); renderChart();
    });
  }

  // ── Focus ────────────────────────────────────────────────────────────────
  function setFocus(id, { fly = true } = {}) {
    const rec = store.records.get(id);
    if (!rec) return;
    const prev = store.focusId;
    store.focusId = id;
    if (prev && markers.signal[prev]) {
      const p = store.records.get(prev);
      if (p) markers.signal[prev].setIcon(signalIcon(p.signal.severity));
    }
    const m = markers.signal[id];
    if (m) m.setIcon(signalIcon(rec.signal.severity, true));
    if (fly) {
      const target = [rec.signal.lat, rec.signal.lng];
      if (m) {
        map.flyTo(target, Math.max(map.getZoom(), 13), { duration: 0.8 });
        map.once('moveend', () => m.openPopup());
      } else {
        L.popup({ autoPanPadding: [40, 40] }).setLatLng(target).setContent(signalPopup(rec)).openOn(map);
        map.flyTo(target, Math.max(map.getZoom(), 13), { duration: 0.8 });
      }
    }
    $$('.inc.is-focus, #log-body tr.is-focus').forEach(el => el.classList.remove('is-focus'));
    $$(`[data-id="${CSS.escape(id)}"]`).forEach(el => el.classList.add('is-focus'));
    const card = $(`.inc[data-id="${CSS.escape(id)}"]`);
    card?.scrollIntoView({ block: 'nearest', behavior: 'smooth' });
  }

  function clearFocus() {
    if (!store.focusId) return;
    const p = store.records.get(store.focusId);
    if (p && markers.signal[store.focusId]) markers.signal[store.focusId].setIcon(signalIcon(p.signal.severity));
    store.focusId = null;
    $$('.is-focus').forEach(el => el.classList.remove('is-focus'));
    map.closePopup();
  }

  // ── Alerts: toast, banner, vignette, sound ───────────────────────────────
  const TOAST_IC = { CRITICAL: 'alert', HIGH: 'alert', MEDIUM: 'pulse', LOW: 'signal' };
  function toast({ signal: s, assignment: a }) {
    const box = $('#toasts');
    const el = document.createElement('div');
    el.className = `toast sev-${s.severity}`;
    el.innerHTML = `
      <div class="toast-ic">${icon(TOAST_IC[s.severity])}</div>
      <div>
        <div class="toast-k">${SEV_LABEL[s.severity]} · ${esc(s.id)}</div>
        <div class="toast-t">${esc(s.type)}</div>
        <div class="toast-s">${a?.station ? `${esc(a.station.name)} assigned` : 'Needs a station'}</div>
      </div>
      <div class="toast-bar"></div>`;
    el.addEventListener('click', () => { setFocus(s.id); dismiss(); });
    box.appendChild(el);
    while (box.children.length > 4) box.firstElementChild.remove();
    let gone = false;
    function dismiss() {
      if (gone) return; gone = true;
      el.classList.add('out');
      setTimeout(() => el.remove(), 300);
    }
    setTimeout(dismiss, 6000);
  }

  function critical(rec) {
    store.lastCritical = rec.signal.id;
    $('#alert-title').textContent = `Critical: ${rec.signal.type}`;
    const station = rec.assignment?.station?.name;
    $('#alert-sub').textContent = `${rec.signal.id} · ${station ? `${station} assigned` : 'Needs a station now'}`;
    $('#alert-banner').hidden = false;
    const v = $('#vignette');
    v.classList.remove('on'); void v.offsetWidth; v.classList.add('on');
    requestAnimationFrame(() => map.invalidateSize());
  }

  function beep(sev) {
    if (store.sound) MesSec.beep(sev);
  }

  function setSound(on) {
    store.sound = on;
    localStorage.setItem('messec.sound', on ? '1' : '0');
    const b = $('#btn-sound');
    b.setAttribute('aria-pressed', String(on));
    b.querySelector('use').setAttribute('href', on ? '#i-volume' : '#i-mute');
    b.querySelector('.rail-tip').textContent = on ? 'Alert sound: on' : 'Alert sound: off';
  }

  // ── Socket handlers ──────────────────────────────────────────────────────
  function onIncident({ signal, assignment, is_new, stats, ack_seconds }) {
    if (stats) store.stats = stats;
    if (ack_seconds) store.ackSeconds = ack_seconds;
    const before = store.records.get(signal.id)?.signal.status;
    upsert(signal, assignment);
    const rec = store.records.get(signal.id);
    drawSignal(rec);
    if (is_new) {
      store.newIds.add(signal.id);
      setTimeout(() => store.newIds.delete(signal.id), 1500);
      toast(rec);
      beep(signal.severity);
      if (signal.severity === 'CRITICAL') critical(rec);
    } else if (before && before !== 'unassigned' && signal.status === 'unassigned') {
      MesSec.notify(`${signal.id} is back in the queue and needs a station.`, 'warn');
      beep(signal.severity);
    }
    if (signal.status !== 'unassigned' && store.lastCritical === signal.id) $('#alert-banner').hidden = true;
    if (signal.status === CLOSED && store.focusId === signal.id) store.focusId = null;
    render();
  }

  function onStations({ stations }) {
    store.units = stations;
    drawUnits();
    render();
  }

  // ── Boot ─────────────────────────────────────────────────────────────────
  async function boot() {
    try {
      const res = await fetch('/api/state');
      if (res.status === 401) { window.location.href = '/login'; return; }
      const data = await res.json();
      store.stats = data.stats;
      store.ackSeconds = data.ack_seconds;
      store.units = data.stations;
      data.records.forEach(r => upsert(r.signal, r.assignment));
      drawUnits();
      store.records.forEach(drawSignal);
      render();
      setTimeout(fitAll, 300);
    } catch (err) {
      console.error('Failed to load initial state', err);
    }
  }

  const socket = io();
  const conn = $('#conn');
  // Reload on every reconnect: events sent while offline are not replayed.
  let everConnected = false;
  socket.on('connect', () => {
    conn.dataset.state = 'live'; $('#conn-label').textContent = 'Live';
    if (everConnected) boot();
    everConnected = true;
  });
  socket.on('disconnect', () => { conn.dataset.state = 'offline'; $('#conn-label').textContent = 'Offline'; });
  socket.on('incident_update', onIncident);
  socket.on('stations_update', onStations);
  socket.on('auto_assign_failed', ({ signal_id, type }) => {
    MesSec.notify(`${signal_id} (${type}): no station is available to take it.`, 'error');
    beep('CRITICAL');
  });

  // ── Clock + ticking UI ───────────────────────────────────────────────────
  function tick() {
    const n = new Date();
    const p = v => String(v).padStart(2, '0');
    $('#clock-time').textContent = `${p(n.getHours())}:${p(n.getMinutes())}:${p(n.getSeconds())}`;
    const off = -n.getTimezoneOffset() / 60;
    $('#clock-sub').textContent = `${n.toLocaleDateString('en-GB', { weekday: 'short', day: '2-digit', month: 'short' })} · UTC${off >= 0 ? '+' : ''}${off}`;
    $$('[data-ago]').forEach(el => { el.textContent = ago(el.dataset.ago); });
    $$('[data-progress]').forEach(el => {
      const r = store.records.get(el.dataset.progress);
      if (r) el.style.width = `${progressOf(r)}%`;
    });
    $$('[data-countdown]').forEach(el => { el.textContent = countdownText(el.dataset.countdown); });
  }
  setInterval(tick, 1000); tick();
  // "No response" notices appear with time, not with an event.
  setInterval(renderQueue, 15_000);
  // Re-bucket the chart at the top of each hour.
  setInterval(() => { renderChart(); renderKpis(); }, 60_000);

  // ── Interactions ─────────────────────────────────────────────────────────
  async function inject() {
    const b = $('#btn-manual');
    if (!b) return;
    b.classList.remove('is-firing'); void b.offsetWidth; b.classList.add('is-firing');
    b.disabled = true;
    try { await MesSec.api('/api/signals/simulate', {}); }
    catch (err) { MesSec.notify(err.message, 'error'); }
    finally { setTimeout(() => { b.disabled = false; }, 350); }
  }
  $('#btn-manual')?.addEventListener('click', inject);

  $('#queue').addEventListener('click', e => {
    const assignBtn = e.target.closest('[data-assign]');
    if (assignBtn) { MesSecAssign.open(assignBtn.dataset.assign); return; }
    if (e.target.closest('a')) return;
    const c = e.target.closest('.inc'); if (c) setFocus(c.dataset.id);
  });
  $('#queue').addEventListener('keydown', e => {
    if (e.key !== 'Enter' || e.target.closest('a, button')) return;
    const c = e.target.closest('.inc'); if (c) setFocus(c.dataset.id);
  });
  $('#log-body').addEventListener('click', e => { const r = e.target.closest('tr'); if (r) setFocus(r.dataset.id); });
  $('#unit-list').addEventListener('click', e => {
    const u = e.target.closest('.unit'); if (!u) return;
    const m = markers.unit[u.dataset.unit]; if (!m) return;
    if (!store.showUnits) toggleUnits();
    map.flyTo(m.getLatLng(), 14, { duration: 0.8 });
    map.once('moveend', () => m.openPopup());
  });

  $('#queue-tabs').addEventListener('click', e => {
    const t = e.target.closest('.tab'); if (!t) return;
    $$('.tab', $('#queue-tabs')).forEach(x => x.classList.toggle('is-active', x === t));
    store.filter = t.dataset.filter;
    render();
  });

  const search = $('#search');
  search.addEventListener('input', () => { store.query = search.value.trim().toLowerCase(); render(); });

  function toggleRoutes() {
    store.showRoutes = !store.showRoutes;
    $('#tool-routes').classList.toggle('is-on', store.showRoutes);
    store.showRoutes ? layers.routes.addTo(map) : map.removeLayer(layers.routes);
  }
  function toggleUnits() {
    store.showUnits = !store.showUnits;
    $('#tool-units').classList.toggle('is-on', store.showUnits);
    store.showUnits ? layers.units.addTo(map) : map.removeLayer(layers.units);
  }
  $('#tool-fit').addEventListener('click', fitAll);
  $('#tool-routes').addEventListener('click', toggleRoutes);
  $('#tool-units').addEventListener('click', toggleUnits);

  function setView(v) {
    $('#workspace').dataset.view = v;
    $$('.rail-btn[data-view]').forEach(b => b.classList.toggle('is-active', b.dataset.view === v));
    setTimeout(() => { map.invalidateSize(); chart.resize(); }, 30);
  }
  $$('.rail-btn[data-view]').forEach(b => b.addEventListener('click', () => setView(b.dataset.view)));

  $('#btn-sound').addEventListener('click', () => { setSound(!store.sound); if (store.sound) beep('LOW'); });
  setSound(store.sound);

  $('#alert-close').addEventListener('click', () => { $('#alert-banner').hidden = true; requestAnimationFrame(() => map.invalidateSize()); });
  $('#alert-locate').addEventListener('click', () => { if (store.lastCritical) setFocus(store.lastCritical); });

  document.addEventListener('keydown', e => {
    if (e.target === search) {
      if (e.key === 'Escape') { search.value = ''; store.query = ''; search.blur(); render(); }
      return;
    }
    if (e.target.matches('input, textarea, select') || e.target.closest('dialog') || e.metaKey || e.ctrlKey || e.altKey) return;
    const k = e.key.toLowerCase();
    if (k === '/') { e.preventDefault(); search.focus(); }
    else if (k === 'i') inject();
    else if (k === 'f') fitAll();
    else if (k === 'r') toggleRoutes();
    else if (k === 'u') toggleUnits();
    else if (k === 's') $('#btn-sound').click();
    else if (k === '1') setView('ops');
    else if (k === '2') setView('map');
    else if (k === '3') setView('log');
    else if (k === 'escape') clearFocus();
  });

  new ResizeObserver(() => map.invalidateSize()).observe($('.p-map'));

  boot();
})();
