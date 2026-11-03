/* EVBro — Live map of the car (Leaflet).
 * (Classic script: shares the global scope with the other files; see the load order in templates/index.html.)
 */

/**
 * Builds a standard, completely free map layer that needs NO API key.
 * Replaces restrictive commercial providers (such as CartoDB) with standard OpenStreetMap,
 * avoiding blank screens and "API Key Required" notices.
 * Falls back to the humanitarian mirror server (HOT) during a temporary outage.
 * @param {Object} [options] - Extra Leaflet TileLayer options.
 * @returns {L.TileLayer}
 */
function createStandardTileLayer(options = {}) {
  const defaultOptions = {
    maxZoom: 19,
    attribution: '&copy; <a href="https://www.openstreetmap.org/copyright" target="_blank" rel="noopener">OpenStreetMap</a> contributors',
    crossOrigin: true,
    ...options
  };

  const layer = L.tileLayer('https://tile.openstreetmap.org/{z}/{x}/{y}.png', defaultOptions);

  layer.on('tileerror', function(e) {
    const tile = e.tile;
    if (tile && !tile._hasFallback) {
      tile._hasFallback = true;
      if (tile.src && tile.src.includes('tile.openstreetmap.org')) {
        tile.src = tile.src.replace('tile.openstreetmap.org', 'a.tile.openstreetmap.fr/hot');
      }
    }
  });

  return layer;
}

// ───────────────────────── Home tab map ─────────────────────────
// Collapsed: fixed preview (no dragging, so the page can be scrolled with a finger); a tap EXPANDS it to full screen,
// where it can be moved and zoomed. «Centrar» goes back to the car; «Ir a» opens navigation to its coordinates.

const MAP_DEFAULT_ZOOM = 15;

/** Turns the map gestures on or off (collapsed = none, expanded = all). */
function setMapInteractive(on) {
  if (!liveMap) return;
  for (const h of ['dragging', 'touchZoom', 'doubleClickZoom', 'scrollWheelZoom', 'boxZoom', 'keyboard']) {
    if (liveMap[h]) (on ? liveMap[h].enable() : liveMap[h].disable());
  }
}

function updateMapButtons() {
  const has = !!vehicleLatLng;
  const center = document.getElementById('btnMapCenter');
  const go = document.getElementById('btnMapGo');
  if (center) {
    center.disabled = !has;
    // Highlight «Centrar» when the car is no longer in the centre of the view
    let off = false;
    if (has && liveMap) {
      const p = liveMap.latLngToContainerPoint(vehicleLatLng), c = liveMap.getSize().divideBy(2);
      off = p.distanceTo(c) > 28;
    }
    center.classList.toggle('is-off-center', off);
  }
  if (go) go.disabled = !has;
}

/** Re-centres the map on the car and resumes automatic following. */
function centerMapOnVehicle() {
  if (!liveMap || !vehicleLatLng) return;
  mapUserMoved = false;
  liveMap.setView(vehicleLatLng, Math.max(liveMap.getZoom(), MAP_DEFAULT_ZOOM), { animate: true });
}

/** Opens navigation to the car: Google Maps (the app on Android/iOS if installed, or the browser). */
function openNavigationToVehicle() {
  if (!vehicleLatLng) return;
  const [lat, lon] = vehicleLatLng;
  const url = `https://www.google.com/maps/dir/?api=1&destination=${lat},${lon}`;
  window.open(url, '_blank', 'noopener');
}

function setMapExpanded(expanded, fromHistory = false) {
  const card = document.getElementById('liveMapCard');
  if (!card || mapExpanded === expanded) return;
  mapExpanded = expanded;
  card.classList.toggle('map-expanded', expanded);
  document.body.classList.toggle('map-open', expanded);
  const close = document.getElementById('btnMapClose');
  if (close) close.hidden = !expanded;
  setMapInteractive(expanded);
  // The phone's «back» closes the expanded map instead of leaving the page
  if (expanded) history.pushState({ ebroMap: true }, '');
  else if (!fromHistory && history.state && history.state.ebroMap) history.back();
  setTimeout(() => {
    if (!liveMap) return;
    liveMap.invalidateSize();
    if (vehicleLatLng && !mapUserMoved) liveMap.setView(vehicleLatLng, liveMap.getZoom(), { animate: false });
    updateMapButtons();
  }, 60);
}

