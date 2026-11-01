/* EVBro — Geofences: map, list, create/edit and charging tariffs.
 * (Classic script: shares the global scope with the other files; see the load order in templates/index.html.)
 */

let geofencesOverviewMapInstance = null;

let geofencesOverviewLayer = null;

let geofenceModalMapInstance = null;

let geofenceModalMarker = null;

let geofenceModalCircle = null;

// Initialise the geofence overview map (Geocercas tab)
function initGeofencesOverviewMap() {
  const container = document.getElementById('geofencesOverviewMap');
  if (!container || geofencesOverviewMapInstance) return;

  geofencesOverviewMapInstance = L.map('geofencesOverviewMap', {
    zoomControl: true,
    attributionControl: false
  }).setView([40.4168, -3.7038], 13);

  createStandardTileLayer().addTo(geofencesOverviewMapInstance);

  geofencesOverviewLayer = L.layerGroup().addTo(geofencesOverviewMapInstance);

  // Clicking anywhere on the overview map opens the modal to create a geofence there
  geofencesOverviewMapInstance.on('click', (e) => {
    openCreateGeofenceModal(e.latlng.lat, e.latlng.lng);
  });
}

// Update markers and circular zones on the geofence overview map
function updateGeofencesOverviewMap() {
  const container = document.getElementById('geofencesOverviewMap');
  if (!container) return;
  if (!geofencesOverviewMapInstance) {
    initGeofencesOverviewMap();
  }

  if (geofencesOverviewLayer) {
    geofencesOverviewLayer.clearLayers();
  }

  const bounds = [];

  // 1. Draw each geofence
  (cachedGeofencesList || []).forEach(gf => {
    const isCharging = gf.is_charging_spot !== false;
    const color = isCharging ? themeAccent() : '#a855f7';
    const latLng = [gf.latitude, gf.longitude];
    bounds.push(latLng);

    // Coverage circle
    const circle = L.circle(latLng, {
      radius: gf.radius_meters || 100,
      color: color,
      fillColor: color,
      fillOpacity: 0.18,
      weight: 2
    }).addTo(geofencesOverviewLayer);

    // Centre marker with a descriptive icon
    const iconHtml = icon(isCharging ? 'bolt' : 'pin', 14);
    const pin = L.divIcon({
      className: 'custom-geofence-pin',
      html: `<div class="gf-pin-dot" data-css="background:${color}; box-shadow:0 0 10px ${color}">${iconHtml}</div>`,
      iconSize: [26, 26],
      iconAnchor: [13, 13]
    });

    const marker = L.marker(latLng, { icon: pin }).addTo(geofencesOverviewLayer);
    applyDataStyles(marker.getElement());
    
    let popupContent = `
      <div class="u-font-family-outfit-sans-serif_min-width-170px">
        <div class="u-font-weight-700_font-size-0p95rem_margin-bottom-0p25rem_color-0f172a">${escapeHtml(gf.name)}</div>
        <div class="u-font-size-0p8rem_color-475569_margin-bottom-0p4rem">Radio: <b>${gf.radius_meters} m</b>${isCharging ? ' · Punto de Carga' : ''}</div>
        <button class="u-background-00e5ff_color-000_border-none_border-radius-6px_padding-0p4rem-0p65rem_font-size-0p8rem_font-weight-600_cursor-pointer_width-100pct" type="button" data-on-click="openEditGeofenceModal(${gf.id})">
          Editar Geocerca
        </button>
      </div>
    `;
    marker.bindPopup(popupContent);
    circle.bindPopup(popupContent);
  });

  // 2. Draw the car's current position on the map if available
  if (latestTelemetryData && latestTelemetryData.latitude && latestTelemetryData.longitude) {
    const carPos = [latestTelemetryData.latitude, latestTelemetryData.longitude];
    bounds.push(carPos);
    const carIcon = L.divIcon({
      className: 'custom-car-overview-pin',
      html: `<div class="u-background-10b981_width-22px_height-22px_border-radius-50pct_border-3px-solid-fff_box-shadow-0-0-12px-10b981_display-flex_align-items-center_justify-content-center_color-fff">${icon('car', 12)}</div>`,
      iconSize: [24, 24],
      iconAnchor: [12, 12]
    });
    L.marker(carPos, { icon: carIcon })
      .bindPopup(`<b>${document.getElementById('vehicleName').innerText || 'Tu Coche'}</b><br>Ubicación actual`)
      .addTo(geofencesOverviewLayer);
  }

  if (bounds.length > 0 && geofencesOverviewMapInstance) {
    if (bounds.length === 1) {
      geofencesOverviewMapInstance.setView(bounds[0], 15);
    } else {
      geofencesOverviewMapInstance.fitBounds(bounds, { padding: [35, 35], maxZoom: 16 });
    }
  }

  setTimeout(() => {
    if (geofencesOverviewMapInstance) geofencesOverviewMapInstance.invalidateSize();
  }, 200);
}

