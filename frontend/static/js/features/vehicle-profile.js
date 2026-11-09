/* EVBro — Vehicle sheet (Ajustes → Coche): identity, purchase, paperwork, tyres, energy, and the photo.
 * (Classic script: shares the global scope with the other files; see the load order in templates/index.html.)
 */

const VP_DEFAULT_BANNER = "url('/static/images/banner-exterior.jpg')";
const vpEl = (id) => document.getElementById(id);

function currentVehicle() {
  return (allVehiclesList || []).find((v) => v.vin === currentVin) || null;
}

/** The header pencil and the «Cambiar en la ficha» links lead to the full sheet. */
function openVehicleSettings() {
  switchTab('vehicle');                                    // the «Coches» section (Más → Coches)
  if (typeof setVehiclePane === 'function') setVehiclePane('data');
}
function openEditVehicleModal() { openVehicleSettings(); }

/** Vehicle photo as the Inicio header background (if there is none, the usual image). */
function applyVehiclePhoto(v) {
  const banner = vpEl('overviewBanner');
  if (!banner) return;
  banner.style.setProperty('--banner', v && v.photo_url ? `url('${v.photo_url}')` : VP_DEFAULT_BANNER);
}

function renderPhoto(v) {
  const has = !!(v && v.photo_url);
  const img = vpEl('vpPhotoImg');
  if (has) img.src = v.photo_url;
  img.hidden = !has;
  vpEl('vpPhotoEmpty').hidden = has;
  vpEl('vpPhotoRemove').hidden = !has;
  vpEl('vpPhotoPick').textContent = has ? 'Cambiar foto' : 'Subir foto';
}

// ───────────────────────── Collapsible sections ─────────────────────────
// Which sections are open is remembered per browser; the first time only «Identidad» is open (less vertical scrolling on a phone).
const VP_FOLD_KEY = 'evbro_vp_open';

function vpOpenSections() {
  try { const v = JSON.parse(localStorage.getItem(VP_FOLD_KEY)); if (Array.isArray(v)) return v; } catch (e) { /* no storage */ }
  return ['identity'];
}
function vpSaveSections() {
  const open = [...document.querySelectorAll('#tabVehicle .vp-section[open]')].map((d) => d.dataset.key);   // Ajustes reuses the look, not this memory
  try { localStorage.setItem(VP_FOLD_KEY, JSON.stringify(open)); } catch (e) { /* does not matter */ }
}
function initVehicleFolds() {
  const saved = vpOpenSections();
  document.querySelectorAll('#tabVehicle .vp-section').forEach((d) => {
    d.open = saved.includes(d.dataset.key);
    d.addEventListener('toggle', vpSaveSections);
  });
}
function foldVehicleSections(open) {
  document.querySelectorAll('#tabVehicle .vp-section').forEach((d) => { d.open = open; });
  vpSaveSections();
}
/** Opens the section that holds a field (before focusing it after a validation error). */
function vpRevealField(el) { const d = el && el.closest('details'); if (d) d.open = true; }

// ───────────────────────── Brand, model and chassis number ─────────────────────────
const VP_BRAND_OTHER = '__other';
const VP_CHASSIS_RE = /^[A-Z0-9]{11,17}$/;

/** Selects the stored brand; one that is not in the list is kept as an extra option (like the model). */
function vpSetBrand(brand) {
  const sel = vpEl('vpBrand');
  sel.querySelectorAll('option[data-extra]').forEach((o) => o.remove());
  const value = (brand || '').trim() || 'EBRO';
  const match = Array.from(sel.options).find((o) => o.value !== VP_BRAND_OTHER && o.value.toLowerCase() === value.toLowerCase());
  if (match) sel.value = match.value;
  else {
    const extra = new Option(value, value); extra.dataset.extra = '1';
    sel.add(extra, sel.options[sel.options.length - 1]);           // before «Otra…»
    sel.value = value;
  }
  vpEl('vpBrandOther').value = '';
  vpEl('vpBrandOther').hidden = true;
}

