/* EVBro — Ebro account: status, connection and disconnection.
 * (Classic script: shares the global scope with the other files; see the load order in templates/index.html.)
 */

let currentAccountInfo = null;

// The header dot: green = account and car channel fine; orange = the account works but the car's live channel (MQTT) is stopped;
// red = no Ebro account, or the cloud no longer accepts its session and password
const CHANNEL_STOPPED = ['conflict', 'rejected', 'error', 'no_certs'];
function paintAccountDot(level, title) {
  const dot = document.getElementById('accountStatusDot');
  if (!dot) return;
  dot.classList.remove('connected', 'disconnected', 'error');
  dot.classList.add({ ok: 'connected', channel: 'disconnected', account: 'error' }[level]);
  const btn = document.getElementById('btnAccountStatus');
  if (btn) btn.title = title;
}

// The car's MQTT channel stopped because another app used the account: shown on Inicio (where nobody misses it) and in the
// account card; «Sincronizar» in either place brings it back. With the account fine, a stopped channel turns the dot orange.
async function showAccountChannelNotice() {
  const notes = ['settingsAccountChannel', 'homeChannelBanner'].map((id) => document.getElementById(id)).filter(Boolean);
  let state = null;
  try {
    const res = await fetch('/api/car-events/status');
    const s = res.ok ? await res.json() : null;
    state = s && s.enabled !== false ? s.state : null;
  } catch (e) {
    state = null;
  }
  notes.forEach((note) => { note.hidden = state !== 'conflict'; });
  const accountOk = !!(currentAccountInfo && currentAccountInfo.connected && currentAccountInfo.session_ok !== false);
  if (accountOk) {
    paintAccountDot(CHANNEL_STOPPED.includes(state) ? 'channel' : 'ok',
      CHANNEL_STOPPED.includes(state) ? t('Cuenta de Ebro conectada, pero el canal en directo del coche está parado') : t('Cuenta de Ebro conectada'));
  }
}
document.addEventListener('visibilitychange', () => { if (!document.hidden && currentAccountInfo) showAccountChannelNotice(); });

async function checkAccountStatus() {
  if (typeof currentRole === 'function' && currentRole() === 'member') return;      // members have no Ebro account
  try {
    const res = await fetch('/api/account/status');
    if (!res.ok) return;
    const data = await res.json();
    currentAccountInfo = data;
    renderPinPolicy(data);                 // the choice exists whether or not an Ebro account is connected right now

    const btnText = document.getElementById('accountBtnText');
    const settingsBadge = document.getElementById('settingsAccountBadge');
    const settingsPhone = document.getElementById('settingsAccountPhone');
    const settingsVehicles = document.getElementById('settingsAccountVehicles');
    const settingsPin = document.getElementById('settingsAccountPinStatus');
    const connectBtn = document.getElementById('btnSettingsConnect');     // «Conectar» or «Desconectar», depending on the state
    const syncBtn = document.getElementById('btnSettingsSync');           // only makes sense with a connected account

    if (data.connected) {
      // `session_ok === false`: the cloud rejects the stored session and password; only connecting the account again fixes it
      const expired = data.session_ok === false;
      if (expired) paintAccountDot('account', t('La nube de Ebro ya no acepta la sesión: vuelve a conectar la cuenta'));
      else paintAccountDot('ok', t('Cuenta de Ebro conectada'));      // orange if the car channel turns out to be stopped
      if (btnText) {
        btnText.innerText = expired ? "Sesión caducada" : (data.phone || "Conectado");
      }
      if (settingsBadge) {
        settingsBadge.className = expired ? "status-badge plugged" : "status-badge driving";
        settingsBadge.innerText = expired ? "Sesión caducada" : "Conectado";
      }
      if (settingsPhone) settingsPhone.innerText = data.phone || "Conectado";
      if (settingsVehicles) settingsVehicles.innerText = data.vehicles_count === 1 ? "1 coche" : `${data.vehicles_count} coches`;
      if (settingsPin) {
        settingsPin.innerText = data.has_pin ? "Guardado" : "No guardado";
        settingsPin.style.color = data.has_pin ? "var(--accent-green)" : "var(--text-secondary)";
      }
      if (connectBtn) {
        connectBtn.textContent = "Desconectar";
        connectBtn.className = "btn-secondary is-danger";
        connectBtn.onclick = logoutEbroAccount;
      }
      if (syncBtn) syncBtn.disabled = false;
      const pwdBtn = document.getElementById('btnEbroPassword');
      if (pwdBtn) pwdBtn.hidden = data.source !== 'web_login';      // the server's own `.env` account is changed in the file
      const pinBtn = document.getElementById('btnEbroPin');
      if (pinBtn) pinBtn.hidden = data.source !== 'web_login' || !(data.vehicles || []).length;
      showAccountChannelNotice();
    } else {
      ['btnEbroPassword', 'btnEbroPin'].forEach((id) => { const b = document.getElementById(id); if (b) b.hidden = true; });
      paintAccountDot('account', t('Sin cuenta de Ebro conectada'));
      if (btnText) {
        btnText.innerText = "Conectar Cuenta";
      }
      if (settingsBadge) {
        settingsBadge.className = "status-badge parked";
        settingsBadge.innerText = "Sin Conectar";
      }
      if (settingsPhone) settingsPhone.innerText = "No conectado";
      if (settingsVehicles) settingsVehicles.innerText = data.vehicles_count === 1 ? "1 guardado" : `${data.vehicles_count} guardados`;
      if (settingsPin) settingsPin.innerText = "No guardado";
      if (connectBtn) {
        connectBtn.textContent = "Conectar";
        connectBtn.className = "btn-primary";
        connectBtn.onclick = openAccountModal;
      }
      if (syncBtn) syncBtn.disabled = true;
      showAccountChannelNotice();            // no account, no channel: the notice goes away
    }
  } catch (err) {
    console.warn("Failed to check the account status:", err);
  }
}