// Centre and zoom on a geofence when "Ver" is tapped in the table or card
function zoomToGeofenceOnOverviewMap(id) {
  const gf = (cachedGeofencesList || []).find(item => item.id == id);
  if (!gf) return;
  const mapEl = document.getElementById('geofencesOverviewMap');
  if (mapEl) {
    mapEl.scrollIntoView({ behavior: 'smooth', block: 'center' });
  }
  if (geofencesOverviewMapInstance) {
    geofencesOverviewMapInstance.flyTo([gf.latitude, gf.longitude], 16, { duration: 1.2 });
  }
}


function closeAllGeofenceMenus() {
  document.querySelectorAll('.geofence-popover-menu').forEach(el => el.style.display = 'none');
}

document.addEventListener('click', (e) => {
  if (!e.target.closest('.geofence-action-btn.btn-more') && !e.target.closest('.geofence-popover-menu')) {
    closeAllGeofenceMenus();
  }
});

function toggleGeofenceMenu(event, id) {
  event.stopPropagation();
  const menu = document.getElementById(`gfMenu_${id}`);
  if (!menu) return;
  const isShown = menu.style.display === 'flex';
  closeAllGeofenceMenus();
  if (!isShown) {
    menu.style.display = 'flex';
  }
}

// Load and show the geofence list as touch cards (fully responsive, no sideways scroll)
/** Tariff of a charging spot in one short phrase («0,150 €/kWh», «2.0TD», «PVPC», «Gratis»); empty for a plain location. */
function geofenceTariffText(gf) {
  if (gf.is_charging_spot !== true) return '';
  const mode = (gf.cost_mode || 'fixed').toLowerCase();
  const eur = (v, d) => `${(v ?? d).toFixed(3).replace('.', ',')} €/kWh`;
  if (mode === 'free') return 'Carga gratis';
  if (mode === 'time_of_use') return `2.0TD ${eur(gf.cost_valle, 0.08)} / ${eur(gf.cost_llano, 0.14)} / ${eur(gf.cost_punta, 0.22)}`;
  if (mode === 'pvpc') return `PVPC ${gf.pvpc_zone || 'Península'}${gf.cost_margin ? ` +${eur(gf.cost_margin, 0)}` : ''}`;
  return eur(gf.cost_per_kwh, 0.15);
}

/** Which alerts this geofence sends: «Avisa al entrar y al salir», «Avisa al entrar», «Avisa al salir» or «Sin avisos». */
function geofenceAlertsText(gf) {
  const enter = gf.notify_enter !== false;
  const exit = gf.notify_exit !== false;
  if (enter && exit) return 'Avisa al entrar y al salir';
  if (enter) return 'Avisa al entrar';
  if (exit) return 'Avisa al salir';
  return 'Sin avisos';
}

/** The second line of a geofence card: alerts and, for charging spots, the tariff (replaces the old «Info» modal). */
function geofenceMetaHtml(gf) {
  const alerts = geofenceAlertsText(gf);
  const silent = alerts === 'Sin avisos';
  const tariff = geofenceTariffText(gf);
  return `<span class="gf-meta-alerts${silent ? ' is-silent' : ''}">${escapeHtml(alerts)}</span>${tariff ? `<span class="gf-meta-tariff">${escapeHtml(tariff)}</span>` : ''}`;
}

