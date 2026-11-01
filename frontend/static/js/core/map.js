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

function updateMapPosition(lat, lon, address) {
  if (!liveMap || !lat || !lon) return;

  const latLng = [lat, lon];
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
  if (typeof refreshGpsPosition === 'function') refreshGpsPosition();

  const locElem = document.getElementById('mapLocationText');
  const coordElem = document.getElementById('mapCoordinatesText');
  if (locElem) locElem.innerText = address || "Ubicación detectada";
  if (coordElem) coordElem.innerText = `${lat.toFixed(4)}, ${lon.toFixed(4)}`;
}


// ───────────────────────── GPS position (Traccar) ─────────────────────────
// A car with its own tracker reports to Traccar; if that position is fresher than the cloud's (and less than 10 min old)
// it is drawn with its own marker. Queried at most every 30 s.
let gpsMarker = null;
let gpsLastFetch = 0;
async function refreshGpsPosition(force = false) {
  if (!liveMap || typeof currentVin === 'undefined' || !currentVin) return;
  const now = Date.now();
  if (!force && now - gpsLastFetch < 30000) return;
  gpsLastFetch = now;
  try {
    const res = await fetch(`/api/vehicles/${currentVin}/gps/latest`);
    if (!res.ok) return;
    const { position } = await res.json();
    const fresh = position && position.age_s < 10 * 60;
    if (!fresh) {
      if (gpsMarker) { liveMap.removeLayer(gpsMarker); gpsMarker = null; }
      return;
    }
    const latLng = [position.latitude, position.longitude];
    const ago = Math.max(0, Math.round(position.age_s / 60));
    const text = `GPS · hace ${ago} min${position.speed_kmh !== null && position.speed_kmh !== undefined ? ` · ${Math.round(position.speed_kmh)} km/h` : ''}`;
    if (!gpsMarker) {
      gpsMarker = L.circleMarker(latLng, { radius: 7, color: '#fff', weight: 2, fillColor: themeAccent(), fillOpacity: 0.95 }).addTo(liveMap);
    } else {
      gpsMarker.setLatLng(latLng);
    }
    gpsMarker.bindTooltip(text, { direction: 'top', offset: [0, -8] });
    if (!mapUserMoved && (!vehicleLatLng || liveMap.distance(latLng, vehicleLatLng) > 50)) liveMap.panTo(latLng);
  } catch (e) { /* offline: try again on the next reading */ }
}