async function openAccountModal() {
  await checkAccountStatus();
  const modal = document.getElementById('accountModal');
  const loginForm = document.getElementById('accountLoginForm');
  const connectedView = document.getElementById('accountConnectedView');
  const errBox = document.getElementById('accLoginError');
  if (errBox) errBox.style.display = 'none';

  if (currentAccountInfo && currentAccountInfo.connected) {
    if (loginForm) loginForm.style.display = 'none';
    if (connectedView) {
      connectedView.style.display = 'block';
      const phoneElem = document.getElementById('accConnectedPhone');
      const carsElem = document.getElementById('accConnectedCarsCount');
      if (phoneElem) phoneElem.innerText = currentAccountInfo.phone || "+34 Conectado";
      if (carsElem) carsElem.innerText = t('Vehículos vinculados: {p0}', {p0: currentAccountInfo.vehicles_count});
    }
  } else {
    if (loginForm) loginForm.style.display = 'block';
    if (connectedView) connectedView.style.display = 'none';
  }

  if (modal) modal.classList.add('open');
}

function closeAccountModal() {
  const modal = document.getElementById('accountModal');
  if (modal) modal.classList.remove('open');
  showEbroPassword(false);                                  // never left readable behind a closed dialog
}

/** «Mostrar»: the manager sees the Ebro password about to be sent (the cloud blocks the account for 5 minutes after five wrong ones). */
function showEbroPassword(show) {
  const field = document.getElementById('accPassword');
  const btn = document.getElementById('accPasswordShow');
  if (!field || !btn) return;
  field.type = show ? 'text' : 'password';
  btn.textContent = show ? t('Ocultar') : t('Mostrar');
  btn.setAttribute('aria-pressed', String(show));
}
function toggleEbroPassword() {
  showEbroPassword(document.getElementById('accPassword').type === 'password');
}

