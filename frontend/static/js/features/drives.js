/* EVBro — Trips (Viajes): filters, summary, editing and route.
 * (Classic script: shares the global scope with the other files; see the load order in templates/index.html.)
 */

async function loadGeofencesForFilter() {
  try {
    const res = await fetch('/api/geofences');
    const geofences = await res.json();
    const sel = document.getElementById('filterGeofence');
    if (!sel) return;
    const curVal = sel.value;
    sel.innerHTML = '<option value="">Todas</option>';
    geofences.forEach(gf => {
      const opt = document.createElement('option');
      opt.value = gf.id;
      opt.innerText = gf.name;
      sel.appendChild(opt);
    });
    sel.value = curVal;
  } catch (e) {
    console.debug("Geofences filter load:", e);
  }
}

let drivesPage = { offset: 0, months: {}, lastMonth: null, params: '', gen: 0 };
let drivesPicker = null;
const drivesPickerOf = () => drivesPicker || (drivesPicker = PeriodPicker.create('drivesPeriod', { storageKey: 'evbro_period_drives', presets: ['all', '7d', '30d', '90d', 'month', 'year'], onChange: loadDrives }));

function driveFilterParams() {
  const val = (id) => { const el = document.getElementById(id); return el ? el.value.trim() : ''; };
  const params = new URLSearchParams();
  if (val('filterGeofence')) params.append('geofence_id', val('filterGeofence'));
  if (val('filterLocation')) params.append('location', val('filterLocation'));
  if (val('filterMinDistance')) params.append('min_distance', val('filterMinDistance'));
  if (val('filterMinSpeed')) params.append('min_speed', val('filterMinSpeed'));
  drivesPickerOf().params(params);                                   // period=<preset> or custom from/to
  return params;
}

/** Reloads the trip list from the first page with the current filters (summary cards and incomplete trips too). */
async function loadDrives() {
  if (!currentVin) return;
  const list = document.getElementById('drivesList');
  list.innerHTML = '<p class="hist-empty">Cargando viajes...</p>';
  historyMore(document.getElementById('drivesMore'), false);
  drivesPage = { offset: 0, months: {}, lastMonth: null, params: driveFilterParams().toString(), gen: drivesPage.gen + 1 };
  cachedDrives = {};
  await fetchDrivesPage(true);
}

async function loadMoreDrives() {
  const btn = document.getElementById('drivesMore');
  if (btn) btn.disabled = true;
  await fetchDrivesPage(false);
}

async function fetchDrivesPage(first) {
  const list = document.getElementById('drivesList');
  const vin = currentVin;
  const gen = drivesPage.gen;
  try {
    const res = await fetch(`/api/vehicles/${vin}/drives?${drivesPage.params}&limit=${HISTORY_PAGE_SIZE}&offset=${drivesPage.offset}`);
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    const data = await res.json();
    if (gen !== drivesPage.gen) return;                   // a newer load (filters or another car) took over
    const drives = data.drives || [];
    if (first) {
      renderDriveSummary(data.summary || {});
      renderIncompleteDrives(data.incomplete_drives || []);
      (data.months || []).forEach((m) => { drivesPage.months[m.month] = m; });
      list.innerHTML = '';
      if (!drives.length) list.innerHTML = '<p class="hist-empty">No se encontraron viajes con los filtros aplicados.</p>';
    }
    drives.forEach((d) => {
      cachedDrives[d.id] = d;
      historyMaybeMonth(list, drivesPage, d.start_time, (key) => {
        const m = drivesPage.months[key];
        return m ? `${m.count} ${m.count === 1 ? 'viaje' : 'viajes'} · ${historyNum(m.distance_km)} km` : '';
      });
      list.appendChild(driveRow(d));
    });
    drivesPage.offset += drives.length;
    historyMore(document.getElementById('drivesMore'), !!data.has_more);
  } catch (err) {
    if (first) list.innerHTML = `<p class="hist-empty is-error">${t('Error al cargar viajes: {p0}', {p0: escapeHtml(err.message || err)})}</p>`;
    else showToast('No se pudieron cargar más viajes.', 'error');
    historyMore(document.getElementById('drivesMore'), !first);
  }
}