async function loadGeofences() {
  const container = document.getElementById('geofencesCardsContainer');
  const tbody = document.getElementById('geofencesTableBody');
  if (!container && !tbody) return;

  if (container) {
    container.innerHTML = '<div class="u-text-align-center_padding-2rem_color-var-text-muted"><span class="spin"></span> Cargando geocercas...</div>';
  }
  if (tbody) {
    tbody.innerHTML = '<tr><td class="u-text-align-center_padding-1p5rem_color-var-text-muted" colspan="3"><span class="spin"></span> Cargando geocercas...</td></tr>';
  }

  try {
    const res = await fetch('/api/geofences');
    const geofences = await res.json();
    cachedGeofencesList = geofences || [];

    // Update the geofence overview map
    updateGeofencesOverviewMap();

    if (!geofences || geofences.length === 0) {
      const emptyMsg = 'No hay geocercas configuradas todavía. Toca el mapa o pulsa "+ Nueva Geocerca" para añadir una.';
      if (container) {
        container.innerHTML = `<div class="u-text-align-center_color-var-text-muted_padding-2rem_font-size-0p95rem">${emptyMsg}</div>`;
      }
      if (tbody) {
        tbody.innerHTML = `<tr><td class="u-text-align-center_color-var-text-muted_padding-2rem" colspan="3">${emptyMsg}</td></tr>`;
      }
      return;
    }

    if (container) {
      container.innerHTML = '';
      geofences.forEach(gf => {
        const isCharging = gf.is_charging_spot === true;
        const purposeBadge = isCharging
          ? '<span class="geofence-card-badge charging">Punto de Carga</span>'
          : '';

        const coords = `${gf.latitude.toFixed(4)}, ${gf.longitude.toFixed(4)}`;

        const card = document.createElement('div');
        card.className = 'geofence-card-item';
        card.innerHTML = `
          <div class="geofence-card-top">
            <div class="geofence-card-info">
              <div class="geofence-card-name">${escapeHtml(gf.name)}</div>
              <div class="geofence-card-coords">
                <span>${coords}</span>
                <span>·</span>
                <span>Radio: <strong class="u-color-var-text-primary">${gf.radius_meters}m</strong></span>
              </div>
              <div class="geofence-card-meta">${geofenceMetaHtml(gf)}</div>
            </div>
            ${purposeBadge}
          </div>
          <div class="geofence-card-actions">
            <button type="button" class="geofence-action-btn btn-view" data-on-click="zoomToGeofenceOnOverviewMap(${gf.id})" title="Ver en el mapa">
              ${icon('pin', 15)} Ver
            </button>
            <button type="button" class="geofence-action-btn btn-edit" data-on-click="openEditGeofenceModal(${gf.id})" title="Editar geocerca">
              ${icon('edit', 15)} Editar
            </button>
            <button type="button" class="geofence-action-btn btn-delete" data-on-click="deleteGeofence(${gf.id})" title="Eliminar geocerca">
              ${icon('trash', 15)}
            </button>
          </div>
        `;
        container.appendChild(card);
      });
    }

    if (tbody) {
      tbody.innerHTML = '';
      geofences.forEach(gf => {
        const tr = document.createElement('tr');
        const isCharging = gf.is_charging_spot === true;
        const purposeBadge = isCharging
          ? '<span class="status-badge u-font-size-0p7rem_padding-0p15rem-0p5rem_background-rgba-0-229-255-0p12_color-var-primary_border-1px-solid-rgba-0-229-255-0p25">Punto de Carga</span>'
          : '';
        const coords = `${gf.latitude.toFixed(4)}, ${gf.longitude.toFixed(4)}`;

        tr.innerHTML = `
          <td>
            <div class="u-font-weight-700_font-size-0p95rem_color-var-text-primary_margin-bottom-0p25rem">${escapeHtml(gf.name)}</div>
            ${purposeBadge}
          </td>
          <td>
            <div class="u-font-family-monospace_font-size-0p82rem_color-var-text-secondary">${coords}</div>
            <div class="u-font-size-0p78rem_color-var-text-muted_margin-top-0p15rem">Radio: <strong class="u-color-var-text-primary">${gf.radius_meters} m</strong></div>
            <div class="geofence-card-meta">${geofenceMetaHtml(gf)}</div>
          </td>
          <td class="u-text-align-right_white-space-nowrap">
            <div class="u-display-inline-flex_gap-0p4rem_align-items-center_position-relative">
              <button class="geofence-action-btn btn-view" data-on-click="zoomToGeofenceOnOverviewMap(${gf.id})" title="Ver en el mapa">
                ${icon('pin', 15)} Ver
              </button>
              <div class="u-position-relative_display-inline-block">
                <button class="geofence-action-btn btn-more" data-on-click="toggleGeofenceMenu(event, ${gf.id})" title="Más opciones">
                  ⋯
                </button>
                <div id="gfMenu_tbl_${gf.id}" class="geofence-popover-menu u-display-none">
                  <button class="geofence-popover-item" data-on-click="closeAllGeofenceMenus(); openEditGeofenceModal(${gf.id})">
                    ${icon('edit', 15)} Editar
                  </button>
                  <button class="geofence-popover-item danger" data-on-click="closeAllGeofenceMenus(); deleteGeofence(${gf.id})">
                    ${icon('trash', 15)} Eliminar
                  </button>
                </div>
              </div>
            </div>
          </td>
        `;
        tbody.appendChild(tr);
      });
    }
  } catch (err) {
    if (container) container.innerHTML = `<div class="u-color-var-accent-red_text-align-center_padding-1p5rem">${t('Error al cargar geocercas: {p0}', {p0: escapeHtml(err)})}</div>`;
    if (tbody) tbody.innerHTML = `<tr><td class="u-color-var-accent-red_text-align-center_padding-1p5rem" colspan="3">${t('Error al cargar geocercas: {p0}', {p0: escapeHtml(err)})}</td></tr>`;
  }
}

