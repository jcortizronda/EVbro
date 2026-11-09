/* EVBro — Vehicles: picker, header, car editing and telemetry reading.
 * (Classic script: shares the global scope with the other files; see the load order in templates/index.html.)
 */

/**
 * Whether the car has ever sent one of these raw keys (`reported_keys` of the vehicle, filled by the server read after read).
 * The models differ: the S900 never sends the cabin temperature, the S700 has no middle seats… What a car never reports is not
 * shown to it. No list yet (a car never read) counts as «yes»: nothing is hidden before the first readings.
 */
function carReports(...keys) {
  const vehicle = (typeof currentVehicle === 'function') ? currentVehicle() : null;
  const seen = vehicle && Array.isArray(vehicle.reported_keys) ? vehicle.reported_keys : null;
  if (!seen || !seen.length) return true;
  return keys.some((k) => seen.includes(k));
}

/**
 * Syncs the vehicle header information (name and VIN).
 * Keeps the VIN visible in the main view.
 * @param {Object} [vehicle] - Optional vehicle metadata (name, model, vin).
 */
function updateVehicleHeaderInfo(vehicle) {
  const nameEl = document.getElementById('vehicleName');
  const vinEl = document.getElementById('vehicleVin');

  if (!vehicle && currentVin) {
    vehicle = (allVehiclesList || []).find(v => v.vin === currentVin);
  }

  const displayName = vehicle?.name || (vehicle?.model ? `Ebro ${vehicle.model}` : 'Ebro');
  const modelName = vehicle?.model || 'Ebro Auto';
  const plateText = vehicle?.license_plate || vehicle?.model || 'EBRO';

  if (nameEl) {
    nameEl.innerText = displayName;
  }

  // Update the header subtitle
  const headerSubtitle = document.getElementById('headerVehicleSubtitle');
  if (headerSubtitle) {
    headerSubtitle.innerText = modelName;
  }

  // Update the plate in the tab headers
  const plateBadge = document.getElementById('heroPlateBadge');
  if (plateBadge) plateBadge.innerText = plateText;
  const energyPlateBadge = document.getElementById('energyHeroPlateBadge');
  if (energyPlateBadge) energyPlateBadge.innerText = plateText;

  // Update the info panel in Ajustes -> Cuenta
  const setName = document.getElementById('settingsVehicleNameDisplay');
  if (setName) setName.innerText = displayName;
  const setModel = document.getElementById('settingsVehicleModelDisplay');
  if (setModel) setModel.innerText = modelName;
  const setPlate = document.getElementById('settingsVehiclePlateDisplay');
  if (setPlate) setPlate.innerText = vehicle?.license_plate || '-';
  const setVin = document.getElementById('settingsVehicleVinDisplay');
  if (setVin) setVin.innerText = vehicle?.vin || currentVin || '-';
  if (typeof applyVehiclePhoto === 'function') applyVehiclePhoto(vehicle);

  if (typeof applyAccessLevel === 'function') applyAccessLevel();

  // Update the browser title
  document.title = t('EVBro — {p0}', {p0: displayName});

  if (vinEl) {
    const vin = vehicle?.vin || currentVin;
    vinEl.innerText = vin ? `VIN: ${vin}` : 'VIN: No detectado';
    vinEl.title = vin ? t('Número de Identificación de Vehículo (VIN): {p0}', {p0: vin}) : 'Sin VIN detectado';
  }
}

async function loadVehicles() {
  try {
    const res = await fetch('/api/vehicles');
    const vehicles = await res.json();
    const sel = document.getElementById('vehicleSelector');
    sel.innerHTML = '';

    if (!vehicles || vehicles.length === 0) {
      sel.innerHTML = '<option value="">Sin vehículos registrados</option>';
      allVehiclesList = [];
      updateVehicleHeaderInfo(null);
      return;
    }

    allVehiclesList = vehicles;

    vehicles.forEach(v => {
      const opt = document.createElement('option');
      opt.value = v.vin;
      opt.innerText = vehicleLabel(v);
      sel.appendChild(opt);
    });

    // The car chosen last time (per browser) if it is still there; otherwise the first one
    let saved = null;
    try { saved = localStorage.getItem(VEHICLE_KEY); } catch (e) { /* no storage */ }
    const chosen = vehicles.find(v => v.vin === saved) || vehicles[0];
    currentVin = chosen.vin;
    sel.value = currentVin;

    // Update name and VIN in the main header
    updateVehicleHeaderInfo(chosen);
    applyVehicleCapabilities();                     // fuel type and «no Ebro account» decide what the interface shows
    if (window.WidgetBoards) WidgetBoards.onTab(window.currentTabName || 'overview');   // the shown page's widgets, for this car (Inicio is the first screen)
    if (window.Dock) Dock.sync();                    // the account's bottom bar (features/dock.js)
    if (window.AppShortcuts) AppShortcuts.run();     // the icon's shortcuts (manifest.json): open a page or ask before an order

    // Load telemetry and charge details of the chosen car
    loadLatestTelemetry(currentVin);
    loadLatestCharge(currentVin);
  } catch (err) {
    console.error("Error loading vehicles:", err);
  }
}

