/* EVBro — admin console. No dependencies on the rest of the web app: it is another application, on another port.
 * The admin manages accounts, invitations, limits and server settings; of the managers it only sees counts and status, never their cars.
 * All text coming from the server is rendered with textContent (never innerHTML) and there are no inline handlers (CSP). */
'use strict';

const $ = (id) => document.getElementById(id);
const STATES = { active: 'Activa', used: 'Usada', expired: 'Caducada', revoked: 'Anulada' };
let setupRequired = false;
let managers = [];

/** Builds an element: h('button', {class: 'ghost', onclick: fn}, 'text', childNode). */
function h(tag, attrs = {}, ...kids) {
  const el = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs)) {
    if (k.startsWith('on')) el.addEventListener(k.slice(2), v);
    else if (v !== false && v !== null && v !== undefined) el.setAttribute(k, v === true ? '' : v);
  }
  for (const kid of kids.flat()) if (kid !== null && kid !== undefined) el.append(kid.nodeType ? kid : document.createTextNode(String(kid)));
  return el;
}

let toastTimer = null;
function toast(msg, isError = false, ms = 4500) {
  const t = $('toast');
  t.textContent = msg;
  t.className = isError ? 'toast error' : 'toast';
  t.hidden = false;
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => { t.hidden = true; }, ms);
}

async function api(url, method = 'GET', body) {
  const res = await fetch(url, { method, credentials: 'same-origin', headers: body ? { 'Content-Type': 'application/json' } : {}, body: body ? JSON.stringify(body) : undefined });
  const data = await res.json().catch(() => ({}));
  if (res.status === 401 && !url.startsWith('/api/auth/')) { showAuth(); throw new Error('Sesión caducada.'); }
  if (!res.ok) throw new Error(Array.isArray(data.detail) ? data.detail.map((d) => d.msg).join(' · ') : (data.detail || `HTTP ${res.status}`));
  return data;
}

const fmtDate = (iso) => (iso ? new Date(iso).toLocaleDateString(I18n.locale) : '—');
const limit = (n) => (n < 0 ? '∞' : String(n));
const storageLimit = (mb) => (mb < 0 ? 'sin límite' : `${Number(mb).toLocaleString(I18n.locale)} MB`);
/** Empty field = null (the server default); otherwise an integer, or NaN when it is not a number. */
const optionalInt = (id) => { const raw = $(id).value.trim(); return raw === '' ? null : parseInt(raw, 10); };

// ───────────────────────── own dialog (no browser alert / confirm / prompt) ─────────────────────────
let askResolve = null;
/** Opens the console's dialog. Without `input` it resolves true/false; with it, the text or null when cancelled. */
function ask(message, { title = 'Confirmar', okText = 'Aceptar', danger = false, input = null } = {}) {
  const dlg = $('dlgAsk');
  $('askTitle').textContent = title;
  $('askMessage').textContent = message;
  $('askField').hidden = !input;
  if (input) {
    $('askLabel').textContent = input.label || '';
    $('askInput').type = input.type || 'text';
    $('askInput').value = input.value || '';
    $('askInput').placeholder = input.placeholder || '';
    $('askInput').autocomplete = input.autocomplete || 'off';
  }
  $('askOk').textContent = okText;
  $('askOk').className = danger ? 'danger' : 'primary';
  if (askResolve) askResolve(input ? null : false);
  dlg.showModal();
  if (input) setTimeout(() => $('askInput').focus(), 30);
  return new Promise((resolve) => { askResolve = (v) => { askResolve = null; if (dlg.open) dlg.close(); resolve(v); }; });
}
const confirmAsk = (message, opts = {}) => ask(message, opts);
const promptAsk = (message, opts = {}) => ask(message, { ...opts, input: opts.input || { } });
$('formAsk').addEventListener('submit', (ev) => { ev.preventDefault(); if (askResolve) askResolve($('askField').hidden ? true : $('askInput').value); });
$('askCancel').addEventListener('click', () => { if (askResolve) askResolve($('askField').hidden ? false : null); });
$('dlgAsk').addEventListener('cancel', (ev) => { ev.preventDefault(); if (askResolve) askResolve($('askField').hidden ? false : null); });

// ───────────────────────── sign-in ─────────────────────────
function showAuth() {
  $('viewApp').hidden = true;
  $('who').hidden = true;
  $('viewAuth').hidden = false;
  $('authTitle').textContent = setupRequired ? 'Crear el administrador' : 'Iniciar sesión';
  $('authHint').textContent = setupRequired
    ? 'Aún no hay administrador. Escribe el código de configuración que aparece en el log del servidor (docker compose logs app) y elige tu usuario y contraseña. El administrador no tiene coches: gestiona el servidor.'
    : 'Solo para el administrador del servidor.';
  $('rowCode').hidden = !setupRequired;
  $('rowPass2').hidden = !setupRequired;
  $('authSubmit').textContent = setupRequired ? 'Crear administrador y entrar' : 'Entrar';
  $('authPass').autocomplete = setupRequired ? 'new-password' : 'current-password';
  $('authError').hidden = true;
}

