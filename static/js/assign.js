/** The dialog a dispatcher uses to choose which registered station takes an incident. */
window.MesSecAssign = (() => {
  'use strict';
  const { api, esc, notify } = window.MesSec;

  function row(station) {
    const unavailable = station.is_current || station.declined;
    const tags = [
      station.recommended && '<span class="pick-tag rec">Recommended</span>',
      station.is_current && '<span class="pick-tag">Current</span>',
      station.declined && '<span class="pick-tag bad">Declined</span>',
      !station.suitable && '<span class="pick-tag muted-tag">Other type</span>',
      station.active_count > 0 && `<span class="pick-tag warn">${station.active_count} open</span>`,
    ].filter(Boolean).join('');
    return `
      <button type="button" class="pick ${station.recommended ? 'is-rec' : ''}" data-station="${station.id}" ${station.is_current ? 'disabled' : ''}
              ${unavailable && !station.is_current ? 'data-was-declined="1"' : ''}>
        <span class="pick-main">
          <strong>${esc(station.name)}</strong>
          <small>${esc(station.type_label)} · ${esc(station.zone)} · ${esc(station.phone)}</small>
          <span class="pick-tags">${tags}</span>
        </span>
        <span class="pick-dist"><b>${station.distance_km} km</b><small>est. ${station.eta_minutes} min</small></span>
      </button>`;
  }

  async function open(signalId, { onAssigned } = {}) {
    const dialog = document.createElement('dialog');
    dialog.className = 'dialog dialog-wide';
    dialog.innerHTML = `
      <div class="dialog-form">
        <h2>Assign ${esc(signalId)} to a station</h2>
        <p class="muted">Sorted by straight-line distance. ETAs are estimates.</p>
        <div class="pick-list" data-list><p class="muted">Loading stations…</p></div>
        <div class="dialog-actions"><button type="button" class="btn btn-ghost" data-cancel>Cancel</button></div>
      </div>`;
    document.body.appendChild(dialog);
    const close = () => { dialog.close(); dialog.remove(); };
    dialog.querySelector('[data-cancel]').addEventListener('click', close);
    dialog.addEventListener('cancel', close);
    dialog.showModal();

    const list = dialog.querySelector('[data-list]');
    try {
      const { stations } = await api(`/api/signals/${encodeURIComponent(signalId)}/candidates`);
      list.innerHTML = stations.length
        ? stations.map(row).join('')
        : '<p class="muted">No approved stations yet. An admin can approve or add stations on the Stations page.</p>';
    } catch (err) {
      list.innerHTML = `<p class="field-error">${esc(err.message)}</p>`;
      return;
    }

    list.addEventListener('click', async e => {
      const button = e.target.closest('[data-station]');
      if (!button || button.disabled) return;
      if (button.dataset.wasDeclined && !window.confirm('This station already declined this incident. Assign it anyway?')) return;
      list.querySelectorAll('button').forEach(b => { b.disabled = true; });
      try {
        await api(`/api/signals/${encodeURIComponent(signalId)}/assign`, { station_id: Number(button.dataset.station) });
        notify(`${signalId} assigned to ${button.querySelector('strong').textContent}.`, 'success');
        close();
        onAssigned?.();
      } catch (err) {
        notify(err.message, 'error');
        close();
      }
    });
  }

  return { open };
})();
