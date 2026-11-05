/** Helpers shared by every MesSec page. */
window.MesSec = (() => {
  'use strict';

  const csrf = () => document.querySelector('meta[name="csrf-token"]')?.content ?? '';
  const esc = v => String(v ?? '').replace(/[&<>"']/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));

  /** POST JSON when a body is given, otherwise GET. Throws an Error with a readable message. */
  async function api(path, body) {
    let response;
    try {
      response = await fetch(path, body === undefined ? {} : {
        method: 'POST',
        headers: { 'Content-Type': 'application/json', 'X-CSRFToken': csrf() },
        body: JSON.stringify(body),
      });
    } catch {
      throw new Error("Can't reach the server. Check your connection and try again.");
    }
    if (response.status === 401) {
      window.location.href = `/login?next=${encodeURIComponent(window.location.pathname)}`;
      throw new Error('Please log in again.');
    }
    const data = await response.json().catch(() => ({}));
    if (!response.ok) throw new Error(data.error || 'Something went wrong. Please try again.');
    return data;
  }

  function notify(message, kind = 'info') {
    let box = document.getElementById('notices');
    if (!box) {
      box = document.createElement('div');
      box.id = 'notices';
      box.className = 'notices';
      box.setAttribute('aria-live', 'assertive');
      document.body.appendChild(box);
    }
    const el = document.createElement('div');
    el.className = `notice notice-${kind}`;
    el.textContent = message;
    box.appendChild(el);
    setTimeout(() => el.remove(), kind === 'error' ? 9000 : 6000);
  }

  let audioCtx;
  function beep(severity = 'HIGH') {
    try {
      audioCtx = audioCtx || new (window.AudioContext || window.webkitAudioContext)();
      const tones = severity === 'CRITICAL' ? [880, 660, 880] : severity === 'HIGH' ? [740, 740] : [620];
      tones.forEach((frequency, i) => {
        const oscillator = audioCtx.createOscillator();
        const gain = audioCtx.createGain();
        const start = audioCtx.currentTime + i * 0.16;
        oscillator.type = 'sine';
        oscillator.frequency.value = frequency;
        gain.gain.setValueAtTime(0, start);
        gain.gain.linearRampToValueAtTime(0.12, start + 0.02);
        gain.gain.exponentialRampToValueAtTime(0.0001, start + 0.14);
        oscillator.connect(gain).connect(audioCtx.destination);
        oscillator.start(start);
        oscillator.stop(start + 0.15);
      });
    } catch { /* audio unavailable */ }
  }

  /** Ask for a line of text in a dialog. Resolves to the text, or null if cancelled. */
  function ask({ title, label, placeholder = '', confirm = 'Confirm', required = true, danger = false }) {
    return new Promise(resolve => {
      const dialog = document.createElement('dialog');
      dialog.className = 'dialog';
      dialog.innerHTML = `
        <form method="dialog" class="dialog-form">
          <h2>${esc(title)}</h2>
          <label class="field"><span>${esc(label)}</span>
            <textarea rows="3" maxlength="500" placeholder="${esc(placeholder)}" ${required ? 'required' : ''}></textarea>
          </label>
          <div class="dialog-actions">
            <button type="button" class="btn btn-ghost" data-cancel>Cancel</button>
            <button type="submit" class="btn ${danger ? 'btn-danger' : 'btn-primary'}">${esc(confirm)}</button>
          </div>
        </form>`;
      document.body.appendChild(dialog);
      const finish = value => { dialog.close(); dialog.remove(); resolve(value); };
      dialog.querySelector('[data-cancel]').addEventListener('click', () => finish(null));
      dialog.addEventListener('cancel', e => { e.preventDefault(); finish(null); });
      dialog.querySelector('form').addEventListener('submit', e => {
        e.preventDefault();
        finish(dialog.querySelector('textarea').value.trim());
      });
      dialog.showModal();
    });
  }

  const ago = iso => {
    const seconds = Math.max(0, Math.round((Date.now() - new Date(iso).getTime()) / 1000));
    if (seconds < 60) return `${seconds}s ago`;
    const minutes = Math.floor(seconds / 60);
    if (minutes < 60) return `${minutes}m ago`;
    const hours = Math.floor(minutes / 60);
    if (hours < 48) return `${hours}h ${minutes % 60}m ago`;
    return `${Math.floor(hours / 24)}d ago`;
  };

  // Forms with data-confirm ask before submitting; times with data-local show in the viewer's zone.
  document.addEventListener('submit', e => {
    const message = e.target.dataset?.confirm;
    if (message && !window.confirm(message)) e.preventDefault();
  });
  document.addEventListener('DOMContentLoaded', () => {
    document.querySelectorAll('[data-local-time]').forEach(el => {
      el.textContent = new Date(el.dataset.localTime).toLocaleString('en-GB', {
        day: '2-digit', month: 'short', year: 'numeric', hour: '2-digit', minute: '2-digit',
      });
    });
    document.querySelectorAll('[data-copy]').forEach(button => {
      button.addEventListener('click', async () => {
        const text = document.querySelector(button.dataset.copy)?.textContent?.trim() ?? '';
        try { await navigator.clipboard.writeText(text); notify('Copied.', 'success'); }
        catch { notify('Copy failed. Select the text and copy it by hand.', 'error'); }
      });
    });
  });

  return { api, esc, notify, beep, ask, ago };
})();