function showApp(username) {
  $('viewAuth').hidden = true;
  $('viewApp').hidden = false;
  $('who').hidden = false;
  $('whoName').textContent = username;
  loadAll();
}

async function init() {
  try {
    const s = await api('/api/auth/status');
    setupRequired = s.setup_required;
    if (s.authenticated) showApp(s.username); else showAuth();
  } catch (e) { showAuth(); $('authError').textContent = 'No hay conexión con el servidor.'; $('authError').hidden = false; }
}

$('authForm').addEventListener('submit', async (ev) => {
  ev.preventDefault();
  const username = $('authUser').value.trim();
  const password = $('authPass').value;
  const fail = (m) => { $('authError').textContent = m; $('authError').hidden = false; };
  if (!username || !password) return fail('Escribe el usuario y la contraseña.');
  if (setupRequired && password !== $('authPass2').value) return fail('Las contraseñas no coinciden.');
  try {
    const body = setupRequired ? { username, password, setup_code: $('authCode').value.trim() } : { username, password };
    const r = await api(setupRequired ? '/api/auth/setup' : '/api/auth/login', 'POST', body);
    $('authPass').value = ''; $('authPass2').value = '';
    setupRequired = false;
    showApp(r.username);
  } catch (e) { fail(e.message); }
});

$('btnLogout').addEventListener('click', async () => { try { await api('/api/auth/logout', 'POST'); } catch (e) { /* does not matter */ } init(); });
$('btnPassword').addEventListener('click', async () => {
  const current = await promptAsk('Escribe tu contraseña actual.', { title: 'Cambiar contraseña', okText: 'Siguiente', input: { label: 'Contraseña actual', type: 'password', autocomplete: 'current-password' } });
  if (!current) return;
  const next = await promptAsk('Mínimo 10 caracteres. Se cerrarán tus otras sesiones.', { title: 'Cambiar contraseña', okText: 'Cambiar', input: { label: 'Contraseña nueva', type: 'password', autocomplete: 'new-password' } });
  if (!next) return;
  try { await api('/api/auth/password', 'POST', { current_password: current, new_password: next }); toast('Contraseña cambiada.'); } catch (e) { toast(e.message, true, 7000); }
});

// ───────────────────────── tabs ─────────────────────────
document.querySelectorAll('.tab').forEach((tab) => tab.addEventListener('click', () => {
  if (tab.dataset.tab === 'stats') loadStats();
  document.querySelectorAll('.tab').forEach((t) => { const on = t === tab; t.classList.toggle('is-active', on); t.setAttribute('aria-selected', String(on)); });
  if (tab.dataset.tab === 'audit') loadAudit();
  for (const name of ['managers', 'stats', 'invites', 'server', 'catalog', 'audit']) $('tab' + name[0].toUpperCase() + name.slice(1)).hidden = tab.dataset.tab !== name;
}));

async function loadAll() { await Promise.all([loadManagers(), loadStats(), loadInvites(), loadServer(), loadCatalog(), loadCatalogSource(), loadCatalogKeys(), loadAudit()]); }

// ───────────────────────── managers ─────────────────────────
async function loadManagers() {
  try { managers = await api('/api/admin/users'); } catch (e) { return; }
  const body = $('tblManagers').querySelector('tbody');
  body.replaceChildren();
  const rows = managers.filter((u) => u.role === 'manager');
  $('emptyManagers').hidden = rows.length > 0;
  for (const u of rows) {
    const chips = [h('span', { class: u.is_active ? 'chip ok' : 'chip off' }, u.is_active ? 'Activo' : 'Desactivado')];
    if (u.read_only) chips.push(h('span', { class: 'chip warn' }, 'Solo lectura'));
    body.append(h('tr', {},
      h('td', {}, h('strong', {}, u.username)),
      h('td', { class: 'limit' }, `${u.vehicle_count} / ${limit(u.vehicle_limit)}`),
      h('td', { class: 'limit' }, `${u.member_count} / ${limit(u.member_limit)}`),
      h('td', { class: 'limit', title: u.storage_limit_mb === null ? 'Límite por defecto del servidor' : null },
        `${bytes(u.storage_used_bytes)} / ${storageLimit(u.storage_limit_effective_mb)}`),
      h('td', {}, h('span', { class: u.ebro_connected ? 'chip ok' : 'chip' }, u.ebro_connected ? 'Conectada' : 'Sin cuenta')),
      h('td', {}, fmtDate(u.expires_at)),
      h('td', {}, fmtDate(u.last_login)),
      h('td', {}, chips),
      h('td', {}, h('div', { class: 'row-actions' },
        h('button', { type: 'button', onclick: () => editLimits(u) }, 'Límites'),
        h('button', { type: 'button', onclick: () => setActive(u, !u.is_active) }, u.is_active ? t('Desactivar') : t('Activar')),
        h('button', { type: 'button', onclick: () => resetPassword(u) }, 'Contraseña'),
        h('button', { type: 'button', class: 'danger', onclick: () => deleteSpace(u) }, 'Borrar espacio')))));
  }
}