async function submitEbroLogin() {
  const area = document.getElementById('accAreaCode').value.trim() || '34';
  const phone = document.getElementById('accPhone').value.trim();
  const password = document.getElementById('accPassword').value;
  const pin = document.getElementById('accPin').value.trim();
  const errBox = document.getElementById('accLoginError');
  const btn = document.getElementById('btnSubmitLogin');
  const btnText = document.getElementById('btnLoginText');

  if (!phone || !password) {
    if (errBox) {
      errBox.innerText = "Por favor, introduce tu número de teléfono y contraseña.";
      errBox.style.display = 'block';
    }
    return;
  }

  if (errBox) errBox.style.display = 'none';
  btn.disabled = true;
  btnText.innerText = "Verificando con servidores Ebro...";

  try {
    const res = await fetch('/api/account/login', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        area_code: area,
        phone: phone,
        password: password,
        pin: pin || null
      })
    });

    const data = await res.json();
    if (res.ok) {
      closeAccountModal();
      await checkAccountStatus();
      await loadVehicles();
      const n = data.vehicles ? data.vehicles.length : 0;
      showToast(n ? `Cuenta de Ebro conectada: ${n === 1 ? 'un coche' : `${n} coches`} en la cuenta.` : 'Cuenta de Ebro conectada. No se ha encontrado ningún coche en ella.', 'success', 6000);
      if (window.EbroPush && EbroPush.suggest) EbroPush.suggest();       // without a channel the notices would only stay in the bell
    } else {
      if (errBox) {
        errBox.innerText = data.detail || "Error de autenticación. Comprueba tus datos.";
        errBox.style.display = 'block';
      }
    }
  } catch (err) {
    if (errBox) {
      errBox.innerText = "Error de conexión con el servidor: " + err;
      errBox.style.display = 'block';
    }
  } finally {
    btn.disabled = false;
    btnText.innerText = "Iniciar Sesión y Conectar";
  }
}

async function logoutEbroAccount() {
  if (!await confirmDialog("¿Desconectar la cuenta de Ebro? Dejarás de ver el estado del coche y de poder mandarle órdenes hasta que vuelvas a conectarla. Lo guardado (viajes, cargas, gastos…) no se borra.")) return;

  try {
    const res = await fetch('/api/account/logout', { method: 'POST' });
    if (res.ok) {
      closeAccountModal();
      await checkAccountStatus();
      showToast('Cuenta de Ebro desconectada.', 'success');
    } else {
      showToast('No se ha podido desconectar la cuenta.', 'error');
    }
  } catch (err) {
    showToast('Error de red al desconectar la cuenta.', 'error');
  }
}


// ───────────────────────── Changing the password of the Ebro account ─────────────────────────
const EBRO_PASSWORD_FIELDS = ['ebroPwdCurrent', 'ebroPwdNew', 'ebroPwdRepeat'];
const EBRO_PASSWORD_SPECIAL = '!@#$%~&*()_-+=}[]:;"\'<>,.?/';

/** The official app's rule (core/ebro_account.py): 8 to 16 characters with a letter, a digit and a special character. */
function ebroPasswordFormatOk(pw) {
  return pw.length >= 8 && pw.length <= 16 && /[A-Za-z]/.test(pw) && /[0-9]/.test(pw) && [...pw].some((c) => EBRO_PASSWORD_SPECIAL.includes(c));
}

function showEbroPasswordError(text) {
  const box = document.getElementById('ebroPwdError');
  if (!box) return;
  box.textContent = text || '';
  box.hidden = !text;
}

function setEbroPasswordFieldsVisible(show) {
  EBRO_PASSWORD_FIELDS.forEach((id) => { const f = document.getElementById(id); if (f) f.type = show ? 'text' : 'password'; });
  const btn = document.getElementById('ebroPwdShow');
  if (btn) {
    btn.textContent = show ? t('Ocultar') : t('Mostrar');
    btn.setAttribute('aria-pressed', String(show));
  }
}
function toggleEbroPasswordFields() {
  setEbroPasswordFieldsVisible(document.getElementById('ebroPwdNew').type === 'password');
}

function openEbroPasswordModal() {
  EBRO_PASSWORD_FIELDS.forEach((id) => { const f = document.getElementById(id); if (f) f.value = ''; });
  setEbroPasswordFieldsVisible(false);
  showEbroPasswordError('');
  document.getElementById('ebroPasswordModal').classList.add('open');
  document.getElementById('ebroPwdCurrent').focus();
}

function closeEbroPasswordModal() {
  document.getElementById('ebroPasswordModal').classList.remove('open');
  EBRO_PASSWORD_FIELDS.forEach((id) => { const f = document.getElementById(id); if (f) f.value = ''; });   // never left behind
  setEbroPasswordFieldsVisible(false);
}

