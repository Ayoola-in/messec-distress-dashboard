/** Citizen portal: the SOS button and the live status of the person's alert. */
(() => {
  'use strict';
  const { api, esc, notify } = window.MesSec;
  const $ = s => document.querySelector(s);
  const HOLD_MS = 1500;

  const button = $('#sos-button');
  const latInput = $('#sos-lat');
  const lngInput = $('#sos-lng');
  const fallback = $('#sos-fallback');
  let picker = null;
  let holdTimer = null;
  let sending = false;

  function showFallback(message) {
    fallback.hidden = false;
    $('#sos-fallback-text').textContent = message;
    if (!picker) picker = MesSecPicker.attach($('#sos-map'), latInput, lngInput);
    picker.map.invalidateSize();
  }

  function position() {
    return new Promise((resolve, reject) => {
      if (!navigator.geolocation) { reject(new Error('unsupported')); return; }
      navigator.geolocation.getCurrentPosition(
        p => resolve([p.coords.latitude, p.coords.longitude]),
        reject,
        { enableHighAccuracy: true, timeout: 10000, maximumAge: 30000 },
      );
    });
  }

  async function send() {
    if (sending) return;
    sending = true;
    button.classList.remove('is-holding');
    button.disabled = true;
    $('#sos-label').textContent = 'Sending…';
    try {
      let lat = parseFloat(latInput.value);
      let lng = parseFloat(lngInput.value);
      if (!Number.isFinite(lat) || !Number.isFinite(lng)) {
        try {
          [lat, lng] = await position();
        } catch {
          showFallback('We couldn’t get your location automatically. Tap where you are on the map, then hold the button again.');
          return;
        }
      }
      const result = await api('/api/sos', {
        lat, lng, type: $('#sos-type').value, description: $('#sos-description').value.trim(),
      });
      notify(result.already_open ? 'Your alert is already open. Help has been requested.' : 'Alert sent. Stay safe and keep your phone with you.', 'success');
      fallback.hidden = true;
      renderActive(result.alert);
    } catch (err) {
      notify(`${err.message} If you are in danger, call 112.`, 'error');
    } finally {
      sending = false;
      button.disabled = false;
      $('#sos-label').textContent = 'Hold for SOS';
    }
  }

  function startHold(e) {
    e.preventDefault();
    if (button.disabled) return;
    button.classList.add('is-holding');
    holdTimer = setTimeout(send, HOLD_MS);
  }
  function endHold() {
    clearTimeout(holdTimer);
    button.classList.remove('is-holding');
  }
  button.addEventListener('pointerdown', startHold);
  // A long press on a phone opens a menu, which would cancel the hold.
  button.addEventListener('contextmenu', e => e.preventDefault());
  ['pointerup', 'pointerleave', 'pointercancel'].forEach(name => button.addEventListener(name, endHold));
  // Keyboard users: hold Space or Enter.
  button.addEventListener('keydown', e => {
    if ((e.key === ' ' || e.key === 'Enter') && !e.repeat) startHold(e);
  });
  button.addEventListener('keyup', endHold);

  const STEP_TEXT = {
    unassigned: 'Your alert has been received and a dispatcher is finding a station.',
    assigned: 'A station has been alerted and is being asked to respond.',
    accepted: 'The station has accepted your alert.',
    en_route: 'Responders are on their way.',
    on_scene: 'Responders have arrived.',
  };

  function renderActive(alert) {
    const box = $('#active-alert');
    if (!alert || alert.status === 'resolved') {
      box.hidden = true;
      $('#sos-panel').hidden = false;
      return;
    }
    $('#sos-panel').hidden = true;
    box.hidden = false;
    const station = alert.station;
    box.innerHTML = `
      <div class="active-kicker"><span class="live-dot"></span>${esc(alert.id)} · ${esc(alert.type)}</div>
      <h2>${esc(alert.status_label)}</h2>
      <p>${esc(STEP_TEXT[alert.status] ?? '')}</p>
      ${station ? `<p class="active-station"><strong>${esc(station.name)}</strong> (${esc(station.type_label)})<br>
        <a class="btn btn-ghost" href="tel:${esc(station.phone)}">Call ${esc(station.phone)}</a></p>` : ''}
      ${alert.eta_minutes ? `<p class="muted">Rough estimate: ${alert.eta_minutes} min. Actual time may differ.</p>` : ''}
      <p class="muted">In immediate danger, also call <a href="tel:112">112</a>.</p>`;
  }

  async function refresh() {
    const { alerts } = await api('/api/me/alerts');
    renderActive(alerts.find(a => a.status !== 'resolved'));
  }

  const socket = io();
  let everConnected = false;
  socket.on('connect', () => {
    if (everConnected) refresh().catch(() => {});
    everConnected = true;
  });
  socket.on('alert_update', alert => {
    if (alert.status === 'resolved') {
      notify(`Your alert ${alert.id} has been resolved.`, 'success');
      setTimeout(() => window.location.reload(), 1500);
    }
    renderActive(alert);
  });

  refresh().catch(() => {});
})();