/** Battery spent on a trip in % (the stored figure, or start − end). */
function driveBatteryUsed(d) {
  if (d.battery_used_pct !== null && d.battery_used_pct !== undefined) return d.battery_used_pct;
  if (d.start_battery_pct !== null && d.end_battery_pct !== null && d.start_battery_pct !== undefined && d.end_battery_pct !== undefined) {
    return d.start_battery_pct - d.end_battery_pct;
  }
  return null;
}

/** Start or end of a trip for the lists: the address, or «Sin posición» for a car the cloud never places (some S800). */
function drivePlace(address) {
  return escapeHtml(address || t('Sin posición'));
}

function driveRow(d) {
  const used = driveBatteryUsed(d);
  // Spent on the row: battery % and the petrol if any, else the energy; everything else is in the detail
  const spent = [];
  if (used !== null) spent.push(`${used > 0 ? '−' : ''}${Math.round(Math.abs(used))} %`);
  if (d.fuel_used_l) spent.push(`${historyNum(d.fuel_used_l)} L`);
  else if (d.energy_consumed_kwh) spent.push(`${historyNum(d.energy_consumed_kwh)} kWh`);
  const head = {
    when: `${d.start_estimated ? '≈ ' : ''}${escapeHtml(historyWhen(d.start_time))}`,
    place: `${drivePlace(d.start_address)} → ${drivePlace(d.end_address)}`,
    big: `${escapeHtml(historyNum(d.distance_km))} km`,
    small: escapeHtml(spent.length ? spent.join(' · ') : historyDuration(d.duration_seconds)),
  };

  const pct = (v) => (v === null || v === undefined ? null : `${Math.round(v)} %`);
  const battery = pct(d.start_battery_pct) && pct(d.end_battery_pct) ? `${pct(d.start_battery_pct)} → ${pct(d.end_battery_pct)}` : null;
  const consumption = [
    d.consumption_kwh_100km ? `${historyNum(d.consumption_kwh_100km)} kWh/100 km` : '',
    d.consumption_l_100km ? `${historyNum(d.consumption_l_100km)} l/100 km` : '',
  ].filter(Boolean).join(' · ');
  const facts = historyFacts([
    ['Duración', historyDuration(d.duration_seconds)],
    ['Batería gastada', used !== null ? `${Math.round(used)} %` : null],
    ['Batería', battery],
    ['Energía', d.energy_consumed_kwh ? `${historyNum(d.energy_consumed_kwh)} kWh` : null],
    ['Gasolina', d.fuel_used_l ? `${historyNum(d.fuel_used_l)} L` : null],
    ['Depósito', pct(d.start_fuel) && pct(d.end_fuel) ? `${pct(d.start_fuel)} → ${pct(d.end_fuel)}` : null],
    ['Repostado en ruta', d.fuel_refuelled_pct ? `${Math.round(d.fuel_refuelled_pct)} %` : null],
    ['Consumo', consumption || null],
    ['Vel. media', d.speed_avg_kmh ? `${Math.round(d.speed_avg_kmh)} km/h` : null],
    ['Vel. máxima', d.speed_max_kmh ? `${Math.round(d.speed_max_kmh)} km/h` : null],
    ['Potencia máx.', d.max_power_kw ? `${Math.round(d.max_power_kw)} kW` : null],
    ['Temp. exterior', d.outside_temp_avg !== null && d.outside_temp_avg !== undefined ? `${historyNum(d.outside_temp_avg)} °C` : null],
    ['Climatización', d.climate_used ? 'Usada' : null],
  ]);
  // No start nor end position: the cloud never placed the car during this trip (no route, no addresses)
  const noPosition = (d.start_lat === null || d.start_lat === undefined) && (d.end_lat === null || d.end_lat === undefined);
  const detail = `
    <p class="hist-route"><b>${drivePlace(d.start_address)}</b> → <b>${drivePlace(d.end_address)}</b></p>
    ${facts}
    ${d.start_estimated ? '<p class="hist-note">Inicio estimado: el viaje se detectó con el coche ya en marcha; la hora, el origen y el odómetro de inicio son los de la última lectura en parado.</p>' : ''}
    ${noPosition ? '<p class="hist-note">Sin posición: la nube no da la ubicación de este coche, así que el viaje no tiene ruta ni direcciones; los kilómetros son del cuentakilómetros.</p>' : ''}
    ${d.notes ? `<p class="hist-note">${escapeHtml(d.notes)}</p>` : ''}
    <div class="hist-actions">
      ${noPosition ? '' : `<button type="button" class="btn-secondary btn-sm" data-on-click="openRouteModal(${Number(d.id)})">Ver trazado</button>
      <a class="btn-secondary btn-sm" href="/api/drives/${Number(d.id)}/gpx" download title="Descargar la ruta en GPX">GPX</a>`}
      <button type="button" class="btn-secondary btn-sm needs-edit" data-on-click="openEditDriveModal(${Number(d.id)})">Editar</button>
      <button type="button" class="btn-secondary btn-sm needs-edit is-danger" data-on-click="deleteDrive(${Number(d.id)})">Borrar</button>
    </div>`;
  const row = historyRow(head, detail);
  if (spent.length) row.querySelector('.hist-small').classList.add('is-used');
  return row;
}