$('btnNewManager').addEventListener('click', () => { $('formManager').hidden = false; $('mgrUser').focus(); });
$('btnCancelManager').addEventListener('click', () => { $('formManager').hidden = true; });
$('formManager').addEventListener('submit', async (ev) => {
  ev.preventDefault();
  const n = (id) => { const v = parseInt($(id).value, 10); return Number.isNaN(v) ? null : v; };
  try {
    const sto = optionalInt('mgrSto');
    if (Number.isNaN(sto)) { toast('El espacio debe ser un número de MB.', true); return; }
    await api('/api/admin/managers', 'POST', { username: $('mgrUser').value.trim(), password: $('mgrPass').value, vehicle_limit: n('mgrVeh'), member_limit: n('mgrMem'),
      storage_limit_mb: sto });
    toast('Gestor creado. Dale el usuario y la contraseña.');
    $('formManager').reset(); $('mgrVeh').value = 2; $('mgrMem').value = 5; $('formManager').hidden = true;
    loadManagers();
  } catch (e) { toast(e.message, true, 7000); }
});

let limitsUser = null;
function editLimits(u) {
  limitsUser = u;
  $('limTitle').textContent = t('Límites de «{p0}»', {p0: u.username});
  $('limVeh').value = u.vehicle_limit; $('limMem').value = u.member_limit;
  $('limSto').value = u.storage_limit_mb === null ? '' : u.storage_limit_mb;
  $('limSto').placeholder = t('Por defecto ({p0})', {p0: storageLimit(u.storage_limit_effective_mb)});
  $('limExp').value = u.expires_at ? u.expires_at.slice(0, 10) : ''; $('limRo').checked = !!u.read_only;
  $('dlgLimits').showModal();
  $('limMem').focus();
}
$('btnLimCancel').addEventListener('click', () => $('dlgLimits').close());
$('formLimits').addEventListener('submit', async (ev) => {
  ev.preventDefault();
  const v = parseInt($('limVeh').value, 10), m = parseInt($('limMem').value, 10), sto = optionalInt('limSto');
  if (Number.isNaN(v) || Number.isNaN(m) || Number.isNaN(sto)) { toast('Los límites deben ser números.', true); return; }
  try {
    await api(`/api/admin/users/${limitsUser.id}`, 'PATCH', { vehicle_limit: v, member_limit: m, storage_limit_mb: sto, read_only: $('limRo').checked,
      expires_at: $('limExp').value ? `${$('limExp').value}T23:59:59` : null });
    $('dlgLimits').close();
    toast('Límites guardados.');
    loadManagers();
  } catch (e) { toast(e.message, true, 7000); }
});

const setActive = (u, value) => api(`/api/admin/users/${u.id}`, 'PATCH', { is_active: value }).then(loadManagers).catch((e) => toast(e.message, true));

async function resetPassword(u) {
  const pw = await promptAsk(t('Mínimo 10 caracteres. Se cerrará la sesión de «{p0}» en todos los dispositivos.', {p0: u.username}), { title: t('Nueva contraseña para {p0}', {p0: u.username}), okText: 'Cambiar', input: { label: 'Contraseña nueva', type: 'password', autocomplete: 'new-password' } });
  if (!pw) return;
  api(`/api/admin/users/${u.id}`, 'PATCH', { new_password: pw }).then(() => toast('Contraseña cambiada.')).catch((e) => toast(e.message, true, 7000));
}

async function deleteSpace(u) {
  const typed = await promptAsk(t('Se borrará TODO el espacio de «{p0}»: sus coches, historiales, cuentas asociadas y datos. No se puede deshacer.', {p0: u.username}), { title: 'Borrar espacio', okText: 'Borrar', danger: true, input: { label: 'Escribe su nombre de usuario para confirmar', placeholder: u.username } });
  if (typed === null) return;
  api(`/api/admin/users/${u.id}?confirm=${encodeURIComponent(typed.trim())}`, 'DELETE')
    .then((r) => { toast(t('Espacio borrado ({p0} coches, {p1} cuentas asociadas).', {p0: r.removed.vehicles, p1: r.removed.members}), false, 7000); loadManagers(); })
    .catch((e) => toast(e.message, true, 7000));
}

