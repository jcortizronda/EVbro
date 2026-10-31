/* EVBro — Cuentas asociadas (Ajustes → Cuentas): quién más puede usar tus coches y con qué permiso, y coches sin cuenta de Ebro.
 * Only managers (and the admin, in their own space) see this. Levels: view < edit < control; the owner is always you.
 * (Script clásico: comparte el ámbito global con los demás ficheros; ver el orden de carga en templates/index.html.)
 */

let accMembers = [];
const aEl = (id) => document.getElementById(id);
const LEVEL_LABELS = { '': 'Sin acceso', view: 'Ver', edit: 'Ver y editar', view_control: 'Ver y controlar', control: 'Ver, editar y controlar' };

function ownCars() { return (allVehiclesList || []).filter((v) => v.access_level === 'owner'); }

/** Ajustes → Coche: the manager's cars; tapping one makes it the car the whole app works on. */
function renderOwnCars() {
  const box = aEl('accCars');
  if (!box) return;
  const info = window.accountInfo || {};
  const cars = ownCars();
  aEl('accCarsLimit').textContent = limitText(info.vehicleLimit, cars.length, 'coche', 'coches');
  box.innerHTML = cars.length ? cars.map((c) => `
    <button type="button" class="car-pick ${c.vin === currentVin ? 'is-current' : ''}" data-on-click="selectVehicle('${c.vin}')" aria-pressed="${c.vin === currentVin}">
      <div class="acc-row-top"><div><div class="acc-name">${escapeHtml(c.name || 'Ebro')}</div>
        <div class="acc-badges"><span class="acc-badge">${escapeHtml(c.model || '')}</span><span class="acc-badge">${c.is_manual ? 'Sin cuenta Ebro' : 'Conectado a Ebro'}</span></div></div></div>
      ${c.vin === currentVin ? '<span class="car-pick-check">Elegido</span>' : ''}
    </button>`).join('')
    : '<div class="acc-empty">Todavía no tienes ningún coche.</div>';
}

async function loadAccounts() {
  if (!aEl('accMembers')) return;
  try {
    accMembers = await fetch('/api/members').then((r) => (r.ok ? r.json() : []));
    renderAccounts();
  } catch (e) {
    console.warn('No se pudieron cargar las cuentas asociadas:', e);
  }
}

function limitText(n, used, one, many) {
  if (n === undefined || n === null) return '';
  const noun = used === 1 ? one : many;
  return n < 0 ? t('{p0} {p1} (sin límite)', {p0: used, p1: noun}) : `${used} de ${n} ${many}`;
}

function renderAccounts() {
  const info = window.accountInfo || {};
  const cars = ownCars();
  aEl('accMembersLimit').textContent = limitText(info.memberLimit, accMembers.length, 'cuenta asociada', 'cuentas asociadas');

  aEl('accMembers').innerHTML = accMembers.length ? accMembers.map((m) => {
    const links = cars.map((c) => {
      const cur = (m.access.find((a) => a.vin === c.vin) || {}).level || '';
      return `<label class="acc-link"><span>${escapeHtml(c.name || 'Ebro')}</span>
        <select class="form-control" data-on-change="setMemberLevel('${c.vin}', ${m.id}, this.value)" aria-label="Permiso de ${escapeHtml(m.username)} sobre ${escapeHtml(c.name || 'Ebro')}">
          ${Object.entries(LEVEL_LABELS).map(([k, l]) => `<option value="${k}" ${k === cur ? 'selected' : ''}>${l}</option>`).join('')}
        </select></label>`;
    }).join('');
    return `<div class="acc-row">
      <div class="acc-row-top"><div><div class="acc-name">${escapeHtml(m.username)}</div>
        <div class="acc-badges">${m.is_active ? '' : '<span class="acc-badge is-off">Desactivada</span>'}
          ${m.last_login ? `<span class="acc-badge">Último acceso ${serverDate(m.last_login).toLocaleDateString(I18n.locale)}</span>` : '<span class="acc-badge">Nunca ha entrado</span>'}</div></div></div>
      <div class="acc-links">${links || '<div class="acc-empty">Añade un coche para poder compartirlo.</div>'}</div>
      <div class="acc-actions">
        <button type="button" data-on-click="toggleMemberActive(${m.id}, ${!m.is_active})">${m.is_active ? 'Desactivar' : 'Activar'}</button>
        <button type="button" data-on-click="resetMemberPassword(${m.id})">Nueva contraseña</button>
        <button type="button" class="danger" data-on-click="removeMember(${m.id}, '${escapeHtml(m.username)}')">Borrar</button>
      </div></div>`;
  }).join('') : '<div class="acc-empty">No has creado ninguna cuenta asociada.</div>';
}

async function accCall(url, method, body) {
  const res = await fetch(url, { method, headers: { 'Content-Type': 'application/json' }, body: body ? JSON.stringify(body) : undefined });
  const data = await res.json().catch(() => ({}));
  if (!res.ok) throw new Error(Array.isArray(data.detail) ? data.detail.map((d) => d.msg).join(' · ') : (data.detail || res.status));
  return data;
}

async function setMemberLevel(vin, memberId, level) {
  try {
    if (level) await accCall(`/api/vehicles/${vin}/access/${memberId}`, 'PUT', { level });
    else await accCall(`/api/vehicles/${vin}/access/${memberId}`, 'DELETE');
    showToast('Permiso actualizado', 'success');
    loadAccounts();
  } catch (e) { showToast(t('No se pudo cambiar el permiso: {p0}', {p0: e.message}), 'error', 6000); loadAccounts(); }
}