/** «Resumen del periodo» cards: totals of every filtered trip, computed by the server. */
function renderDriveSummary(summary) {
  const energy = summary.total_energy_kwh || 0;
  document.getElementById('sumEnergy').innerText = energy >= 1000 ? `${(energy / 1000).toFixed(2)} MWh` : `${energy.toFixed(1)} kWh`;
  document.getElementById('sumFuel').innerText = t('{p0} L Gasolina', {p0: (summary.total_fuel_l || 0).toFixed(1)});

  const durDays = summary.total_duration_days || 0;
  const durHours = Math.floor((summary.total_duration_seconds || 0) / 3600);
  const durMins = Math.round(((summary.total_duration_seconds || 0) % 3600) / 60);
  document.getElementById('sumDuration').innerText = durDays >= 1 ? `${durDays} días` : `${durHours}h ${durMins}min`;
  document.getElementById('sumDurationDays').innerText = t('{p0} horas netas', {p0: ((summary.total_duration_seconds || 0) / 3600).toFixed(1)});

  document.getElementById('sumDistance').innerText = `${(summary.total_distance_km || 0).toFixed(1)} km`;
  document.getElementById('sumTripsCount').innerText = `${summary.count || 0} viajes`;

  document.getElementById('sumAvgConsumption').innerText = `${historyNum((summary.avg_consumption_wh_km || 0) / 10)} kWh/100 km`;
  document.getElementById('sumAvgFuel').innerText = `${(summary.avg_fuel_consumption_l_100km || 0).toFixed(2)} l/100km`;
}

function renderIncompleteDrives(incomplete) {
  const wrapper = document.getElementById('incompleteDrivesWrapper');
  const tbody = document.getElementById('incompleteDrivesTableBody');
  if (!incomplete.length) { wrapper.style.display = 'none'; return; }
  wrapper.style.display = 'block';
  tbody.innerHTML = '';
  incomplete.forEach((inc) => {
    cachedDrives[inc.id] = inc;
    const tr = document.createElement('tr');
    const start = serverDate(inc.start_time).toLocaleString(I18n.locale, { dateStyle: 'short', timeStyle: 'short' });
    tr.innerHTML = `
      <td>${escapeHtml(start)}</td>
      <td>${drivePlace(inc.start_address)}</td>
      <td>${escapeHtml(inc.distance_km)} km</td>
      <td>${inc.start_battery_pct !== null && inc.start_battery_pct !== undefined ? escapeHtml(Math.round(inc.start_battery_pct)) : '--'}%</td>
      <td><button class="btn-primary needs-edit u-padding-0p3rem-0p75rem_font-size-0p8rem" data-on-click="openEditDriveModal(${Number(inc.id)})">Corregir / Cerrar</button></td>
    `;
    tbody.appendChild(tr);
  });
}

// Edit trip modal
function openEditDriveModal(driveId) {
  const d = cachedDrives[driveId];
  if (!d) return;

  document.getElementById('editDriveId').value = driveId;
  document.getElementById('editStartAddr').value = d.start_address || "";
  document.getElementById('editEndAddr').value = d.end_address || "";
  document.getElementById('editDist').value = d.distance_km || 0;
  document.getElementById('editDurationMin').value = d.duration_min || 0;
  document.getElementById('editBatteryUsed').value = d.battery_used_pct || 0;
  document.getElementById('editFuelUsed').value = d.fuel_used_l || 0;
  document.getElementById('editSpeedAvg').value = d.speed_avg_kmh || 0;
  document.getElementById('editSpeedMax').value = d.speed_max_kmh || 0;
  document.getElementById('editNotes').value = d.notes || "";

  document.getElementById('editDriveModal').classList.add('open');
}