function vpBrandValue() {
  const sel = vpEl('vpBrand');
  return sel.value === VP_BRAND_OTHER ? vpEl('vpBrandOther').value.trim() : sel.value;
}

/** The S700/S800/S900 list is EBRO's; any other brand types its model. */
function vpIsEbroBrand() { return vpBrandValue().toLowerCase() === 'ebro'; }

function vpSyncModelField() {
  const ebro = vpIsEbroBrand();
  vpEl('vpModel').hidden = !ebro;
  vpEl('vpModelText').hidden = ebro;
}

function vpOnBrandChange() {
  const other = vpEl('vpBrand').value === VP_BRAND_OTHER;
  vpEl('vpBrandOther').hidden = !other;
  if (other) vpEl('vpBrandOther').focus();
  vpSyncModelField();
}

/** Connected car: its real VIN, read-only. Manual car: its `vin` is internal, so the field edits the informative chassis number. */
function vpRenderChassis(v) {
  const input = vpEl('vpVin');
  input.readOnly = !v.is_manual;
  input.value = (v.is_manual ? v.chassis_number : v.vin) || '';
  input.placeholder = v.is_manual ? '17 letras y números' : '';
  vpEl('vpVinHint').textContent = v.is_manual
    ? 'Solo informativo: este coche no tiene cuenta Ebro y EVBro lo identifica por su cuenta.'
    : 'Se lee de tu cuenta Ebro; no se puede cambiar.';
}

function loadVehicleProfile() {
  const v = currentVehicle();
  if (!v) return;
  const set = (id, val) => { const el = vpEl(id); if (el) el.value = val ?? ''; };
  const day = (iso) => (iso ? iso.slice(0, 10) : '');
  set('vpName', v.name); vpSetBrand(v.brand); set('vpTrim', v.trim); set('vpColor', v.color); set('vpEnvLabel', v.env_label);
  // Closed list of models; if the car carries another value (older installs) it is kept as an extra option
  const sel = vpEl('vpModel');
  sel.querySelectorAll('option[data-extra]').forEach((o) => o.remove());
  if (v.model && !Array.from(sel.options).some((o) => o.value === v.model)) {
    const extra = new Option(v.model, v.model); extra.dataset.extra = '1'; sel.add(extra, 0);
  }
  sel.value = v.model && Array.from(sel.options).some((o) => o.value === v.model) ? v.model : 'S900 PHEV';
  set('vpModelText', /^S[789]00 PHEV$/.test(v.model || '') || v.model === 'Ebro Auto' ? '' : v.model);
  vpSyncModelField();
  set('vpYear', v.model_year); set('vpPlate', v.license_plate);
  vpRenderChassis(v);
  set('vpPurchasePrice', v.purchase_price); set('vpPurchaseOdo', v.purchase_odometer ?? 0);
  set('vpRegDate', day(v.registration_date)); set('vpDeliveryDate', day(v.delivery_date));
  set('vpInsurer', v.insurance_company); set('vpPolicy', v.insurance_policy);
  set('vpInsCoverage', v.insurance_coverage); set('vpInsPhone', v.insurance_assistance_phone); set('vpInsRenewal', day(v.insurance_renewal_date));
  updateInsurancePhoneLink();
  set('vpTyreFrontSize', v.tyre_front_size); set('vpTyreRearSize', v.tyre_rear_size);
  set('vpTyreFrontBar', v.tyre_front_pressure_bar); set('vpTyreRearBar', v.tyre_rear_pressure_bar);
  set('vpCapacity', v.battery_capacity_kwh ?? 19.27); set('vpTank', v.fuel_tank_liters ?? 50); set('vpFuelType', v.fuel_type || 'phev');
  renderPhoto(v);
  if (typeof loadVehicleDocuments === 'function') loadVehicleDocuments();
}