// ───────────────────────── statistics ─────────────────────────
const num = (n) => (n === null || n === undefined ? '—' : Number(n).toLocaleString(I18n.locale));
const bytes = (n) => {
  if (n === null || n === undefined) return '—';
  const u = ['B', 'KB', 'MB', 'GB']; let i = 0, v = n;
  while (v >= 1024 && i < u.length - 1) { v /= 1024; i += 1; }
  return `${v.toLocaleString(I18n.locale, { maximumFractionDigits: v < 10 && i ? 1 : 0 })} ${u[i]}`;
};
const uptime = (s) => { const d = Math.floor(s / 86400), hh = Math.floor((s % 86400) / 3600), m = Math.floor((s % 3600) / 60); return d ? `${d} d ${hh} h` : hh ? `${hh} h ${m} min` : `${m} min`; };
const kpi = (value, label, note) => h('div', { class: 'kpi' }, h('span', { class: 'kpi-v' }, value), h('span', { class: 'kpi-l' }, label), note ? h('span', { class: 'kpi-n' }, note) : null);

async function loadStats() {
  let d;
  try { d = await api('/api/admin/stats'); } catch (e) { return; }
  const a = d.accounts, v = d.vehicles, u = d.usage, a2 = d.activity, st = d.data;
  $('statsStamp').textContent = t('Actualizado {p0}.', {p0: new Date(d.generated_at + 'Z').toLocaleTimeString(I18n.locale, { hour: '2-digit', minute: '2-digit' })});
  $('kpiMain').replaceChildren(
    kpi(num(a.total), 'Cuentas de usuario', t('{p0} gestores · {p1} asociadas', {p0: num(a.managers), p1: num(a.members)})),
    kpi(num(v.total), 'Coches', t('{p0} conectados · {p1} manuales', {p0: num(v.connected), p1: num(v.manual)})),
    kpi(num(v.ebro_accounts), 'Cuentas de Ebro conectadas'),
    kpi(num(a2.logins_7d), 'Cuentas activas (7 días)', t('{p0} en las últimas 24 h', {p0: num(a2.logins_24h)})),
    kpi(bytes(d.storage.database_bytes), 'Base de datos', t('+ {p0} en ficheros', {p0: bytes(d.storage.files_bytes)})),
    kpi(uptime(d.server.uptime_s), 'Servidor encendido'));
  $('kpiUsage').replaceChildren(
    kpi(num(u.commands_24h), 'Órdenes al coche (24 h)'), kpi(num(u.commands_7d), 'Órdenes (7 días)', `${num(u.commands_failed_7d)} fallidas`),
    kpi(num(u.notifications_24h), 'Avisos enviados (24 h)', `${num(u.notifications_failed_24h)} fallidos`),
    kpi(num(u.trips_7d), 'Viajes (7 días)'), kpi(num(u.charges_7d), 'Cargas (7 días)'),
    kpi(num(u.refuels_30d + u.expenses_30d), 'Repostajes y gastos (30 días)'));
  $('kpiAccounts').replaceChildren(
    kpi(num(a2.sessions_open), 'Sesiones abiertas'), kpi(num(a2.logins_30d), 'Entraron en 30 días'), kpi(num(a.new_30d), 'Altas en 30 días'),
    kpi(num(a.inactive), 'Desactivadas'), kpi(num(a.expiring_30d), 'Caducan en 30 días'),
    kpi(`${num(d.invitations.active)} / ${num(d.invitations.used)}`, 'Invitaciones activas / usadas'));
  $('kpiData').replaceChildren(
    kpi(num(st.trips), 'Viajes'), kpi(num(st.charges), 'Cargas'), kpi(num(st.snapshots), 'Lecturas de telemetría', st.oldest_snapshot ? `desde ${fmtDate(st.oldest_snapshot)}` : null),
    kpi(num(st.expenses + st.refuels), 'Gastos y repostajes'), kpi(num(st.maintenance_logs), 'Intervenciones'), kpi(num(st.parts), 'Piezas anotadas'));
  drawGrowth(d.growth_weekly);
  const body = $('tblUsage').querySelector('tbody');
  body.replaceChildren(...d.managers.map((m) => h('tr', {},
    h('td', {}, h('strong', {}, m.username), m.is_active ? '' : ' (desactivado)'), h('td', {}, num(m.vehicles)), h('td', {}, num(m.members)),
    h('td', {}, num(m.commands_7d)), h('td', {}, bytes(m.storage_bytes)), h('td', {}, fmtDate(m.last_login)))));
}
$('btnRefreshStats').addEventListener('click', loadStats);