const VEHICLE_KEY = 'evbro_vin';

/** Name shown in the car pickers: the car's name, and a hint when it has no Ebro account. */
function vehicleLabel(v) {
  return `${v.name || 'Ebro'}${v.is_manual ? ' · sin cuenta Ebro' : ''}`;
}

/** Changes the car the whole app works on: every tab, the car sheet, expenses, costs, maintenance, notifications thresholds... */
function selectVehicle(vin) {
  if (!vin) return;
  currentVin = vin;
  try { localStorage.setItem(VEHICLE_KEY, vin); } catch (e) { /* does not matter */ }
  const sel = document.getElementById('vehicleSelector');
  if (sel && sel.value !== vin) sel.value = vin;
  updateVehicleHeaderInfo();
  applyVehicleCapabilities();
  loadLatestTelemetry(vin);
  loadLatestCharge(vin);
  // reload what is on screen for the new car (each tab loads its data when it is opened)
  if (window.currentTabName) switchTab(window.currentTabName);
  if (window.currentTabName === 'settings' && window.currentSettingsSubTab) switchSettingsSubTab(window.currentSettingsSubTab);
}

function onVehicleChange(vin) { selectVehicle(vin); }

// Load the latest telemetry
async function loadLatestTelemetry(vin) {
  if (!vin) return;
  try {
    const res = await fetch(`/api/vehicles/${vin}/latest`);
    if (res.ok) {
      const data = await res.json();
      applyTelemetry(data);
    }
  } catch (err) {
    console.error("Error loading the latest telemetry:", err);
  }
}

async function triggerVehicleDiscovery() {
  // «Sincronizar» (Ajustes → Cuenta and the account dialog): asks Ebro for the cars of the connected account.
  const buttons = [...document.querySelectorAll('[data-on-click*="triggerVehicleDiscovery"]')];
  buttons.forEach((b) => { b.disabled = true; b.dataset.label = b.textContent; b.textContent = 'Buscando en Ebro…'; });
  try {
    const res = await fetch('/api/vehicles/discover', { method: 'POST' });
    const data = await res.json().catch(() => ({}));
    if (!res.ok) { showToast(data.detail || 'No se ha podido sincronizar con Ebro.', 'error', 6000); return; }
    const n = data.count || 0;
    showToast(n ? `Sincronizado: ${n === 1 ? 'un coche' : `${n} coches`} en tu cuenta de Ebro.` : 'Sincronizado: tu cuenta de Ebro no tiene coches nuevos.', 'success', 5000);
    loadVehicles();
  } catch (err) {
    showToast('Error de red al sincronizar con Ebro.', 'error');
  } finally {
    buttons.forEach((b) => { b.disabled = false; b.textContent = b.dataset.label || 'Sincronizar'; });
    if (typeof checkAccountStatus === 'function') checkAccountStatus();      // keeps «Sincronizar» disabled when there is no account
  }
}


/** What the chosen car can do decides what the app shows (body[data-fuel], body[data-manual], body[data-gps]):
 *  Energía and the battery only for PHEV/EV; Repostajes and the tank not for an EV; and for a car without an Ebro account
 *  nothing that needs live data (figure, orders, tyres, Confort, ranges) — only the map, and only when a Traccar fix exists. */
async function applyVehicleCapabilities() {
  const v = typeof currentVehicle === 'function' ? currentVehicle() : null;
  const fuel = (v && v.fuel_type) || 'phev';
  const manual = !!(v && v.is_manual);
  document.body.dataset.fuel = fuel;
  document.body.dataset.manual = manual ? '1' : '0';
  const electric = fuel === 'phev' || fuel === 'ev';
  if ((!electric || manual) && window.currentTabName === 'charges') switchTab('overview');
  if (manual && window.currentTabName === 'climate') switchTab('overview');
  if (fuel === 'ev' && window.currentTabName === 'refuels') switchTab('overview');
  if (!manual) { document.body.dataset.gps = '1'; return; }
  // a manual car shows the map only when something (Traccar) has ever placed it
  try {
    const res = await fetch(`/api/vehicles/${currentVin}/gps/latest`);
    const data = res.ok ? await res.json() : { position: null };
    document.body.dataset.gps = data.position ? '1' : '0';
  } catch (e) { document.body.dataset.gps = '0'; }
}
const applyFuelType = applyVehicleCapabilities;      // older name