// Set up the interactive map inside the geofence modal
function setupGeofenceModalMap(initialLat, initialLon, radius = 100) {
  const container = document.getElementById('geofenceModalMap');
  if (!container) return;

  // Without valid coordinates, use the car's or fall back to Madrid
  let lat = parseFloat(initialLat);
  let lon = parseFloat(initialLon);
  if (isNaN(lat) || isNaN(lon)) {
    if (latestTelemetryData && !isNaN(parseFloat(latestTelemetryData.latitude)) && !isNaN(parseFloat(latestTelemetryData.longitude))) {
      lat = parseFloat(latestTelemetryData.latitude);
      lon = parseFloat(latestTelemetryData.longitude);
    } else {
      lat = 40.4168;
      lon = -3.7038;
    }
  }

  // Initialise Leaflet in the modal if it does not exist
  if (!geofenceModalMapInstance) {
    geofenceModalMapInstance = L.map('geofenceModalMap', {
      zoomControl: true,
      attributionControl: false
    }).setView([lat, lon], 16);

    createStandardTileLayer().addTo(geofenceModalMapInstance);

    // Clicking the map moves the marker and the circle
    geofenceModalMapInstance.on('click', (e) => {
      setGeofencePosition(e.latlng.lat, e.latlng.lng);
    });
  } else {
    geofenceModalMapInstance.setView([lat, lon], 16);
  }

  // Draggable interactive pin
  const pinIcon = L.divIcon({
    className: 'geofence-pin-marker',
    html: '<div class="u-background-00e5ff_width-30px_height-30px_border-radius-50pct_border-3px-solid-ffffff_box-shadow-0-0-16px-rgba-0-229-255-0p85_display-flex_align-items-center_justify-content-center_color-000">' + icon('pin', 16) + '</div>',
    iconSize: [32, 32],
    iconAnchor: [16, 16]
  });

  if (!geofenceModalMarker) {
    geofenceModalMarker = L.marker([lat, lon], {
      icon: pinIcon,
      draggable: true
    }).addTo(geofenceModalMapInstance);

    geofenceModalMarker.on('dragend', (e) => {
      const pos = e.target.getLatLng();
      setGeofencePosition(pos.lat, pos.lng);
    });
  } else {
    geofenceModalMarker.setLatLng([lat, lon]);
  }

  // Visual radius circle
  if (!geofenceModalCircle) {
    geofenceModalCircle = L.circle([lat, lon], {
      radius: radius,
      color: themeAccent(),
      fillColor: themeAccent(),
      fillOpacity: 0.22,
      weight: 2
    }).addTo(geofenceModalMapInstance);
  } else {
    geofenceModalCircle.setLatLng([lat, lon]);
    geofenceModalCircle.setRadius(radius);
  }

  // Update the field values and the slider
  document.getElementById('gfRadius').value = radius;
  document.getElementById('gfRadiusSlider').value = radius;
  document.getElementById('gfRadiusDisplay').innerText = radius;
  setGeofencePosition(lat, lon, false);

  // Make sure it renders correctly when the modal opens
  setTimeout(() => {
    if (geofenceModalMapInstance) {
      geofenceModalMapInstance.invalidateSize();
      geofenceModalMapInstance.setView([lat, lon], 16);
    }
  }, 200);
}