/** Bars of new accounts per week, as SVG built with attributes (the page's CSP forbids inline styles). */
function drawGrowth(weeks) {
  const svg = $('chartGrowth');
  svg.replaceChildren();
  const ns = 'http://www.w3.org/2000/svg';
  const max = Math.max(1, ...weeks.map((w) => w.new_accounts));
  const slot = 480 / weeks.length;
  weeks.forEach((w, i) => {
    const bar = 96 * (w.new_accounts / max), x = i * slot + slot * 0.18, bw = slot * 0.64;
    const rect = document.createElementNS(ns, 'rect');
    for (const [k, val] of Object.entries({ x, y: 120 - bar, width: bw, height: Math.max(bar, 1.5), rx: 4, class: 'bar' })) rect.setAttribute(k, val);
    const tip = document.createElementNS(ns, 'title'); tip.textContent = t('Semana del {p0}: {p1}', {p0: fmtDate(w.week_start), p1: w.new_accounts}); rect.append(tip);
    const label = document.createElementNS(ns, 'text');
    for (const [k, val] of Object.entries({ x: x + bw / 2, y: 140, 'text-anchor': 'middle', class: 'axis' })) label.setAttribute(k, val);
    label.textContent = new Date(w.week_start).toLocaleDateString(I18n.locale, { day: '2-digit', month: '2-digit' });
    const count = document.createElementNS(ns, 'text');
    for (const [k, val] of Object.entries({ x: x + bw / 2, y: 114 - bar, 'text-anchor': 'middle', class: 'val' })) count.setAttribute(k, val);
    count.textContent = w.new_accounts || '';
    svg.append(rect, label, count);
  });
}

// ───────────────────────── invitations ─────────────────────────
async function loadInvites() {
  let list;
  try { list = await api('/api/admin/invites'); } catch (e) { return; }
  const body = $('tblInvites').querySelector('tbody');
  body.replaceChildren();
  $('emptyInvites').hidden = list.length > 0;
  for (const i of list) {
    body.append(h('tr', {},
      h('td', {}, i.note || 'Sin nota'),
      h('td', {}, h('span', { class: i.state === 'active' ? 'chip ok' : 'chip' }, STATES[i.state] || i.state)),
      h('td', {}, fmtDate(i.created_at)), h('td', {}, fmtDate(i.expires_at)), h('td', {}, i.used_by || '—'),
      h('td', {}, i.state === 'active'
        ? h('button', { type: 'button', class: 'danger', onclick: () => revokeInvite(i.id) }, 'Anular') : '')));
  }
}

$('formInvite').addEventListener('submit', async (ev) => {
  ev.preventDefault();
  const n = (id) => { const v = parseInt($(id).value, 10); return Number.isNaN(v) ? undefined : v; };
  try {
    const inv = await api('/api/admin/invites', 'POST', { note: $('invNote').value.trim() || null, vehicle_limit: n('invVeh'), member_limit: n('invMem'),
      storage_limit_mb: n('invSto'), days: n('invDays') });
    $('tokenValue').textContent = inv.token;
    $('tokenBox').hidden = false;
    $('invNote').value = '';
    toast('Invitación creada. Copia el código ahora.');
    loadInvites();
  } catch (e) { toast(e.message, true, 7000); }
});

$('btnCopy').addEventListener('click', async () => {
  try { await navigator.clipboard.writeText($('tokenValue').textContent); toast('Código copiado.'); }
  catch (e) { toast('Selecciónalo y cópialo a mano.', true); }
});

async function revokeInvite(id) {
  if (!await confirmAsk('¿Anular esta invitación? Dejará de funcionar.', { title: 'Anular invitación', okText: 'Anular', danger: true })) return;
  api(`/api/admin/invites/${id}`, 'DELETE').then(loadInvites).catch((e) => toast(e.message, true));
}

// ───────────────────────── server ─────────────────────────
const info = (x) => (x.count ? `${x.count.toLocaleString(I18n.locale)} guardados${x.oldest ? `, el más antiguo del ${fmtDate(x.oldest)}` : ''}` : 'Nada guardado');

async function loadServer() {
  try {
    const r = await api('/api/settings/retention');
    $('retTrips').value = r.config.trips_days; $('retCharges').value = r.config.charges_days; $('retSnap').value = r.config.snapshots_days;
    $('retTripsInfo').textContent = info(r.stored.trips); $('retChargesInfo').textContent = info(r.stored.charges); $('retSnapInfo').textContent = info(r.stored.snapshots);
    const p = await api('/api/server/polling');
    $('polParked').value = p.poll_interval_parked_min; $('polPlugged').value = p.poll_interval_plugged_min; $('polCharging').value = p.poll_interval_charging_min;
    $('polDriving').value = p.poll_interval_driving_min; $('polIdle').value = p.poll_interval_driving_idle_min;
  } catch (e) { /* no session: the sign-in screen is already shown */ }
}