async function toggleMemberActive(id, value) {
  try { await accCall(`/api/members/${id}`, 'PATCH', { is_active: value }); loadAccounts(); } catch (e) { showToast(e.message, 'error'); }
}
async function resetMemberPassword(id) {
  const pw = await promptDialog('Nueva contraseña (mínimo 10 caracteres). Se cerrará su sesión en todos los dispositivos.', { title: 'Cambiar la contraseña', type: 'password', autocomplete: 'new-password', confirmText: 'Cambiar' });
  if (!pw) return;
  try { await accCall(`/api/members/${id}`, 'PATCH', { new_password: pw }); showToast('Contraseña cambiada', 'success'); } catch (e) { showToast(e.message, 'error', 6000); }
}
async function removeMember(id, name) {
  if (!await confirmDialog(t('¿Borrar la cuenta «{p0}»? Perderá el acceso a tus coches. No se borra ningún dato de los coches.', {p0: name}), { confirmText: 'Borrar', danger: true })) return;
  try { await accCall(`/api/members/${id}`, 'DELETE'); loadAccounts(); } catch (e) { showToast(e.message, 'error'); }
}

// ───────────────────────── Modales ─────────────────────────
function openMemberModal() {
  for (const id of ['memUser', 'memPass']) aEl(id).value = '';
  aEl('memberModal').classList.add('open');
}
function closeMemberModal() { aEl('memberModal').classList.remove('open'); }

async function saveMember() {
  try {
    await accCall('/api/members', 'POST', { username: aEl('memUser').value.trim(), password: aEl('memPass').value });
    closeMemberModal();
    showToast('Cuenta creada. Dale el usuario y la contraseña; ahora asígnale un coche.', 'success', 6000);
    loadAccounts();
  } catch (e) { showToast(t('No se pudo crear: {p0}', {p0: e.message}), 'error', 6000); }
}

/** The brand list is the car sheet's («EBRO» first, then the usual brands, then «Otra…»): one list, copied at open time. */
function manFillBrands() {
  const sel = aEl('manBrand');
  sel.innerHTML = '';
  document.querySelectorAll('#vpBrand option:not([data-extra])').forEach((o) => sel.add(new Option(o.textContent, o.value)));
  sel.value = 'EBRO';
}
function manBrandValue() {
  const sel = aEl('manBrand');
  return sel.value === VP_BRAND_OTHER ? aEl('manBrandOther').value.trim() : sel.value;
}
/** The S700/S800/S900 list is EBRO's; any other brand types its model. */
function manOnBrandChange() {
  const other = aEl('manBrand').value === VP_BRAND_OTHER;
  aEl('manBrandOther').hidden = !other;
  const ebro = manBrandValue().toLowerCase() === 'ebro';
  aEl('manModel').hidden = !ebro;
  aEl('manModelText').hidden = ebro;
  if (!aEl('manFuel').dataset.touched) aEl('manFuel').value = ebro ? 'phev' : 'gasoline';   // an Ebro is a PHEV; anything else, petrol unless said otherwise
  if (other) aEl('manBrandOther').focus();
}

function openManualVehicleModal() {
  aEl('manName').value = '';
  manFillBrands();
  aEl('manBrandOther').value = '';
  aEl('manModel').value = 'S900 PHEV';
  aEl('manModelText').value = '';
  delete aEl('manFuel').dataset.touched;
  aEl('manFuel').onchange = () => { aEl('manFuel').dataset.touched = '1'; };
  manOnBrandChange();
  aEl('manualVehicleModal').classList.add('open');
}
function closeManualVehicleModal() { aEl('manualVehicleModal').classList.remove('open'); }

async function saveManualVehicle() {
  const name = aEl('manName').value.trim();
  if (!name) { showToast('Ponle un nombre al coche.', 'warning'); return; }
  const brand = manBrandValue();
  if (!brand) { showToast('Indica la marca del coche.', 'warning'); return; }
  const ebro = brand.toLowerCase() === 'ebro';
  const model = ebro ? aEl('manModel').value : aEl('manModelText').value.trim();
  if (!model) { showToast('Indica el modelo del coche.', 'warning'); return; }
  try {
    const created = await accCall('/api/vehicles', 'POST', { name, brand, model, fuel_type: aEl('manFuel').value });
    closeManualVehicleModal();
    showToast('Coche añadido y elegido', 'success');
    await loadVehicles();
    selectVehicle(created.vin);                       // the new car becomes the one the app works on
    renderOwnCars();
  } catch (e) { showToast(t('No se pudo añadir: {p0}', {p0: e.message}), 'error', 6000); }
}

/** Ficha del coche → zona peligrosa: borra el coche y todo lo anotado de él. */
async function deleteCurrentVehicle() {
  const v = currentVehicle();
  if (!v) return;
  const typed = await promptDialog(t('Se borrará «{p0}» y TODO lo anotado de él (viajes, cargas, gastos, mantenimiento, piezas). No se puede deshacer.\n\nEscribe el nombre del coche para confirmar.', {p0: v.name}), { title: 'Borrar el coche', placeholder: v.name || '', confirmText: 'Borrar', maxLength: 100 });
  if (typed === null) return;
  if (typed.trim() !== (v.name || '').trim()) { showToast('El nombre no coincide: no se ha borrado nada.', 'warning', 5000); return; }
  try {
    await accCall(`/api/vehicles/${v.vin}`, 'DELETE');
    showToast('Coche borrado', 'success');
    currentVin = null;
    await loadVehicles();
    switchTab('overview');
  } catch (e) { showToast(t('No se pudo borrar: {p0}', {p0: e.message}), 'error', 6000); }
}