// Leaflet map initialisation
function initMap() {
  const mapElem = document.getElementById('liveMap');
  if (!mapElem || liveMap) return;

  liveMap = L.map('liveMap', {
    zoomControl: false,
    attributionControl: false
  }).setView([40.4168, -3.7038], 14);

  L.control.zoom({ position: 'topright' }).addTo(liveMap);
  L.control.attribution({ prefix: false, position: 'bottomleft' }).addTo(liveMap);

  createStandardTileLayer().addTo(liveMap);

  setMapInteractive(false);
  liveMap.on('click', () => { if (!mapExpanded) setMapExpanded(true); });
  liveMap.on('dragstart', () => { mapUserMoved = true; });
  liveMap.on('moveend zoomend', updateMapButtons);

  document.getElementById('btnMapCenter')?.addEventListener('click', centerMapOnVehicle);
  document.getElementById('btnMapGo')?.addEventListener('click', openNavigationToVehicle);
  document.getElementById('btnMapClose')?.addEventListener('click', () => setMapExpanded(false));
  document.addEventListener('keydown', (e) => { if (e.key === 'Escape' && mapExpanded) setMapExpanded(false); });
  window.addEventListener('popstate', () => { if (mapExpanded) setMapExpanded(false, true); });
}

// The marker belongs to one car: switching cars forgets the previous one's position (a car without an Ebro account used to
// keep showing the previous car's spot, usually home, once its own GPS marker expired)
let mapVin = null;
function forgetOtherCarPosition() {
  if (typeof currentVin === 'undefined' || mapVin === currentVin) return;
  mapVin = currentVin;
  vehicleLatLng = null;
  mapUserMoved = false;
  if (vehicleMarker && liveMap) { liveMap.removeLayer(vehicleMarker); vehicleMarker = null; }
  if (gpsMarker && liveMap) { liveMap.removeLayer(gpsMarker); gpsMarker = null; }
  const locElem = document.getElementById('mapLocationText');
  const coordElem = document.getElementById('mapCoordinatesText');
  if (locElem) locElem.innerText = '';
  if (coordElem) coordElem.innerText = '';
  updateMapButtons();
}

/** Moves the car's marker (and the view, while the user has not moved it). */
function placeVehicleMarker(latLng) {
  forgetOtherCarPosition();
  const firstFix = !vehicleLatLng;
  vehicleLatLng = latLng;
  if (!vehicleMarker) {
    const carIcon = L.divIcon({
      className: 'custom-car-pin',
      html: `<div class="u-background-00e5ff_width-16px_height-16px_border-radius-50pct_border-3px-solid-fff_box-shadow-0-0-12px-00e5ff"></div>`,
      iconSize: [20, 20],
      iconAnchor: [10, 10]
    });
    vehicleMarker = L.marker(latLng, { icon: carIcon }).addTo(liveMap);
  } else {
    vehicleMarker.setLatLng(latLng);
  }

  // Follow the car while the user has not moved the map (it used to re-centre on every reading)
  if (firstFix || !mapUserMoved) liveMap.setView(latLng, firstFix ? MAP_DEFAULT_ZOOM : liveMap.getZoom());
  updateMapButtons();
}

function updateMapPosition(lat, lon, address) {
  if (!liveMap || !lat || !lon) return;
  // A car without an Ebro account: its tracker is its position, however old (the stored one is usually where it was registered)
  if (isManualCar() && gpsLatest && gpsLatest.vin === currentVin) { refreshGpsPosition(); return; }
  placeVehicleMarker([lat, lon]);
  if (typeof refreshGpsPosition === 'function') refreshGpsPosition();

  const locElem = document.getElementById('mapLocationText');
  const coordElem = document.getElementById('mapCoordinatesText');
  if (locElem) locElem.innerText = address || "Ubicación detectada";
  if (coordElem) coordElem.innerText = `${lat.toFixed(4)}, ${lon.toFixed(4)}`;
}