async function submitEbroPassword() {
  const current = document.getElementById('ebroPwdCurrent').value;
  const fresh = document.getElementById('ebroPwdNew').value;
  const repeat = document.getElementById('ebroPwdRepeat').value;
  if (!current || !fresh) { showEbroPasswordError(t('Escribe la contraseña actual y la nueva.')); return; }
  if (!ebroPasswordFormatOk(fresh)) { showEbroPasswordError(t('La contraseña nueva debe tener de 8 a 16 caracteres, con letras, números y algún carácter especial.')); return; }
  if (fresh !== repeat) { showEbroPasswordError(t('Las dos contraseñas nuevas no coinciden.')); return; }
  if (fresh === current) { showEbroPasswordError(t('La contraseña nueva es igual que la actual.')); return; }
  const sure = await confirmDialog(t('¿Cambiar la contraseña de tu cuenta de Ebro?'),
    { title: t('Cambiar contraseña'), confirmText: t('Cambiar contraseña') });
  if (!sure) return;

  const btn = document.getElementById('btnEbroPwdSubmit');
  btn.disabled = true;
  showEbroPasswordError('');
  try {
    const res = await fetch('/api/account/ebro-password', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ current_password: current, new_password: fresh }),
    });
    const data = await res.json().catch(() => ({}));
    if (!res.ok) throw new Error(typeof data.detail === 'string' ? data.detail : res.status);
    closeEbroPasswordModal();
    await checkAccountStatus();
    showToast(data.session_renewed
      ? t('Contraseña de Ebro cambiada. EVBro ya usa la nueva.')
      : t('Ebro ha cambiado la contraseña, pero todavía no deja entrar con la nueva. EVBro la ha guardado y lo volverá a intentar; si no conecta, vuelve a conectar la cuenta.'),
      data.session_renewed ? 'success' : 'warning', 9000);
  } catch (e) {
    showEbroPasswordError(e.message || String(e));
  } finally {
    btn.disabled = false;
  }
}


// ───────────────────────── Changing the PIN of the Ebro account (in the cloud, not EVBro's copy) ─────────────────────────
const EBRO_PIN_FIELDS = ['ebroPinCurrent', 'ebroPinNew', 'ebroPinRepeat'];

/** The car the change goes through: the one on screen when it is a car of the account, else the account's first one. */
function ebroPinCar() {
  const cars = (currentAccountInfo && currentAccountInfo.vehicles) || [];
  return cars.find((c) => typeof currentVin !== 'undefined' && c.vin === currentVin) || cars[0] || null;
}

function showEbroPinError(text) {
  const box = document.getElementById('ebroPinError');
  if (!box) return;
  box.textContent = text || '';
  box.hidden = !text;
}

function setEbroPinFieldsVisible(show) {
  EBRO_PIN_FIELDS.forEach((id) => { const f = document.getElementById(id); if (f) f.type = show ? 'text' : 'password'; });
  const btn = document.getElementById('ebroPinShow');
  if (btn) {
    btn.textContent = show ? t('Ocultar') : t('Mostrar');
    btn.setAttribute('aria-pressed', String(show));
  }
}
function toggleEbroPinFields() {
  setEbroPinFieldsVisible(document.getElementById('ebroPinNew').type === 'password');
}

function openEbroPinModal() {
  EBRO_PIN_FIELDS.forEach((id) => { const f = document.getElementById(id); if (f) f.value = ''; });
  setEbroPinFieldsVisible(false);
  showEbroPinError('');
  const cars = (currentAccountInfo && currentAccountInfo.vehicles) || [];
  const car = ebroPinCar();
  const carLine = document.getElementById('ebroPinCar');
  if (carLine) {                       // with several cars, say through which one it goes (whether the PIN is per car is not known)
    carLine.hidden = cars.length < 2 || !car;
    if (car) carLine.textContent = t('Se cambia a través de {p0}.', {p0: car.name || car.vin});
  }
  document.getElementById('ebroPinModal').classList.add('open');
  document.getElementById('ebroPinCurrent').focus();
}

function closeEbroPinModal() {
  document.getElementById('ebroPinModal').classList.remove('open');
  EBRO_PIN_FIELDS.forEach((id) => { const f = document.getElementById(id); if (f) f.value = ''; });     // never left behind
  setEbroPinFieldsVisible(false);
}