async function saveVehicleProfile() {
  const v = currentVehicle();
  if (!v) { showToast('No hay ningún vehículo seleccionado.', 'warning'); return; }
  const text = (id) => vpEl(id).value.trim() || null;
  const num = (id) => { const x = parseFloat(vpEl(id).value.replace(',', '.')); return isNaN(x) ? null : x; };
  const day = (id) => { const x = vpEl(id).value; return x ? `${x}T00:00:00` : null; };     // calendar day, no time zone
  const brand = vpBrandValue();
  if (!brand) {
    showToast('Escribe la marca del coche.', 'warning');
    vpRevealField(vpEl('vpBrandOther')); vpEl('vpBrandOther').focus();
    return;
  }
  const model = vpIsEbroBrand() ? vpEl('vpModel').value : vpEl('vpModelText').value.trim();
  if (!model) {
    showToast('Escribe el modelo del coche.', 'warning');
    vpRevealField(vpEl('vpModelText')); vpEl('vpModelText').focus();
    return;
  }
  const body = {
    name: vpEl('vpName').value.trim() || 'Ebro', brand, model,
    model_year: num('vpYear'), license_plate: text('vpPlate'),
    purchase_price: num('vpPurchasePrice'), purchase_odometer: num('vpPurchaseOdo') ?? 0,
    registration_date: day('vpRegDate'), delivery_date: day('vpDeliveryDate'),
    insurance_company: text('vpInsurer'), insurance_policy: text('vpPolicy'),
    insurance_coverage: text('vpInsCoverage'), insurance_assistance_phone: text('vpInsPhone'), insurance_renewal_date: day('vpInsRenewal'),
    trim: text('vpTrim'), color: text('vpColor'), env_label: vpEl('vpEnvLabel').value || null,
    tyre_front_size: text('vpTyreFrontSize'), tyre_rear_size: text('vpTyreRearSize'),
    tyre_front_pressure_bar: num('vpTyreFrontBar'), tyre_rear_pressure_bar: num('vpTyreRearBar'),
  };
  for (const [key, id] of [['battery_capacity_kwh', 'vpCapacity'], ['fuel_tank_liters', 'vpTank']]) {
    const n = num(id);
    if (n !== null) body[key] = n;
  }
  body.fuel_type = vpEl('vpFuelType').value;
  if (v.is_manual) {
    const chassis = vpEl('vpVin').value.replace(/\s+/g, '').toUpperCase();
    if (chassis && !VP_CHASSIS_RE.test(chassis)) {
      showToast('El número de bastidor debe tener entre 11 y 17 letras o números.', 'warning', 6000);
      vpRevealField(vpEl('vpVin')); vpEl('vpVin').focus();
      return;
    }
    body.chassis_number = chassis || null;
  }
  const btn = vpEl('vpSave');
  btn.disabled = true;
  try {
    const res = await fetch(`/api/vehicles/${v.vin}`, { method: 'PUT', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) });
    const data = await res.json().catch(() => ({}));
    if (!res.ok) throw new Error(Array.isArray(data.detail) ? data.detail.map((d) => `${d.loc?.slice(-1)[0]}: ${d.msg}`).join(' · ') : (data.detail || res.status));
    replaceVehicle(data);
    showToast('Ficha del vehículo guardada', 'success');
    loadVehicleProfile();                                               // the brand decides the model field
    if (typeof loadMaintenance === 'function') loadMaintenance();      // dates and brand change the official plan and the ITV
  } catch (e) {
    showToast(t('No se pudo guardar: {p0}', {p0: e.message || e}), 'error', 6000);
  } finally {
    btn.disabled = false;
  }
}

/** Replaces the vehicle in the in-memory list and refreshes whatever shows it. */
function replaceVehicle(updated) {
  const i = (allVehiclesList || []).findIndex((x) => x.vin === updated.vin);
  if (i !== -1) allVehiclesList[i] = updated;
  const sel = document.getElementById('vehicleSelector');
  if (sel) Array.from(sel.options).forEach((o) => { if (o.value === updated.vin) o.innerText = vehicleLabel(updated); });
  updateVehicleHeaderInfo(updated);
  if (typeof applyVehicleCapabilities === 'function') applyVehicleCapabilities();
}