// Set the selected position on the map and geocode the address
async function setGeofencePosition(lat, lon, updateAddress = true) {
  document.getElementById('gfLat').value = lat;
  document.getElementById('gfLon').value = lon;

  if (geofenceModalMarker) geofenceModalMarker.setLatLng([lat, lon]);
  if (geofenceModalCircle) geofenceModalCircle.setLatLng([lat, lon]);

  const coordsBadge = document.getElementById('gfCoordsBadge');
  if (coordsBadge) {
    coordsBadge.innerText = `${lat.toFixed(4)}°, ${lon.toFixed(4)}°`;
  }

  const addrText = document.getElementById('gfAddressText');
  if (addrText) {
    addrText.innerText = 'Consultando dirección...';
  }

  try {
    const res = await fetch(`/api/geofences/reverse?lat=${lat}&lon=${lon}`);
    if (res.ok) {
      const data = await res.json();
      const addr = data.address || `${lat.toFixed(4)}, ${lon.toFixed(4)}`;
      if (addrText) addrText.innerText = `${addr}`;
      
      // If the name is empty, suggest the street/area automatically
      const nameInput = document.getElementById('gfName');
      if (nameInput && nameInput.value.trim() === '') {
        nameInput.value = addr.split(',')[0].trim();
      }
    }
  } catch (e) {
    if (addrText) addrText.innerText = `${lat.toFixed(4)}, ${lon.toFixed(4)}`;
  }
}

// Change of the interactive radius slider
function onGeofenceRadiusSlider(val) {
  const r = parseInt(val) || 100;
  document.getElementById('gfRadius').value = r;
  document.getElementById('gfRadiusDisplay').innerText = r;
  if (geofenceModalCircle) {
    geofenceModalCircle.setRadius(r);
  }
}

// Address search in the modal to position the map
async function searchGeofenceLocation() {
  const input = document.getElementById('gfAddressSearchInput');
  const resultsDropdown = document.getElementById('gfSearchResultsList');
  if (!input || !resultsDropdown) return;

  const q = input.value.trim();
  if (q.length < 2) return;

  resultsDropdown.innerHTML = '<div class="u-padding-0p6rem_color-var-text-muted_font-size-0p82rem">Buscando en mapa...</div>';
  resultsDropdown.style.display = 'block';

  try {
    const res = await fetch(`/api/geofences/search-address?q=${encodeURIComponent(q)}`);
    if (res.ok) {
      const results = await res.json();
      if (!results || results.length === 0) {
        resultsDropdown.innerHTML = '<div class="u-padding-0p6rem_color-var-text-muted_font-size-0p82rem">No se encontraron resultados</div>';
        return;
      }

      resultsDropdown.innerHTML = '';
      results.forEach(it => {
        const item = document.createElement('div');
        item.className = 'geofence-search-item';
        item.innerHTML = `${icon('pin', 15)} <span>${escapeHtml(it.name)}</span>`;      // text from OpenStreetMap: never trusted as HTML
        item.onclick = () => {
          resultsDropdown.style.display = 'none';
          input.value = it.name.split(',')[0];
          setGeofencePosition(it.lat, it.lon);
          if (geofenceModalMapInstance) {
            geofenceModalMapInstance.flyTo([it.lat, it.lon], 17, { duration: 1.2 });
          }
        };
        resultsDropdown.appendChild(item);
      });
    }
  } catch (err) {
    resultsDropdown.innerHTML = '<div class="u-padding-0p6rem_color-var-accent-red_font-size-0p82rem">Error al buscar</div>';
  }
}