async function submitEbroPin() {
  const current = document.getElementById('ebroPinCurrent').value.trim();
  const fresh = document.getElementById('ebroPinNew').value.trim();
  const repeat = document.getElementById('ebroPinRepeat').value.trim();
  const car = ebroPinCar();
  if (!car) { showEbroPinError(t('Tu cuenta de Ebro no tiene ningún coche.')); return; }
  if (!/^[0-9]{4,6}$/.test(current)) { showEbroPinError(t('Escribe el PIN actual de Ebro.')); return; }
  if (!/^[0-9]{6}$/.test(fresh)) { showEbroPinError(t('El PIN nuevo tiene que tener 6 dígitos.')); return; }
  if (fresh !== repeat) { showEbroPinError(t('Los dos PIN nuevos no coinciden.')); return; }
  if (fresh === current) { showEbroPinError(t('El PIN nuevo es igual que el actual.')); return; }
  const sure = await confirmDialog(t('¿Cambiar el PIN de tu cuenta de Ebro?'), { title: t('Cambiar PIN de Ebro'), confirmText: t('Cambiar PIN') });
  if (!sure) return;

  const btn = document.getElementById('btnEbroPinSubmit');
  btn.disabled = true;
  showEbroPinError('');
  try {
    const res = await fetch('/api/account/ebro-pin', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ vin: car.vin, current_pin: current, new_pin: fresh }),
    });
    const data = await res.json().catch(() => ({}));
    if (!res.ok) throw new Error(typeof data.detail === 'string' ? data.detail : res.status);
    closeEbroPinModal();
    if (typeof commandPinMap !== 'undefined') commandPinMap = null;
    await checkAccountStatus();
    showToast(data.stored_pin_updated ? t('PIN de Ebro cambiado. El PIN guardado en EVBro ya es el nuevo.') : t('PIN de Ebro cambiado. Úsalo en las órdenes que te lo pidan.'), 'success', 7000);
  } catch (e) {
    showEbroPinError(e.message || String(e));
  } finally {
    btn.disabled = false;
  }
}


// ───────────────────────── When orders ask for the PIN ─────────────────────────
const PIN_POLICY_HINT = {
  open: 'Las órdenes que abren, desbloquean o desarman el coche (puertas, maletero, ventanillas, techo, ventilar y quitar la alarma) piden el PIN cada vez; las demás (clima, luces, cerrar…) usan el PIN guardado.',
  always: 'Todas las órdenes piden el PIN, también encender el clima o cerrar.',
  never: 'Ninguna orden pide el PIN: con el PIN guardado, quien entre en EVBro con tu cuenta puede abrir el coche.',
};

function renderPinPolicy(data) {
  const box = document.getElementById('pinPolicyBox');
  if (!box) return;
  const policy = data.pin_policy || 'open';
  box.querySelectorAll('.theme-seg-btn').forEach((b) => {
    const on = b.dataset.policy === policy;
    b.classList.toggle('selected', on);
    b.setAttribute('aria-checked', String(on));
  });
  const hint = document.getElementById('pinPolicyHint');
  if (hint) hint.textContent = (PIN_POLICY_HINT[policy] || '') + (!data.has_pin && policy !== 'always' ? ' Sin PIN guardado, la nube de Ebro lo pide en toda orden.' : '');
}

async function setPinPolicy(policy) {
  if (policy === 'never') {
    if (!currentAccountInfo?.has_pin) { showToast('Guarda primero el PIN en la cuenta.', 'warning', 5000); return; }
    const sure = await confirmDialog('Ninguna orden pedirá el PIN: quien entre en EVBro con tu cuenta podrá abrir y desbloquear el coche sin teclearlo. ¿Seguro?',
      { title: 'No pedir nunca el PIN', confirmText: 'No pedirlo nunca', danger: true });
    if (!sure) return;
  }
  try {
    const res = await fetch('/api/account/pin-policy', { method: 'PUT', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ policy }) });
    const data = await res.json().catch(() => ({}));
    if (!res.ok) throw new Error(data.detail || res.status);
    if (typeof commandPinMap !== 'undefined') commandPinMap = null;      // the catalogue's requires_pin changes with the policy
    showToast('Guardado.', 'success');
    await checkAccountStatus();
  } catch (e) {
    showToast(t('No se pudo cambiar: {p0}', {p0: e.message || e}), 'error', 6000);
  }
}