// ───────────────────────── Photo ─────────────────────────
async function uploadVehiclePhoto(file) {
  const v = currentVehicle();
  if (!v || !file) return;
  if (!['image/jpeg', 'image/png', 'image/webp'].includes(file.type)) { showToast('La foto debe ser JPG, PNG o WebP.', 'warning'); return; }
  if (file.size > 6 * 1024 * 1024) { showToast('La foto pesa más de 6 MB.', 'warning'); return; }
  const form = new FormData();
  form.append('file', file);
  try {
    const res = await fetch(`/api/vehicles/${v.vin}/photo`, { method: 'POST', body: form });
    const data = await res.json().catch(() => ({}));
    if (!res.ok) throw new Error(data.detail || res.status);
    replaceVehicle(data);
    renderPhoto(data);
    showToast('Foto actualizada', 'success');
    if (typeof loadStorageUsage === 'function') loadStorageUsage();
  } catch (e) {
    showToast(t('No se pudo subir la foto: {p0}', {p0: e.message || e}), 'error', 6000);
  }
}

async function removeVehiclePhoto() {
  const v = currentVehicle();
  if (!v || !await confirmDialog('¿Quitar la foto del vehículo?', { confirmText: 'Borrar', danger: true })) return;
  const res = await fetch(`/api/vehicles/${v.vin}/photo`, { method: 'DELETE' });
  if (res.ok) { const data = await res.json(); replaceVehicle(data); renderPhoto(data); if (typeof loadStorageUsage === 'function') loadStorageUsage(); }
}

document.addEventListener('DOMContentLoaded', () => {
  vpEl('vpPhotoPick')?.addEventListener('click', () => vpEl('vpPhotoInput').click());
  vpEl('vpPhotoInput')?.addEventListener('change', (e) => { uploadVehiclePhoto(e.target.files[0]); e.target.value = ''; });
  vpEl('vpPhotoRemove')?.addEventListener('click', removeVehiclePhoto);
});

/* Factory figures of the EBRO models (services/vehicle_profiles.py, one table for the server and the sheet): chosen from the list,
 * they fill the sheet (the person can still correct them). Models without an entry leave the fields as they are. */
let ebroModelSpecs = null;
async function loadModelSpecs() {
  if (ebroModelSpecs) return ebroModelSpecs;
  try {
    const res = await fetch('/api/vehicles/specs');
    ebroModelSpecs = res.ok ? await res.json() : {};
  } catch (e) { ebroModelSpecs = {}; }
  return ebroModelSpecs;
}
async function vpOnModelChange() {
  const spec = (await loadModelSpecs())[vpEl('vpModel').value];
  if (!spec || !vpIsEbroBrand()) return;
  vpEl('vpEnvLabel').value = spec.env_label;
  vpEl('vpTank').value = spec.fuel_tank_liters;
  vpEl('vpCapacity').value = spec.battery_capacity_kwh;
  vpEl('vpFuelType').value = spec.fuel_type;
  if (typeof showToast === 'function') showToast('Etiqueta, depósito, batería y motor rellenados con los datos del modelo.', 'info', 3500);
}

document.addEventListener('DOMContentLoaded', () => { vpEl('vpBrand')?.addEventListener('change', vpOnBrandChange);
  vpEl('vpModel')?.addEventListener('change', vpOnModelChange);
  vpEl('vpBrandOther')?.addEventListener('input', vpSyncModelField);
});

document.addEventListener('DOMContentLoaded', initVehicleFolds);

/** The call button next to the assistance phone: a `tel:` link that only shows when there is a number. */
function updateInsurancePhoneLink() {
  const input = document.getElementById('vpInsPhone');
  const link = document.getElementById('vpInsPhoneCall');
  if (!input || !link) return;
  const digits = (input.value || '').replace(/[^\d+]/g, '');
  link.hidden = digits.length < 3;
  link.href = `tel:${digits}`;
}
document.addEventListener('DOMContentLoaded', () => {
  const input = document.getElementById('vpInsPhone');
  if (input) input.addEventListener('input', updateInsurancePhoneLink);
});
