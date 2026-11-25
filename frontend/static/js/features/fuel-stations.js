/* EVbro — Repostajes → «Gasolineras cercanas»: the stations around the car (or around the part of the map the user moved to) with
 * the price of one fuel, on a map (each station a pill with its price, green the cheapest, red the dearest) and as a list, cheapest
 * first. Data: GET /api/vehicles/{vin}/fuel-stations — the Ministry's list (backend services/fuel_stations.py).
 * (Classic script: shares the global scope with the other files; see the load order in templates/index.html.)
 */
(function () {
  'use strict';

  const $ = (id) => document.getElementById(id);
  const STORE = 'evbro_fuel';
  const RADIUS_KM = 5;
  const LIST_MAX = 8;
  const PILLS_MAX = 10;
  let map = null;
  let layer = null;
  let markers = {};
  let centre = null;            // {lat, lon} chosen with «Buscar en esta zona»; null = around the car
  let lastVin = null;
  let busy = false;

  function fuel() {
    try { return localStorage.getItem(STORE) || 'g95e5'; } catch (e) { return 'g95e5'; }
  }

  function setFuel(value) {
    try { localStorage.setItem(STORE, value); } catch (e) { /* not kept: the default next time */ }
    load();
  }

  const price = (p) => p.toLocaleString((window.I18n && I18n.locale) || 'es-ES', { minimumFractionDigits: 3, maximumFractionDigits: 3 });
  const km = (d) => (d < 1 ? `${Math.round(d * 1000)} m` : `${d.toLocaleString((window.I18n && I18n.locale) || 'es-ES', { maximumFractionDigits: 1 })} km`);

  /** Green for the cheapest of the area, red for the dearest, through amber. */
  function colour(p, cheapest, dearest) {
    const r = dearest > cheapest ? (p - cheapest) / (dearest - cheapest) : 0;
    return `hsl(${Math.round(130 - 130 * r)}, 70%, 40%)`;
  }

  function status(text) {
    const box = $('fsStatus');
    if (!box) return;
    box.textContent = text || '';
    box.hidden = !text;
  }

  function ensureMap() {
    if (map || !$('fsMap') || typeof L === 'undefined') return;
    map = L.map('fsMap', { zoomControl: true, attributionControl: true, scrollWheelZoom: false }).setView([40.4168, -3.7038], 13);
    createStandardTileLayer().addTo(map);
    layer = L.layerGroup().addTo(map);
    // after moving the map by hand, offer to search there
    map.on('dragend', () => { const b = $('fsHere'); if (b) b.hidden = false; });
  }

  function popupFor(s) {
    const box = document.createElement('div');
    box.className = 'fs-popup';
    const title = document.createElement('strong');
    title.textContent = s.name;
    const where = document.createElement('div');
    where.textContent = [s.address, s.town].filter(Boolean).join(', ');
    const hours = document.createElement('div');
    hours.className = 'fs-popup-hours';
    hours.textContent = s.hours || '';
    const cost = document.createElement('div');
    cost.className = 'fs-popup-price';
    cost.textContent = `${price(s.price)} €/l · ${km(s.distance_km)}`;
    const go = document.createElement('a');
    go.href = `https://www.google.com/maps/dir/?api=1&destination=${s.lat},${s.lon}`;
    go.target = '_blank';
    go.rel = 'noopener';
    go.textContent = t('Cómo llegar');
    box.append(title, where, hours, cost, go);
    return box;
  }

  function pin(s, data) {
    const el = document.createElement('span');
    el.className = 'fs-pin';
    el.textContent = price(s.price);
    el.style.backgroundColor = colour(s.price, data.cheapest, data.dearest);       // CSSOM: allowed by the CSP (no style attribute)
    return L.divIcon({ html: el, className: 'fs-pin-wrap', iconSize: null, iconAnchor: [24, 14] });
  }

  function render(data) {
    ensureMap();
    markers = {};
    if (layer) layer.clearLayers();
    const list = $('fsList');
    list.replaceChildren();
    const stations = data.stations || [];
    if (map) {
      const c = [data.center.lat, data.center.lon];
      if (data.center.from === 'car') {
        L.circleMarker(c, { radius: 8, color: '#fff', weight: 2, fillColor: themeAccent(), fillOpacity: 1 }).bindTooltip(t('Tu coche')).addTo(layer);
      }
      // the cheapest ones carry their price; the rest are a dot of their colour (a city has dozens and the pills piled up)
      const withPrice = new Set([...stations].sort((a, b) => a.price - b.price).slice(0, PILLS_MAX).map((s) => s.id));
      stations.forEach((s) => {
        markers[s.id] = withPrice.has(s.id)
          ? L.marker([s.lat, s.lon], { icon: pin(s, data), riseOnHover: true, zIndexOffset: 500 }).bindPopup(popupFor(s)).addTo(layer)
          : L.circleMarker([s.lat, s.lon], { radius: 6, color: '#fff', weight: 1.5, fillColor: colour(s.price, data.cheapest, data.dearest), fillOpacity: 0.95 })
            .bindPopup(popupFor(s)).addTo(layer);
      });
      if (stations.length) {
        map.fitBounds(L.latLngBounds([c, ...stations.slice(0, 12).map((s) => [s.lat, s.lon])]), { padding: [24, 24], maxZoom: 15 });
      } else {
        map.setView(c, 13);
      }
      setTimeout(() => map.invalidateSize(), 50);
    }
    const here = $('fsHere');
    if (here) here.hidden = true;
    if (!stations.length) {
      status(t('No hay gasolineras con ese combustible a menos de {p0} km.', { p0: data.radius_km }));
    } else {
      status('');
    }
    // the list: cheapest first (the nearest when two cost the same)
    [...stations].sort((a, b) => a.price - b.price || a.distance_km - b.distance_km).slice(0, LIST_MAX).forEach((s, i) => {
      const li = document.createElement('li');
      const btn = document.createElement('button');
      btn.type = 'button';
      btn.className = 'fs-row';
      btn.addEventListener('click', () => {
        const m = markers[s.id];
        if (map && m) { map.setView(m.getLatLng(), Math.max(map.getZoom(), 15)); m.openPopup(); }
        $('fsMap').scrollIntoView({ behavior: 'smooth', block: 'center' });
      });
      const name = document.createElement('span');
      name.className = 'fs-row-name';
      name.textContent = s.name;
      const sub = document.createElement('span');
      sub.className = 'fs-row-sub';
      sub.textContent = [s.town, km(s.distance_km)].filter(Boolean).join(' · ');
      const cost = document.createElement('strong');
      cost.className = 'fs-row-price';
      cost.textContent = `${price(s.price)} €/l`;
      cost.style.color = colour(s.price, data.cheapest, data.dearest);
      const text = document.createElement('span');
      text.className = 'fs-row-text';
      text.append(name, sub);
      btn.append(text, cost);
      if (i === 0 && stations.length > 1) btn.classList.add('is-cheapest');
      li.appendChild(btn);
      list.appendChild(li);
    });
    const source = $('fsSource');
    if (source) {
      source.textContent = data.source_time
        ? t('Precios del Ministerio para la Transición Ecológica ({p0}). Pueden no coincidir con el surtidor.', { p0: data.source_time })
        : '';
    }
  }

  async function load() {
    const card = $('fsCard');
    if (!card || !card.open || typeof currentVin === 'undefined' || !currentVin || busy) return;      // folded: nothing is asked
    if (currentVin !== lastVin) { centre = null; lastVin = currentVin; }
    const select = $('fsFuel');
    if (select) select.value = fuel();
    busy = true;
    ensureMap();
    status(t('Buscando gasolineras…'));
    try {
      const params = new URLSearchParams({ fuel: fuel(), radius_km: String(RADIUS_KM) });
      if (centre) { params.set('lat', centre.lat.toFixed(5)); params.set('lon', centre.lon.toFixed(5)); }
      const res = await fetch(`/api/vehicles/${encodeURIComponent(currentVin)}/fuel-stations?${params}`);
      const data = await res.json().catch(() => ({}));
      if (!res.ok) {
        if (layer) layer.clearLayers();
        $('fsList').replaceChildren();
        status(data.detail || t('No se pudieron cargar las gasolineras.'));
        return;
      }
      render(data);
    } catch (e) {
      status(t('No se pudieron cargar las gasolineras.'));
    } finally {
      busy = false;
    }
  }

  /** Around the car again. */
  function nearCar() {
    centre = null;
    load();
  }

  /** Around the middle of the map, after moving it. */
  function searchHere() {
    if (!map) return;
    const c = map.getCenter();
    centre = { lat: c.lat, lon: c.lng };
    load();
  }

  // the fold: closed by default, remembered on this device; opening it loads the stations (and the map gets its real size)
  const OPEN_STORE = 'evbro_fuel_open';
  function wireFold() {
    const card = $('fsCard');
    if (!card) return;
    try { card.open = localStorage.getItem(OPEN_STORE) === '1'; } catch (e) { /* closed */ }
    card.addEventListener('toggle', () => {
      try { localStorage.setItem(OPEN_STORE, card.open ? '1' : '0'); } catch (e) { /* not remembered */ }
      if (!card.open) return;
      if (map) setTimeout(() => map.invalidateSize(), 50);
      load();
    });
  }
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', wireFold); else wireFold();

  window.FuelStations = { load, setFuel, nearCar, searchHere };
})();