// Centre the geofence on the car
async function centerGeofenceOnCar() {
  let lat = latestTelemetryData ? latestTelemetryData.latitude : null;
  let lon = latestTelemetryData ? latestTelemetryData.longitude : null;

  if (!lat || !lon) {
    if (currentVin) {
      try {
        const res = await fetch(`/api/vehicles/${currentVin}/latest`);
        if (res.ok) {
          const d = await res.json();
          latestTelemetryData = d;
          lat = d.latitude;
          lon = d.longitude;
        }
      } catch (e) {}
    }
  }

  if (lat && lon) {
    setGeofencePosition(lat, lon);
    if (geofenceModalMapInstance) {
      geofenceModalMapInstance.flyTo([lat, lon], 16);
    }
  } else {
    showToast('No hay posición GPS registrada del coche.', 'warning');
  }
}

// Centre the geofence on my GPS location (browser)
function centerGeofenceOnUserLocation() {
  if (!navigator.geolocation) {
    showToast('Tu navegador no soporta geolocalización.', 'warning');
    return;
  }
  navigator.geolocation.getCurrentPosition(
    (pos) => {
      const lat = pos.coords.latitude;
      const lon = pos.coords.longitude;
      setGeofencePosition(lat, lon);
      if (geofenceModalMapInstance) {
        geofenceModalMapInstance.flyTo([lat, lon], 16);
      }
    },
    (err) => {
      showToast(t('No se pudo obtener tu ubicación GPS: {p0}', {p0: err.message}), 'error', 5000);
    },
    { enableHighAccuracy: true, timeout: 8000 }
  );
}

// Open the modal to create a new geofence
function openCreateGeofenceModal(customLat = null, customLon = null) {
  const setVal = (id, val) => { const el = document.getElementById(id); if (el) el.value = val; };
  const setCheck = (id, val) => { const el = document.getElementById(id); if (el) el.checked = val; };

  const titleEl = document.getElementById('geofenceModalTitle');
  if (titleEl) titleEl.innerText = 'Nueva Geocerca';

  setVal('gfId', '');
  setVal('gfName', '');
  setVal('gfAddressSearchInput', '');
  const searchDrop = document.getElementById('gfSearchResultsList');
  if (searchDrop) searchDrop.style.display = 'none';

  setCheck('gfNotifyEnter', true);
  setCheck('gfNotifyExit', true);
  setCheck('gfIsCharging', false);

  setVal('gfCostMode', 'fixed');
  setVal('gfCostPerKwh', '0.150');
  setVal('gfCostValle', '0.080');
  setVal('gfCostLlano', '0.140');
  setVal('gfCostPunta', '0.220');
  setVal('gfPvpcZone', 'Peninsula');
  setVal('gfCostMargin', '0.000');
  setVal('gfCostFallback', '0.150');

  toggleGeofenceChargingOptions();
  onGeofenceCostModeChange();

  // Initial position: if the overview map was clicked, use those coordinates; otherwise the car's or Madrid
  let lat = customLat;
  let lon = customLon;
  if (!lat || !lon) {
    if (latestTelemetryData && latestTelemetryData.latitude && latestTelemetryData.longitude) {
      lat = latestTelemetryData.latitude;
      lon = latestTelemetryData.longitude;
    } else {
      lat = 40.4168;
      lon = -3.7038;
    }
  }

  const modal = document.getElementById('geofenceModal');
  if (modal) modal.classList.add('open');
  setupGeofenceModalMap(lat, lon, 100);
}

// Backward compatibility
function showAddGeofenceModal() {
  openCreateGeofenceModal();
}

