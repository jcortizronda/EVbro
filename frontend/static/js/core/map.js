/* EVBro — Mapa en vivo del coche (Leaflet).
 * (Script clásico: comparte el ámbito global con los demás ficheros; ver el orden de carga en templates/index.html.)
 */

/**
 * Genera una capa de mapa estándar y 100% gratuita que NO requiere ninguna API key.
 * Sustituye proveedores comerciales restrictivos (como CartoDB) por OpenStreetMap estándar,
 * eliminando pantallas en blanco y avisos de "API Key Required".
 * Incluye tolerancia a fallos con servidor espejo humanitario (HOT) en caso de caída temporal.
 * @param {Object} [options] - Opciones adicionales de Leaflet TileLayer.
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

// ───────────────────────── Mapa de la portada ─────────────────────────
// Plegado: vista previa fija (no arrastra, para que la página se pueda desplazar con el dedo); un toque lo AMPLÍA a pantalla
// completa, donde se puede mover y hacer zoom. «Centrar» vuelve al coche; «Ir a» abre la navegación hacia sus coordenadas.

const MAP_DEFAULT_ZOOM = 15;

/** Activa o desactiva los gestos del mapa (plegado = sin gestos, ampliado = todos). */
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
    // Resalta «Centrar» cuando el coche ya no está en el centro de la vista
    let off = false;
    if (has && liveMap) {
      const p = liveMap.latLngToContainerPoint(vehicleLatLng), c = liveMap.getSize().divideBy(2);
      off = p.distanceTo(c) > 28;
    }
    center.classList.toggle('is-off-center', off);
  }
  if (go) go.disabled = !has;
}

/** Vuelve a centrar el mapa en el coche y retoma el seguimiento automático. */
function centerMapOnVehicle() {
  if (!liveMap || !vehicleLatLng) return;
  mapUserMoved = false;
  liveMap.setView(vehicleLatLng, Math.max(liveMap.getZoom(), MAP_DEFAULT_ZOOM), { animate: true });
}

/** Abre la navegación hacia el coche: Google Maps (la app en Android/iOS si está instalada, o el navegador). */
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
  // «Atrás» del móvil cierra el mapa ampliado en vez de salir de la página
  if (expanded) history.pushState({ ebroMap: true }, '');
  else if (!fromHistory && history.state && history.state.ebroMap) history.back();
  setTimeout(() => {
    if (!liveMap) return;
    liveMap.invalidateSize();
    if (vehicleLatLng && !mapUserMoved) liveMap.setView(vehicleLatLng, liveMap.getZoom(), { animate: false });
    updateMapButtons();
  }, 60);
}

// Inicialización de Leaflet Map
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

  // Sigue al coche mientras el usuario no haya movido el mapa (antes se recentraba en cada lectura)
  if (firstFix || !mapUserMoved) liveMap.setView(latLng, firstFix ? MAP_DEFAULT_ZOOM : liveMap.getZoom());
  updateMapButtons();
  if (typeof refreshGpsPosition === 'function') refreshGpsPosition();

  const locElem = document.getElementById('mapLocationText');
  const coordElem = document.getElementById('mapCoordinatesText');
  if (locElem) locElem.innerText = address || "Ubicación detectada";
  if (coordElem) coordElem.innerText = `${lat.toFixed(4)}, ${lon.toFixed(4)}`;
}


// ───────────────────────── Posición por GPS (Traccar) ─────────────────────────
// Un coche con localizador propio reporta a Traccar; si esa posición es más fresca que la de la nube (y de hace menos de
// 10 min) se pinta con su propio marcador. Se consulta como mucho cada 30 s.
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
  } catch (e) { /* sin conexión: se intenta en la siguiente lectura */ }
}
