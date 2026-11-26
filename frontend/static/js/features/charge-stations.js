/* EVbro — Carga → «Cargadores cercanos»: the public charging points around the car (or around the part of the map the user moved
 * to) that it can use, on a map (each site a pill with its best power) and as a list, nearest first.
 * Data: GET /api/vehicles/{vin}/charge-stations — the official list of the DGT's National Access Point (backend
 * services/charge_stations.py). It has no prices and does not say whether a point is free.
 * (Classic script: shares the global scope with the other files; see the load order in templates/index.html.)
 */
(function () {
  'use strict';

  const $ = (id) => document.getElementById(id);
  const STORE = 'evbro_chargers';
  const OPEN_STORE = 'evbro_chargers_open';
  const RADIUS_KM = 10;
  const LIST_MAX = 10;
  const PILLS_MAX = 14;
  const PLUG = { type2: 'Tipo 2', ccs2: 'CCS2', chademo: 'CHAdeMO', type1: 'Tipo 1', ccs1: 'CCS1', schuko: 'Schuko', other: 'Otro' };
  const PAY = { apps: 'app', rfid: 'tarjeta RFID', creditCard: 'tarjeta de crédito', debitCard: 'tarjeta de débito', nfc: 'NFC', pinpad: 'PIN' };
  let map = null;
  let layer = null;
  let markers = {};
  let centre = null;            // {lat, lon} chosen with «Buscar en esta zona»; null = around the car
  let lastVin = null;
  let busy = false;

  const locale = () => (window.I18n && I18n.locale) || 'es-ES';
  const num = (n) => Number(n).toLocaleString(locale(), { maximumFractionDigits: 1 });
  const km = (d) => (d < 1 ? `${Math.round(d * 1000)} m` : `${num(d)} km`);

  /** The kind chosen on this device (every connector the car takes, only fast or only slow). */
  function kind() {
    let kept = null;
    try { kept = localStorage.getItem(STORE); } catch (e) { /* the default */ }
    return ['all', 'dc', 'ac'].includes(kept) ? kept : 'all';
  }

  function setKind(value) {
    try { localStorage.setItem(STORE, value); } catch (e) { /* not kept: the default next time */ }
    load();
  }

  /** Purple from 100 kW, blue for the other fast ones, teal for the slow ones. */
  function colour(s) {
    if (!s.fast) return '#0f9d8a';
    return s.max_kw >= 100 ? '#7c3aed' : '#0a6cff';
  }

  /** «6 × CCS2 150 kW · 6 × Tipo 2 22 kW» */
  const plugsText = (s) => (s.plugs || []).map(([plug, kw, n]) => `${n} × ${t(PLUG[plug] || PLUG.other)} ${num(kw)} kW`).join(' · ');

  function status(text) {
    const box = $('csStatus');
    if (!box) return;
    box.textContent = text || '';
    box.hidden = !text;
  }

  function ensureMap() {
    if (map || !$('csMap') || typeof L === 'undefined') return;
    map = L.map('csMap', { zoomControl: true, attributionControl: true, scrollWheelZoom: false }).setView([40.4168, -3.7038], 12);
    createStandardTileLayer().addTo(map);
    layer = L.layerGroup().addTo(map);
    // after moving the map by hand, offer to search there
    map.on('dragend', () => { const b = $('csHere'); if (b) b.hidden = false; });
  }

  function line(className, text) {
    const el = document.createElement('div');
    if (className) el.className = className;
    el.textContent = text;
    return el;
  }

  function popupFor(s) {
    const box = document.createElement('div');
    box.className = 'fs-popup';
    const title = document.createElement('strong');
    title.textContent = s.name;
    box.append(title);
    if (s.operator && s.operator !== s.name) box.append(line('fs-popup-hours', s.operator));
    box.append(line('', [s.address, s.town].filter(Boolean).join(', ')));
    if (s.hours) box.append(line('fs-popup-hours', s.hours));
    box.append(line('fs-popup-price', plugsText(s)));
    if ((s.pay || []).length) box.append(line('fs-popup-hours', t('Pago: {p0}', { p0: s.pay.map((p) => t(PAY[p] || p)).join(', ') })));
    box.append(line('fs-popup-hours', km(s.distance_km)));
    const go = document.createElement('a');
    go.href = `https://www.google.com/maps/dir/?api=1&destination=${s.lat},${s.lon}`;
    go.target = '_blank';
    go.rel = 'noopener';
    go.textContent = t('Cómo llegar');
    box.append(go);
    return box;
  }

  function pin(s) {
    const el = document.createElement('span');
    el.className = 'fs-pin';
    el.textContent = `${Math.round(s.max_kw)} kW`;
    el.style.backgroundColor = colour(s);                    // CSSOM: allowed by the CSP (no style attribute)
    return L.divIcon({ html: el, className: 'fs-pin-wrap', iconSize: null, iconAnchor: [24, 14] });
  }

  function render(data) {
    ensureMap();
    markers = {};
    if (layer) layer.clearLayers();
    const list = $('csList');
    list.replaceChildren();
    const stations = data.stations || [];
    if (map) {
      const c = [data.center.lat, data.center.lon];
      if (data.center.from === 'car') {
        L.circleMarker(c, { radius: 8, color: '#fff', weight: 2, fillColor: themeAccent(), fillOpacity: 1 }).bindTooltip(t('Tu coche')).addTo(layer);
      }
      // the nearest ones carry their power; the rest are a dot of their colour (a city has hundreds and the pills piled up)
      const withPower = new Set(stations.slice(0, PILLS_MAX).map((s) => s.id));
      stations.forEach((s) => {
        markers[s.id] = withPower.has(s.id)
          ? L.marker([s.lat, s.lon], { icon: pin(s), riseOnHover: true, zIndexOffset: 500 }).bindPopup(popupFor(s)).addTo(layer)
          : L.circleMarker([s.lat, s.lon], { radius: 6, color: '#fff', weight: 1.5, fillColor: colour(s), fillOpacity: 0.95 }).bindPopup(popupFor(s)).addTo(layer);
      });
      if (stations.length) {
        map.fitBounds(L.latLngBounds([c, ...stations.slice(0, 12).map((s) => [s.lat, s.lon])]), { padding: [24, 24], maxZoom: 15 });
      } else {
        map.setView(c, 12);
      }
      setTimeout(() => map.invalidateSize(), 50);
    }
    const here = $('csHere');
    if (here) here.hidden = true;
    status(stations.length ? '' : t('No hay cargadores de ese tipo a menos de {p0} km.', { p0: data.radius_km }));
    // the list: nearest first
    stations.slice(0, LIST_MAX).forEach((s) => {
      const li = document.createElement('li');
      const btn = document.createElement('button');
      btn.type = 'button';
      btn.className = 'fs-row cs-row';
      btn.addEventListener('click', () => {
        const m = markers[s.id];
        if (map && m) { map.setView(m.getLatLng(), Math.max(map.getZoom(), 15)); m.openPopup(); }
        $('csMap').scrollIntoView({ behavior: 'smooth', block: 'center' });
      });
      const name = document.createElement('span');
      name.className = 'fs-row-name';
      name.textContent = s.name;
      const sub = document.createElement('span');
      sub.className = 'fs-row-sub';
      sub.textContent = [km(s.distance_km), s.town, s.hours].filter(Boolean).join(' · ');
      const plugs = document.createElement('span');
      plugs.className = 'fs-row-sub cs-row-plugs';
      plugs.textContent = plugsText(s);
      const power = document.createElement('strong');
      power.className = 'fs-row-price';
      power.textContent = `${Math.round(s.max_kw)} kW`;
      power.style.color = colour(s);
      const text = document.createElement('span');
      text.className = 'fs-row-text';
      text.append(name, sub, plugs);
      btn.append(text, power);
      li.appendChild(btn);
      list.appendChild(li);
    });
    const source = $('csSource');
    if (source) {
      const when = data.source_time ? new Date(data.source_time) : null;
      const day = when && !isNaN(when) ? when.toLocaleDateString(locale(), { day: 'numeric', month: 'long', year: 'numeric' }) : '';
      source.textContent = t('Lista oficial de puntos de recarga (DGT, Punto de Acceso Nacional; datos del Ministerio para la Transición Ecológica){p0}. No indica precios ni si el punto está libre.', { p0: day ? `, ${day}` : '' });
    }
  }

  async function load() {
    const card = $('csCard');
    if (!card || !card.open || typeof currentVin === 'undefined' || !currentVin || busy) return;      // folded: nothing is asked
    if (currentVin !== lastVin) { centre = null; lastVin = currentVin; }
    const select = $('csKind');
    if (select) select.value = kind();
    busy = true;
    ensureMap();
    status(t('Buscando cargadores…'));
    try {
      const params = new URLSearchParams({ kind: kind(), radius_km: String(RADIUS_KM) });
      if (centre) { params.set('lat', centre.lat.toFixed(5)); params.set('lon', centre.lon.toFixed(5)); }
      const res = await fetch(`/api/vehicles/${encodeURIComponent(currentVin)}/charge-stations?${params}`);
      const data = await res.json().catch(() => ({}));
      if (!res.ok) {
        if (layer) layer.clearLayers();
        $('csList').replaceChildren();
        status(data.detail || t('No se pudieron cargar los cargadores.'));
        return;
      }
      render(data);
    } catch (e) {
      status(t('No se pudieron cargar los cargadores.'));
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

  // the fold: closed by default, remembered on this device; opening it loads the chargers (and the map gets its real size)
  function wireFold() {
    const card = $('csCard');
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

  window.ChargeStations = { load, setKind, nearCar, searchHere };
})();