// Open the modal to edit an existing geofence
async function openEditGeofenceModal(id) {
  let gf = (cachedGeofencesList || []).find(item => String(item.id) === String(id));
  if (!gf) {
    try {
      const res = await fetch(`/api/geofences/${id}`);
      if (res.ok) {
        gf = await res.json();
      }
    } catch (e) {
      console.warn("Could not fetch the geofence from the API:", e);
    }
  }
  if (!gf) {
    showToast('Geocerca no encontrada.', 'error');
    return;
  }

  const setVal = (id, val) => { const el = document.getElementById(id); if (el) el.value = val; };
  const setCheck = (id, val) => { const el = document.getElementById(id); if (el) el.checked = val; };

  const titleEl = document.getElementById('geofenceModalTitle');
  if (titleEl) titleEl.innerText = 'Editar Geocerca';

  setVal('gfId', gf.id);
  setVal('gfName', gf.name || '');
  setVal('gfAddressSearchInput', '');
  const searchDrop = document.getElementById('gfSearchResultsList');
  if (searchDrop) searchDrop.style.display = 'none';

  setCheck('gfNotifyEnter', gf.notify_enter !== false);
  setCheck('gfNotifyExit', gf.notify_exit !== false);
  setCheck('gfIsCharging', gf.is_charging_spot === true);

  setVal('gfCostMode', gf.cost_mode || 'fixed');
  setVal('gfCostPerKwh', (gf.cost_per_kwh !== undefined && gf.cost_per_kwh !== null) ? gf.cost_per_kwh : '0.150');
  setVal('gfCostValle', (gf.cost_valle !== undefined && gf.cost_valle !== null) ? gf.cost_valle : '0.080');
  setVal('gfCostLlano', (gf.cost_llano !== undefined && gf.cost_llano !== null) ? gf.cost_llano : '0.140');
  setVal('gfCostPunta', (gf.cost_punta !== undefined && gf.cost_punta !== null) ? gf.cost_punta : '0.220');
  setVal('gfPvpcZone', gf.pvpc_zone || 'Peninsula');
  setVal('gfCostMargin', (gf.cost_margin !== undefined && gf.cost_margin !== null) ? gf.cost_margin : '0.000');
  setVal('gfCostFallback', (gf.cost_fallback !== undefined && gf.cost_fallback !== null) ? gf.cost_fallback : '0.150');

  toggleGeofenceChargingOptions();
  onGeofenceCostModeChange();

  const modal = document.getElementById('geofenceModal');
  if (modal) modal.classList.add('open');
  setupGeofenceModalMap(gf.latitude, gf.longitude, gf.radius_meters || 100);
}

function closeGeofenceModal() {
  const m = document.getElementById('geofenceModal');
  if (m) m.classList.remove('open');
  const searchDrop = document.getElementById('gfSearchResultsList');
  if (searchDrop) searchDrop.style.display = 'none';
}

// Toggle the cost section depending on the charging-point checkbox
function toggleGeofenceChargingOptions() {
  const chk = document.getElementById('gfIsCharging');
  const isCharging = chk ? chk.checked : false;
  const section = document.getElementById('gfChargingOptionsSection');
  if (section) {
    section.style.display = isCharging ? 'block' : 'none';
  }
}

// Toggle the tariff panels by the selected mode (fixed, free, time_of_use, pvpc)
function onGeofenceCostModeChange() {
  const modeEl = document.getElementById('gfCostMode');
  const mode = modeEl ? modeEl.value : 'fixed';
  const fixedP = document.getElementById('gfModeFixedPanel');
  const freeP = document.getElementById('gfModeFreePanel');
  const touP = document.getElementById('gfModeTouPanel');
  const pvpcP = document.getElementById('gfModePvpcPanel');

  if (fixedP) fixedP.style.display = (mode === 'fixed') ? 'block' : 'none';
  if (freeP) freeP.style.display = (mode === 'free') ? 'block' : 'none';
  if (touP) touP.style.display = (mode === 'time_of_use') ? 'block' : 'none';
  if (pvpcP) pvpcP.style.display = (mode === 'pvpc') ? 'block' : 'none';
}