// ───────────────────────── Home Assistant: the manager's own MQTT broker ─────────────────────────
window.MqttHa = (function () {
  const el = (id) => document.getElementById(id);
  let link = null;

  function form() {
    return { host: el('mqHost').value.trim(), port: el('mqPort').value ? parseInt(el('mqPort').value, 10) : null, username: el('mqUser').value.trim() || null,
             password: el('mqPass').value || null, use_tls: el('mqTls').checked, ha_discovery: el('mqDiscovery').checked, enabled: el('mqEnabled').checked };
  }

  function render() {
    if (!el('mqHost')) return;
    el('mqHost').value = link ? link.host : '';
    el('mqPort').value = link && link.port ? link.port : '';
    el('mqUser').value = link ? (link.username || '') : '';
    el('mqPass').value = '';
    el('mqPass').placeholder = link && link.has_password ? 'Guardada (déjala vacía para no cambiarla)' : '';
    el('mqTls').checked = link ? !!link.use_tls : false;
    el('mqDiscovery').checked = link ? !!link.ha_discovery : true;
    el('mqEnabled').checked = link ? !!link.enabled : true;
    el('mqDelete').hidden = !link;
    const st = el('mqStatus');
    if (!link) { st.textContent = 'Sin conectar. Tus coches no se publican en ningún broker.'; st.dataset.tone = 'neutral'; return; }
    if (!link.enabled) { st.textContent = 'En pausa: no se publica nada.'; st.dataset.tone = 'neutral'; return; }
    if (link.last_error) { st.textContent = t('Sin conexión con el broker: {p0}', {p0: link.last_error}); st.dataset.tone = 'bad'; return; }
    st.textContent = link.connected ? t('Conectado a {p0}. Coches publicados: {p1}.', {p0: link.host, p1: link.cars.length}) : t('Conectando con {p0}…', {p0: link.host});
    st.dataset.tone = link.connected ? 'ok' : 'neutral';
  }

  async function load() {
    if (!el('mqHost')) return;
    try {
      const res = await fetch('/api/account/mqtt');
      if (res.status === 403) { el('mqttCard').hidden = true; return; }
      link = res.ok ? (await res.json()).link : null;
    } catch (e) { link = null; }
    render();
  }

  async function call(method, body) {
    const res = await fetch('/api/account/mqtt', { method, headers: body ? { 'Content-Type': 'application/json' } : {}, body: body ? JSON.stringify(body) : undefined });
    const data = await res.json().catch(() => ({}));
    if (!res.ok) throw new Error(Array.isArray(data.detail) ? data.detail.map((d) => d.msg).join(' · ') : (data.detail || res.status));
    return data;
  }

  async function test() {
    const body = form();
    if (!body.host) { showToast('Indica el broker.', 'warning'); return; }
    el('mqTest').disabled = true;
    try {
      const res = await fetch('/api/account/mqtt/test', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) });
      const data = await res.json().catch(() => ({}));
      if (!res.ok) throw new Error(data.detail || res.status);
      showToast(data.ok ? 'El broker responde y acepta las credenciales.' : t('No se pudo conectar: {p0}', {p0: data.error}), data.ok ? 'success' : 'error', 7000);
    } catch (e) {
      showToast(t('No se pudo probar: {p0}', {p0: e.message || e}), 'error', 6000);
    } finally { el('mqTest').disabled = false; }
  }

  async function save() {
    const body = form();
    if (!body.host) { showToast('Indica el broker.', 'warning'); return; }
    try {
      link = (await call('PUT', body)).link;
      render();
      showToast(link.connected ? 'Guardado y conectado: tus coches ya se publican en Home Assistant.' : 'Guardado. Conectando con el broker…', 'success', 6000);
      setTimeout(load, 3000);
    } catch (e) { showToast(t('No se pudo guardar: {p0}', {p0: e.message || e}), 'error', 7000); }
  }

  async function remove() {
    if (!await confirmDialog('¿Quitar la conexión con tu broker? Home Assistant dejará de recibir el coche.', { title: 'Quitar MQTT', confirmText: 'Quitar', danger: true })) return;
    try { await call('DELETE'); link = null; render(); showToast('Conexión MQTT quitada', 'success'); }
    catch (e) { showToast(t('No se pudo quitar: {p0}', {p0: e.message || e}), 'error'); }
  }

  return { load, test, save, remove };
})();