$('formRetention').addEventListener('submit', async (ev) => {
  ev.preventDefault();
  const n = (id) => Math.max(0, Math.min(3650, parseInt($(id).value || '0', 10) || 0));
  if (!await confirmAsk('Se borrará de forma definitiva el historial que supere los plazos nuevos. ¿Continuar?', { title: 'Aplicar plazos', okText: 'Aplicar', danger: true })) return;
  try {
    await api('/api/settings/retention', 'PUT', { trips_days: n('retTrips'), charges_days: n('retCharges'), snapshots_days: n('retSnap') });
    const run = await api('/api/settings/retention/run', 'POST');
    const total = Object.values(run.deleted || {}).reduce((a, b) => a + b, 0);
    toast(total ? t('Plazos guardados. Se borraron {p0} registros antiguos.', {p0: total}) : 'Plazos guardados.');
    loadServer();
  } catch (e) { toast(e.message, true, 7000); }
});

$('formPolling').addEventListener('submit', async (ev) => {
  ev.preventDefault();
  const n = (id) => parseInt($(id).value, 10);
  try {
    await api('/api/server/polling', 'POST', { parked_min: n('polParked'), plugged_min: n('polPlugged'), charging_min: n('polCharging'), driving_min: n('polDriving'), driving_idle_min: n('polIdle') });
    toast('Intervalos de sondeo guardados.');
    loadServer();
  } catch (e) { toast(e.message, true, 7000); }
});

init();


// ───────────────────────── Shared catalogue ─────────────────────────
const CAT_STATES = { pending: 'Pendiente', approved: 'Aprobada', rejected: 'Rechazada' };
let catalogRows = [];
const qty = (n) => (n === null || n === undefined ? null : Number(n).toLocaleString(I18n.locale));
async function loadCatalog() {
  try { catalogRows = await api(`/api/admin/catalog?status=${encodeURIComponent($('catStatus').value)}`); } catch (e) { return; }
  const body = $('tblCatalog').querySelector('tbody');
  body.replaceChildren();
  $('emptyCatalog').hidden = catalogRows.length > 0;
  for (const e of catalogRows) {
    const state = h('span', { class: e.status === 'approved' ? 'chip ok' : (e.status === 'rejected' ? 'chip warn' : 'chip') }, CAT_STATES[e.status] || e.status);
    const detail = e.reject_reason ? h('small', { class: 'hint' }, ' ' + e.reject_reason) : '';
    // Each category has its own specification (viscosity, size, standard...) and unit (litres, units): show them by name
    const details = [e.spec ? `${e.spec_label}: ${e.spec}` : null, qty(e.quantity) !== null ? `${e.qty_label}: ${qty(e.quantity)}` : null].filter(Boolean).join(' · ');
    const actions = h('span', {}, h('button', { type: 'button', class: 'ghost', onclick: () => editCatalog(e) }, 'Editar'), ' ');
    if (e.status === 'pending') {
      actions.append(h('button', { type: 'button', class: 'primary', onclick: () => reviewCatalog(e.id, true) }, 'Aprobar'), ' ',
                     h('button', { type: 'button', class: 'danger', onclick: () => reviewCatalog(e.id, false) }, 'Rechazar'));
    } else if (e.confirmations) {
      actions.append(`×${e.confirmations + 1}`);
    }
    body.append(h('tr', {},
      h('td', {}, (e.fitments || []).map((f) => [f.make, f.model].filter(Boolean).join(' ') + (f.years ? ` (${f.years})` : '') + (f.oem_reference ? ` · ${t('ref. fab. {p0}', {p0: f.oem_reference})}` : '')).join(', ')),
      h('td', {}, `${e.category_label}${e.position_label ? ' · ' + e.position_label.toLowerCase() : ''}`, h('br'), h('small', { class: 'hint' }, e.name || '')),
      h('td', {}, [e.brand, e.reference].filter(Boolean).join(' · ') || '—'),
      h('td', {}, details || '—', e.notes ? h('br') : '', e.notes ? h('small', { class: 'hint' }, e.notes) : ''),
      h('td', {}, (e.proposed_by || (e.remote_note ? t('servidor «{p0}»', {p0: e.remote_note}) : '—')) + (e.proposed_at ? ` · ${fmtDate(e.proposed_at)}` : '') + (e.forwarded_at ? ' · enviada al catálogo en línea' : '')),
      h('td', {}, state, detail),
      h('td', {}, actions)));
  }
}
async function reviewCatalog(id, approve) {
  let body;
  if (!approve) {
    const reason = await promptAsk('Lo verá quien la propuso. Puede quedar vacío.', { title: 'Rechazar propuesta', okText: 'Rechazar', danger: true, input: { label: 'Motivo del rechazo' } });
    if (reason === null) return;
    body = { reason: reason.trim() || null };
  }
  api(`/api/admin/catalog/${id}/${approve ? 'approve' : 'reject'}`, 'POST', body).then(loadCatalog).catch((e) => toast(e.message, true));
}
let catalogEntry = null;
function editCatalog(e) {
  catalogEntry = e;
  $('catTitle').textContent = t('{p0}{p1} (propuesta de {p2})', {p0: e.category_label, p1: e.position_label ? ' · ' + e.position_label.toLowerCase() : '', p2: e.proposed_by || '—'});
  $('catFits').value = (e.fitments || []).map((f) => [f.make || '', f.model || '', f.years || '', f.oem_reference || ''].join(' | ').replace(/( \| )+$/, '')).join('\n');
  $('catName').value = e.name || ''; $('catBrand').value = e.brand || ''; $('catRef').value = e.reference || '';
  // Position (front / rear / spare) only for the part types that have one: pads, discs, tyres, wiper blades
  const positions = e.positions || [];
  $('catPositionWrap').hidden = !positions.length;
  $('catPosition').replaceChildren(h('option', { value: '' }, t('Sin indicar')), ...positions.map((p) => h('option', { value: p.key }, t(p.label))));
  $('catPosition').value = e.position || '';
  $('catSpecLabel').textContent = e.spec_label; $('catSpec').value = e.spec || '';
  $('catQtyLabel').textContent = e.qty_label; $('catQty').value = e.quantity === null || e.quantity === undefined ? '' : e.quantity;
  $('catNotes').value = e.notes || '';
  $('dlgCatalog').showModal();
}
$('btnCatCancel').addEventListener('click', () => $('dlgCatalog').close());
$('formCatalog').addEventListener('submit', async (ev) => {
  ev.preventDefault();
  const q = $('catQty').value.trim();
  try {
    const fitments = $('catFits').value.split('\n').map((l) => l.split('|').map((x) => x.trim())).filter((p) => p[1] || (p.length === 1 && p[0]))
      .map((p) => (p.length === 1 ? { model: p[0] } : { make: p[0] || null, model: p[1], years: p[2] || null, oem_reference: p[3] || null }));
    if (!fitments.length) { toast('Indica al menos un coche (marca | modelo | años | ref. fabricante).', true); return; }
    await api(`/api/admin/catalog/${catalogEntry.id}`, 'PATCH', {
      fitments, name: $('catName').value, brand: $('catBrand').value,
      ...((catalogEntry.positions || []).length ? { position: $('catPosition').value } : {}),
      reference: $('catRef').value, spec: $('catSpec').value, quantity: q === '' ? null : parseFloat(q), notes: $('catNotes').value });
    $('dlgCatalog').close();
    toast('Propuesta guardada.');
    loadCatalog();
  } catch (e) { toast(e.message, true, 7000); }
});
$('catStatus').addEventListener('change', loadCatalog);