// ───────────────────────── GPS position (Traccar) ─────────────────────────
// A connected car: if the tracker's position is fresher than the cloud's (and less than 10 min old) it is drawn with its own
// marker. A car without an Ebro account has no cloud position: the tracker's last fix IS the car's position, however old (a
// tracker sleeps while parked). Queried at most every 30 s.
let gpsMarker = null;
let gpsLastFetch = 0;
let gpsLatest = null;                 // {vin, position} of the last answer
const isManualCar = () => document.body.dataset.manual === '1';

/** «hace 4 min», «hace 3 h» or the date, for how old a fix is. */
function gpsAgeText(ageS) {
  const min = Math.max(0, Math.round(ageS / 60));
  if (min < 60) return t('hace {p0} min', {p0: min});
  if (min < 48 * 60) return t('hace {p0} h', {p0: Math.round(min / 60)});
  return new Date(Date.now() - ageS * 1000).toLocaleString(I18n.locale, { day: 'numeric', month: 'short', hour: '2-digit', minute: '2-digit' });
}

function showGpsAsVehicle(position) {
  const latLng = [position.latitude, position.longitude];
  if (gpsMarker) { liveMap.removeLayer(gpsMarker); gpsMarker = null; }
  placeVehicleMarker(latLng);
  const locElem = document.getElementById('mapLocationText');
  const coordElem = document.getElementById('mapCoordinatesText');
  if (locElem) locElem.innerText = t('Posición GPS · {p0}', {p0: gpsAgeText(position.age_s)});
  if (coordElem) coordElem.innerText = `${position.latitude.toFixed(4)}, ${position.longitude.toFixed(4)}`;
}

async function refreshGpsPosition(force = false) {
  if (!liveMap || typeof currentVin === 'undefined' || !currentVin) return;
  forgetOtherCarPosition();
  const now = Date.now();
  if (!force && now - gpsLastFetch < 30000) {
    if (isManualCar() && gpsLatest && gpsLatest.vin === currentVin && gpsLatest.position) showGpsAsVehicle(gpsLatest.position);
    return;
  }
  gpsLastFetch = now;
  try {
    const vin = currentVin;
    const res = await fetch(`/api/vehicles/${vin}/gps/latest`);
    if (!res.ok) return;
    const { position } = await res.json();
    gpsLatest = { vin, position, at: now };
    if (vin !== currentVin) return;
    if (isManualCar() && position) { showGpsAsVehicle(position); return; }
    const fresh = position && position.age_s < 10 * 60;
    if (!fresh) {
      if (gpsMarker) { liveMap.removeLayer(gpsMarker); gpsMarker = null; }
      return;
    }
    const latLng = [position.latitude, position.longitude];
    const speed = position.speed_kmh !== null && position.speed_kmh !== undefined ? ` · ${Math.round(position.speed_kmh)} km/h` : '';
    const text = `${t('GPS · {p0}', {p0: gpsAgeText(position.age_s)})}${speed}`;
    if (!gpsMarker) {
      gpsMarker = L.circleMarker(latLng, { radius: 7, color: '#fff', weight: 2, fillColor: themeAccent(), fillOpacity: 0.95 }).addTo(liveMap);
    } else {
      gpsMarker.setLatLng(latLng);
    }
    gpsMarker.bindTooltip(text, { direction: 'top', offset: [0, -8] });
    if (!mapUserMoved && (!vehicleLatLng || liveMap.distance(latLng, vehicleLatLng) > 50)) liveMap.panTo(latLng);
  } catch (e) { /* offline: try again on the next reading */ }
}

// A car without an Ebro account gets no readings from the cloud: while Inicio is on screen its GPS position is asked every 30 s
setInterval(() => {
  if (document.visibilityState !== 'visible' || !isManualCar() || window.currentTabName !== 'overview') return;
  refreshGpsPosition();
}, 30000);