function closeEditDriveModal() {
  document.getElementById('editDriveModal').classList.remove('open');
}

async function saveEditedDrive() {
  const driveId = document.getElementById('editDriveId').value;
  if (!driveId) return;

  const durationMin = parseFloat(document.getElementById('editDurationMin').value) || 0;
  const payload = {
    start_address: document.getElementById('editStartAddr').value.trim(),
    end_address: document.getElementById('editEndAddr').value.trim(),
    distance_km: parseFloat(document.getElementById('editDist').value) || 0,
    duration_seconds: Math.round(durationMin * 60),
    battery_used_pct: parseFloat(document.getElementById('editBatteryUsed').value) || 0,
    fuel_used_l: parseFloat(document.getElementById('editFuelUsed').value) || 0,
    speed_avg_kmh: parseFloat(document.getElementById('editSpeedAvg').value) || 0,
    speed_max_kmh: parseFloat(document.getElementById('editSpeedMax').value) || 0,
    notes: document.getElementById('editNotes').value.trim()
  };

  try {
    const res = await fetch(`/api/drives/${driveId}`, {
      method: 'PUT',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(payload)
    });
    if (res.ok) {
      closeEditDriveModal();
      showToast('Viaje guardado.', 'success');
      loadDrives();
    } else {
      showToast('No se ha podido guardar el viaje.', 'error');
    }
  } catch (e) {
    showToast('Error de red al guardar el viaje.', 'error');
  }
}

async function deleteDrive(driveId) {
  if (!await confirmDialog("¿Borrar este viaje del historial? No se puede deshacer.", { confirmText: 'Borrar', danger: true })) return;
  try {
    const res = await fetch(`/api/drives/${driveId}`, { method: 'DELETE' });
    if (res.ok) {
      showToast('Viaje borrado.', 'success');
      loadDrives();
    } else {
      showToast('No se ha podido borrar el viaje.', 'error');
    }
  } catch (e) {
    showToast('Error de red al borrar el viaje.', 'error');
  }
}

// Trip route modal
async function openRouteModal(driveId) {
  const modal = document.getElementById('routeMapModal');
  modal.classList.add('open');

  if (!routeModalMap) {
    routeModalMap = L.map('routeModalMap', {
      zoomControl: true,
      attributionControl: false
    }).setView([40.4168, -3.7038], 13);

    createStandardTileLayer().addTo(routeModalMap);
  }

  setTimeout(() => routeModalMap.invalidateSize(), 200);

  try {
    const res = await fetch(`/api/drives/${driveId}/track`);
    const trackData = await res.json();
    const geojson = trackData.geojson;
    const points = trackData.points || [];

    const statsElem = document.getElementById('routeModalStats');
    statsElem.innerHTML = `
      <span><strong>Distancia:</strong> ${geojson.properties.distance_km} km</span>
      <span><strong>Duración:</strong> ${Math.round(geojson.properties.duration_s / 60)} min</span>
      <span><strong>Vel. Máx:</strong> ${geojson.properties.speed_max} km/h</span>
      <span><strong>Puntos GPS:</strong> ${points.length}</span>
    `;

    if (routePolyline) routeModalMap.removeLayer(routePolyline);
    if (routeStartMarker) routeModalMap.removeLayer(routeStartMarker);
    if (routeEndMarker) routeModalMap.removeLayer(routeEndMarker);

    if (points.length > 0) {
      const latLngs = points.map(p => [p.lat, p.lon]);
      routePolyline = L.polyline(latLngs, {
        color: themeAccent(),
        weight: 5,
        opacity: 0.9,
        lineCap: 'round'
      }).addTo(routeModalMap);

      const startIcon = L.divIcon({
        className: 'pin-start',
        html: `<div class="u-background-00e676_width-14px_height-14px_border-radius-50pct_border-2px-solid-fff_box-shadow-0-0-8px-00e676"></div>`,
        iconSize: [16, 16]
      });
      const endIcon = L.divIcon({
        className: 'pin-end',
        html: `<div class="u-background-ff3d00_width-14px_height-14px_border-radius-50pct_border-2px-solid-fff_box-shadow-0-0-8px-ff3d00"></div>`,
        iconSize: [16, 16]
      });

      routeStartMarker = L.marker(latLngs[0], { icon: startIcon }).addTo(routeModalMap).bindPopup("Inicio del viaje");
      routeEndMarker = L.marker(latLngs[latLngs.length - 1], { icon: endIcon }).addTo(routeModalMap).bindPopup("Fin del viaje");

      routeModalMap.fitBounds(routePolyline.getBounds(), { padding: [40, 40] });
    }
    setupRouteReplay(points);
  } catch (e) {
    console.error("Error loading the route:", e);
  }
}