// ───────────────────────── Online catalogue: this server's source and keys for others ─────────────────────────
const KEY_STATES = { active: 'Activa', expired: 'Caducada', revoked: 'Anulada' };
async function loadCatalogSource() {
  let c;
  try { c = await api('/api/admin/catalog/source'); } catch (e) { return; }
  $('srcMode').value = c.source;
  $('srcLink').value = '';
  $('srcLink').placeholder = c.has_link ? `guardado: ${c.server || 'enlace'}#…  (pega otro para cambiarlo)` : 'https://servidor#ebc_…';
  $('srcLinkInfo').textContent = c.has_link ? 'Deja el campo vacío para conservar el enlace guardado.' : '';
  const parts = [];
  if (c.source === 'remote') parts.push(t('Espejo: {p0} entradas', {p0: c.entries}));
  if (c.last_sync_at) parts.push(t('última sincronización {p0}', {p0: new Date(c.last_sync_at).toLocaleString(I18n.locale)}));
  if (c.last_error) parts.push(t('Error: {p0}', {p0: c.last_error}));
  $('srcStatus').textContent = parts.join(' · ');
  $('srcStatus').className = c.last_error ? 'hint error' : 'hint';
  $('btnCatalogSync').disabled = !c.has_link;
}
$('formCatalogSource').addEventListener('submit', async (ev) => {
  ev.preventDefault();
  const link = $('srcLink').value.trim();
  try {
    await api('/api/admin/catalog/source', 'PUT', { source: $('srcMode').value, link: link || null });
    toast('Origen del catálogo guardado.');
    loadCatalogSource();
  } catch (e) { toast(e.message, true, 8000); }
});
$('btnCatalogSync').addEventListener('click', async () => {
  $('btnCatalogSync').disabled = true;
  try {
    const r = await api('/api/admin/catalog/source/sync', 'POST');
    toast(t('Catálogo sincronizado: {p0} entradas de {p1}.', {p0: r.entries, p1: r.server}));
    loadCatalog();
  } catch (e) { toast(e.message, true, 8000); }
  loadCatalogSource();
});

