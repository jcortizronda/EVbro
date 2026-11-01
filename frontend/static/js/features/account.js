/* EVBro — Ebro account: status, connection and disconnection.
 * (Classic script: shares the global scope with the other files; see the load order in templates/index.html.)
 */

let currentAccountInfo = null;

async function checkAccountStatus() {
  if (typeof currentRole === 'function' && currentRole() === 'member') return;      // members have no Ebro account
  try {
    const res = await fetch('/api/account/status');
    if (!res.ok) return;
    const data = await res.json();
    currentAccountInfo = data;
    renderPinPolicy(data);                 // the choice exists whether or not an Ebro account is connected right now

    const dot = document.getElementById('accountStatusDot');
    const btnText = document.getElementById('accountBtnText');
    const settingsBadge = document.getElementById('settingsAccountBadge');
    const settingsPhone = document.getElementById('settingsAccountPhone');
    const settingsVehicles = document.getElementById('settingsAccountVehicles');
    const settingsPin = document.getElementById('settingsAccountPinStatus');
    const connectBtn = document.getElementById('btnSettingsConnect');     // «Conectar» or «Desconectar», depending on the state
    const syncBtn = document.getElementById('btnSettingsSync');           // only makes sense with a connected account

    if (data.connected) {
      if (dot) {
        dot.style.background = '#00e676';
        dot.style.boxShadow = '0 0 8px rgba(0, 230, 118, 0.6)';
      }
      if (btnText) {
        btnText.innerText = data.phone || "Conectado";
      }
      if (settingsBadge) {
        settingsBadge.className = "status-badge driving";
        settingsBadge.innerText = "Conectado";
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
    } else {
      if (dot) {
        dot.style.background = '#ffb300';
        dot.style.boxShadow = '0 0 8px rgba(255, 179, 0, 0.6)';
      }
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


// ───────────────────────── When orders ask for the PIN ─────────────────────────
const PIN_POLICY_HINT = {
  open: 'Las órdenes que abren o desbloquean el coche piden el PIN cada vez; las demás (clima, luces, cerrar…) usan el PIN guardado.',
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