function closeRouteModal() {
  stopRouteReplay();
  document.getElementById('routeMapModal').classList.remove('open');
}

// ───────────────────────── Trip replay ─────────────────────────
let routeReplay = { points: [], index: 0, timer: null, marker: null };

function setupRouteReplay(points) {
  stopRouteReplay();
  routeReplay.points = points;
  routeReplay.index = 0;
  const box = document.getElementById('routeReplay');
  const slider = document.getElementById('routeReplaySlider');
  box.hidden = points.length < 2;
  slider.max = Math.max(0, points.length - 1);
  slider.value = 0;
  if (routeReplay.marker) { routeModalMap.removeLayer(routeReplay.marker); routeReplay.marker = null; }
  if (points.length >= 2) {
    routeReplay.marker = L.circleMarker([points[0].lat, points[0].lon], { radius: 8, color: '#fff', weight: 2, fillColor: themeAccent(), fillOpacity: 1 }).addTo(routeModalMap);
    seekRouteReplay(0);
  }
}

function seekRouteReplay(i) {
  const p = routeReplay.points[Number(i)];
  if (!p || !routeReplay.marker) return;
  routeReplay.index = Number(i);
  document.getElementById('routeReplaySlider').value = routeReplay.index;
  routeReplay.marker.setLatLng([p.lat, p.lon]);
  const t = serverDate(p.ts).toLocaleTimeString(I18n.locale, { hour: '2-digit', minute: '2-digit' });
  const parts = [t];
  if (p.speed !== null && p.speed !== undefined) parts.push(`${Math.round(p.speed)} km/h`);
  if (p.battery !== null && p.battery !== undefined) parts.push(`${Math.round(p.battery)} %`);
  document.getElementById('routeReplayInfo').textContent = parts.join(' · ');
}

function toggleRouteReplay() {
  if (routeReplay.timer) { stopRouteReplay(); return; }
  if (routeReplay.index >= routeReplay.points.length - 1) routeReplay.index = 0;
  document.getElementById('routeReplayBtn').textContent = 'Pausa';
  const stepMs = Math.max(40, Math.min(250, 20000 / routeReplay.points.length));   // the whole trip in about 20 s
  routeReplay.timer = setInterval(() => {
    if (routeReplay.index >= routeReplay.points.length - 1) { stopRouteReplay(); return; }
    seekRouteReplay(routeReplay.index + 1);
  }, stepMs);
}

function stopRouteReplay() {
  if (routeReplay.timer) clearInterval(routeReplay.timer);
  routeReplay.timer = null;
  const btn = document.getElementById('routeReplayBtn');
  if (btn) btn.textContent = 'Reproducir';
}

// ───────────────────────── Frequent places ─────────────────────────
async function loadPlaces() {
  const card = document.getElementById('placesCard');
  if (!card || !currentVin) return;
  try {
    const res = await fetch(`/api/vehicles/${currentVin}/places?limit=6`);
    if (!res.ok) throw new Error(res.status);
    const { places } = await res.json();
    card.hidden = !places.length;
    document.getElementById('placesList').innerHTML = places.map((p) => `
      <div class="place-row">
        <div class="place-main">
          <strong>${escapeHtml(p.address || `${p.latitude.toFixed(4)}, ${p.longitude.toFixed(4)}`)}</strong>
          <small>${p.visits} ${p.visits === 1 ? 'visita' : 'visitas'}${p.last_visit ? ` · última ${escapeHtml(historyWhen(p.last_visit))}` : ''}${p.geofence_id ? ' · con geocerca' : ''}</small>
        </div>
        ${p.geofence_id ? '' : `<button type="button" class="btn-secondary btn-sm" data-on-click="openCreateGeofenceModal(${p.latitude}, ${p.longitude})">Crear geocerca</button>`}
      </div>`).join('');
  } catch (e) {
    card.hidden = true;
  }
}