async function loadCatalogKeys() {
  let data;
  try { data = await api('/api/admin/catalog/keys'); } catch (e) { return; }
  $('pubUrl').value = data.public_url || '';
  const body = $('tblKeys').querySelector('tbody');
  body.replaceChildren();
  $('emptyKeys').hidden = data.keys.length > 0;
  for (const k of data.keys) {
    body.append(h('tr', {},
      h('td', {}, k.note || '—'),
      h('td', {}, h('span', { class: k.state === 'active' ? 'chip ok' : 'chip warn' }, KEY_STATES[k.state] || k.state)),
      h('td', {}, fmtDate(k.created_at)), h('td', {}, k.expires_at ? fmtDate(k.expires_at) : 'nunca'),
      h('td', {}, k.last_used_at ? new Date(k.last_used_at).toLocaleString(I18n.locale) : '—'),
      h('td', {}, String(k.downloads)), h('td', {}, String(k.proposals)),
      h('td', {}, k.state === 'active' ? h('button', { type: 'button', class: 'danger', onclick: () => revokeCatalogKey(k.id) }, 'Anular') : '')));
  }
}
async function revokeCatalogKey(id) {
  if (!await confirmAsk('¿Anular esta clave? Ese servidor dejará de poder leer el catálogo y de enviar propuestas.', { title: 'Anular clave', okText: 'Anular', danger: true })) return;
  api(`/api/admin/catalog/keys/${id}`, 'DELETE').then(loadCatalogKeys).catch((e) => toast(e.message, true));
}
$('formPublicUrl').addEventListener('submit', async (ev) => {
  ev.preventDefault();
  try {
    await api('/api/admin/catalog/keys/public-url', 'PUT', { public_url: $('pubUrl').value.trim() || null });
    toast('URL pública guardada.');
  } catch (e) { toast(e.message, true, 7000); }
});
$('formCatalogKey').addEventListener('submit', async (ev) => {
  ev.preventDefault();
  try {
    const k = await api('/api/admin/catalog/keys', 'POST', { note: $('keyNote').value.trim() || null, days: optionalInt('keyDays') });
    const box = $('keyResult');
    box.replaceChildren(
      k.link ? 'Enlace para ese servidor (se enseña solo ahora): ' : 'Clave (se enseña solo ahora; pon la URL pública para obtener el enlace completo): ',
      h('code', {}, k.link || k.key), ' ',
      h('button', { type: 'button', class: 'ghost', onclick: () => navigator.clipboard.writeText(k.link || k.key).then(() => toast('Copiado.')) }, 'Copiar'));
    box.hidden = false;
    $('keyNote').value = ''; $('keyDays').value = '';
    loadCatalogKeys();
  } catch (e) { toast(e.message, true, 7000); }
});


// ───────────────────────── Action log ─────────────────────────
const AUDIT_LABELS = { login: 'Entrada en la consola', 'manager.create': 'Gestor creado', 'manager.delete': 'Espacio de gestor borrado', 'user.update': 'Cuenta modificada',
  'invite.create': 'Invitación creada', 'invite.revoke': 'Invitación anulada', 'catalog.approve': 'Catálogo: aprobada', 'catalog.reject': 'Catálogo: rechazada', 'catalog.edit': 'Catálogo: editada',
  'catalog.key.create': 'Catálogo: clave creada', 'catalog.key.revoke': 'Catálogo: clave anulada', 'catalog.public_url': 'Catálogo: URL pública', 'catalog.source': 'Catálogo: origen cambiado', 'catalog.sync': 'Catálogo: sincronizado' };
async function loadAudit() {
  let list;
  try { list = await api('/api/admin/audit?limit=300'); } catch (e) { return; }
  const body = $('tblAudit').querySelector('tbody');
  body.replaceChildren();
  $('emptyAudit').hidden = list.length > 0;
  for (const r of list) {
    body.append(h('tr', {}, h('td', {}, r.created_at ? new Date(r.created_at + (r.created_at.endsWith('Z') ? '' : 'Z')).toLocaleString(I18n.locale) : '—'),
      h('td', {}, r.username), h('td', {}, AUDIT_LABELS[r.action] || r.action), h('td', {}, r.target || '—'), h('td', {}, r.detail || '—'), h('td', {}, r.ip || '—')));
  }
}

// Language of the console (and of this administrator's account): saved on the server, the page reloads in it (docs/i18n.md)
(() => {
  const select = $('adminLanguage');
  if (!select) return;
  select.value = (window.I18n && I18n.lang) || 'es';
  select.addEventListener('change', async () => {
    try { await api('/api/auth/language', 'PUT', { language: select.value }); window.location.reload(); }
    catch (e) { select.value = (window.I18n && I18n.lang) || 'es'; }
  });
})();