// Save geofence (create or update)
async function submitGeofenceModal() {
  const getVal = (id, def = '') => { const el = document.getElementById(id); return el ? el.value : def; };
  const getFloat = (id, def = 0.0) => { const el = document.getElementById(id); return el && el.value !== '' ? parseFloat(el.value) || def : def; };
  const getChecked = (id, def = false) => { const el = document.getElementById(id); return el ? el.checked : def; };

  const id = getVal('gfId').trim();
  const name = getVal('gfName').trim();
  const latStr = getVal('gfLat').trim();
  const lonStr = getVal('gfLon').trim();
  const radius = getFloat('gfRadius', 100.0);

  if (!name) {
    showToast('Ponle un nombre a la geocerca (Casa, Trabajo…).', 'warning');
    return;
  }
  if (!latStr || !lonStr) {
    showToast('Toca el mapa para situar la geocerca.', 'warning');
    return;
  }

  const isCharging = getChecked('gfIsCharging', false);

  const payload = {
    name: name,
    latitude: parseFloat(latStr),
    longitude: parseFloat(lonStr),
    radius_meters: radius || 100.0,
    notify_enter: getChecked('gfNotifyEnter', true),
    notify_exit: getChecked('gfNotifyExit', true),
    is_charging_spot: isCharging,
    cost_mode: getVal('gfCostMode', 'fixed'),
    cost_per_kwh: getFloat('gfCostPerKwh', 0.15),
    cost_valle: getFloat('gfCostValle', 0.08),
    cost_llano: getFloat('gfCostLlano', 0.14),
    cost_punta: getFloat('gfCostPunta', 0.22),
    pvpc_zone: getVal('gfPvpcZone', 'Peninsula'),
    cost_margin: getFloat('gfCostMargin', 0.0),
    cost_fallback: getFloat('gfCostFallback', 0.15),
  };

  try {
    let res;
    if (id) {
      res = await fetch(`/api/geofences/${id}`, {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(payload)
      });
    } else {
      res = await fetch('/api/geofences', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(payload)
      });
    }

    if (!res.ok) {
      const err = await res.json();
      throw new Error(err.detail || "Error del servidor");
    }

    closeGeofenceModal();
    await loadGeofences();
  } catch (err) {
    showToast(t('No se pudo guardar la geocerca: {p0}', {p0: err.message}), 'error', 6000);
  }
}

// Delete geofence
async function deleteGeofence(id) {
  const gf = (cachedGeofencesList || []).find(item => item.id == id);
  const name = gf ? gf.name : 'esta geocerca';
  if (!await confirmDialog(t('¿Eliminar la geocerca "{p0}"?', {p0: name}), { confirmText: 'Borrar', danger: true })) return;

  try {
    const res = await fetch(`/api/geofences/${id}`, { method: 'DELETE' });
    if (!res.ok) {
      const err = await res.json();
      throw new Error(err.detail || "Error al eliminar");
    }
    await loadGeofences();
  } catch (err) {
    showToast(t('No se pudo eliminar la geocerca: {p0}', {p0: err.message}), 'error', 6000);
  }
}


// ───────────────────────── Entries and exits ─────────────────────────
async function loadGeofenceEvents() {
  const box = document.getElementById('gfEventsList');
  if (!box || !currentVin) return;
  try {
    const res = await fetch(`/api/geofences/events/${currentVin}?limit=30`);
    if (!res.ok) throw new Error(res.status);
    const events = await res.json();
    if (!events.length) { box.innerHTML = '<div class="costs-empty">Todavía no hay entradas ni salidas registradas.</div>'; return; }
    box.innerHTML = events.map((e) => `
      <div class="gf-event ${e.kind === 'enter' ? 'is-enter' : 'is-exit'}">
        <span class="gf-event-kind">${e.kind === 'enter' ? 'Entra' : 'Sale'}</span>
        <div class="gf-event-main">
          <strong>${escapeHtml(e.geofence_name)}</strong>
          <small>${escapeHtml(serverDate(e.timestamp).toLocaleString(I18n.locale, { day: '2-digit', month: 'short', hour: '2-digit', minute: '2-digit' }))}${e.battery_pct !== null && e.battery_pct !== undefined ? ` · ${Math.round(e.battery_pct)} %` : ''}${e.odometer_km ? ` · ${Math.round(e.odometer_km).toLocaleString(I18n.locale)} km` : ''}</small>
        </div>
      </div>`).join('');
  } catch (err) {
    box.innerHTML = '<div class="costs-empty">No se pudieron cargar las entradas y salidas.</div>';
  }
}
