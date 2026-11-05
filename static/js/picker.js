/** Click-to-set location map. Used by the station forms and as the SOS fallback. */
window.MesSecPicker = (() => {
  'use strict';

  function attach(element, latInput, lngInput, { onPick } = {}) {
    const lat = parseFloat(latInput.value);
    const lng = parseFloat(lngInput.value);
    const hasValue = Number.isFinite(lat) && Number.isFinite(lng);
    const map = L.map(element).setView(hasValue ? [lat, lng] : [6.5244, 3.3792], hasValue ? 15 : 11);
    L.tileLayer('https://tile.openstreetmap.org/{z}/{x}/{y}.png', {
      maxZoom: 19,
      className: 'tiles-dark',
      attribution: '&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a>',
    }).addTo(map);

    let marker = hasValue ? L.marker([lat, lng]).addTo(map) : null;

    function set(latitude, longitude) {
      latInput.value = latitude.toFixed(6);
      lngInput.value = longitude.toFixed(6);
      if (marker) marker.setLatLng([latitude, longitude]);
      else marker = L.marker([latitude, longitude]).addTo(map);
      onPick?.(latitude, longitude);
    }

    map.on('click', e => set(e.latlng.lat, e.latlng.lng));
    return { map, set, center: (a, b) => { map.setView([a, b], 16); set(a, b); } };
  }

  /** Wire up the shared station form. */
  document.addEventListener('DOMContentLoaded', () => {
    const element = document.getElementById('station-map');
    if (!element) return;
    const picker = attach(element, document.getElementById('lat'), document.getElementById('lng'));
    document.getElementById('use-my-location')?.addEventListener('click', () => {
      if (!navigator.geolocation) { MesSec.notify('This browser cannot share its location.', 'error'); return; }
      navigator.geolocation.getCurrentPosition(
        p => picker.center(p.coords.latitude, p.coords.longitude),
        () => MesSec.notify('Could not get your location. Click the map instead.', 'error'),
        { enableHighAccuracy: true, timeout: 10000 },
      );
    });
  });

  return { attach };
})();
