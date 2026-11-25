/* EVbro — Orders to the car: action sheets, PIN dialog and sending.
 * (Classic script: shares the global scope with the other files; see the load order in templates/index.html.)
 */

function openTrunkActionSheet() {
  const statusStr = "Estado actual: " + openClosedText(latestTelemetryData?.doors?.trunk, "Abierto", "Cerrado").replace(/^./, (c) => c.toUpperCase());

  openActionSheet("Control de Maletero", statusStr, [
    {
      label: "Abrir Maletero",
      desc: "Accionamiento remoto del portón eléctrico",
      type: "primary",
      action: "executeCommandDirect('maletero_abrir', 'Abrir maletero'); closeActionSheet();"
    },
    {
      label: "Cerrar Maletero",
      desc: "Cierre seguro del portón eléctrico",
      action: "executeCommandDirect('maletero_cerrar', 'Cerrar maletero'); closeActionSheet();"
    }
  ]);
}

function openWindowsActionSheet() {
  const w = latestTelemetryData?.windows || {};
  const statusStr = "Estado actual: " + window.windowsStateText(w);

  openActionSheet("Control de Ventanillas", statusStr, [
    {
      label: "Abrir Todas las Ventanillas",
      desc: "Apertura completa de las cuatro lunas",
      type: "primary",
      action: "executeCommandDirect('ventanillas_abrir', 'Abrir ventanillas'); closeActionSheet();"
    },
    {
      label: "Cerrar Todas las Ventanillas",
      desc: "Cierre hermético de todas las lunas",
      action: "executeCommandDirect('ventanillas_cerrar', 'Cerrar ventanillas'); closeActionSheet();"
    },
    {
      label: "Ventilar (Rendija de Confort)",
      desc: "Baja unos centímetros para renovar el aire",
      action: "executeCommandDirect('ventilar_ventanillas', 'Ventilar ventanillas'); closeActionSheet();"
    }
  ]);
}

function openSunroofActionSheet() {
  const sunroof = latestTelemetryData?.windows?.sunroof;                       // 0 closed, 2 tilted («Ventilar»), 3 open
  const statusStr = "Estado actual: " + openClosedText(sunroof, sunroof === 2 ? "Entreabierto" : "Abierto", "Cerrado").replace(/^./, (c) => c.toUpperCase());

  openActionSheet("Control de Techo Solar", statusStr, [
    {
      label: "Abrir Techo Solar",
      desc: "Apertura completa del techo panorámico",
      type: "primary",
      action: "executeCommandDirect('techo_abrir', 'Abrir techo solar'); closeActionSheet();"
    },
    {
      label: "Cerrar Techo Solar",
      desc: "Cierre y recogida del cristal",
      action: "executeCommandDirect('techo_cerrar', 'Cerrar techo solar'); closeActionSheet();"
    },
    {
      label: "Ventilar (Techo Entreabierto)",
      desc: "Levanta el cristal para renovar el aire",
      action: "executeCommandDirect('techo_ventilar', 'Ventilar techo solar'); closeActionSheet();"
    }
  ]);
}

// Central locking: decided by the car's REAL state; if it has not reported yet, let the user choose
function toggleLockState() {
  const locked = latestTelemetryData ? latestTelemetryData.is_locked : null;
  if (locked === true) { executeCommandDirect('desbloquear', 'Desbloquear puertas'); return; }
  if (locked === false) { executeCommandDirect('bloquear', 'Bloquear puertas'); return; }
  openActionSheet("Cierre centralizado", "Estado actual: sin datos", [
    { label: "Desbloquear", desc: "Pide el PIN", type: "primary",
      action: "executeCommandDirect('desbloquear', 'Desbloquear puertas'); closeActionSheet();" },
    { label: "Bloquear", desc: "Cierra el coche",
      action: "executeCommandDirect('bloquear', 'Bloquear puertas'); closeActionSheet();" }
  ]);
}

function toggleTrunk() {
  openTrunkActionSheet();
}

function toggleWindows() {
  openWindowsActionSheet();
}

function toggleSunroof() {
  openSunroofActionSheet();
}

function locateGps() {
  executeCommandDirect('localizar_coche_gps', 'Petición Posición GPS en Reposo');
}

/**
 * Sends a remote command or asks for confirmation first.
 */
async function promptCommand(commandKey, commandName, extraParams = null) {
  if (!currentVin) {
    showToast("No hay ningún vehículo seleccionado.", "warning");
    return;
  }
  await executeCommandDirect(commandKey, commandName, extraParams);
}

/** The toast of the last order sent, so the car's confirmation (MQTT → WebSocket) replaces it instead of adding another notice. */
let lastCommandToast = null;
function rememberCommandToast(name, toast) {
  lastCommandToast = { name, toast, at: Date.now() };
}
function applyCommandResult(message, failed) {
  const recent = lastCommandToast && lastCommandToast.toast.alive && Date.now() - lastCommandToast.at < 60000 ? lastCommandToast : null;
  const text = recent ? `${recent.name}: ${message}` : message;
  if (recent) recent.toast.update(text, failed ? 'error' : 'success', failed ? 7000 : 4500);
  else showToast(text, failed ? 'error' : 'success', 5000);
  if (recent && !failed) lastCommandToast = null;
}

// The server decides which commands need the PIN (`requires_pin` field of the catalogue).
let commandPinMap = null;

async function commandRequiresPin(commandKey) {
  if (!commandPinMap) {
    try {
      const res = await fetch('/api/commands/catalog');
      if (res.ok) {
        commandPinMap = Object.fromEntries((await res.json()).map((c) => [c.key, !!c.requires_pin]));
      }
    } catch (e) { /* if it does not load, the server will require it anyway and the dialog will open */ }
  }
  return commandPinMap ? !!commandPinMap[commandKey] : false;
}

function setPinModalError(message) {
  const el = document.getElementById('pinModalError');
  if (!el) return;
  el.textContent = message || '';
  el.hidden = !message;
}

function openPinModalForCommand(commandKey, commandName, extraParams = null, errorMessage = '') {
  pendingCommand = { key: commandKey, name: commandName, params: extraParams };
  const titleEl = document.getElementById('pinModalTitle');
  const inputEl = document.getElementById('pinInput');
  if (titleEl) titleEl.innerText = commandName;
  if (inputEl) inputEl.value = '';
  setPinModalError(errorMessage);
  // Saving the PIN only makes sense if none is saved yet.
  const rememberRow = document.getElementById('pinRememberRow');
  if (rememberRow) rememberRow.hidden = !!currentAccountInfo?.has_pin;
  const rememberChk = document.getElementById('chkRememberPin');
  if (rememberChk) rememberChk.checked = false;
  const modal = document.getElementById('pinModal');
  if (modal) modal.classList.add('open');
  if (inputEl) inputEl.focus();
}

/**
 * What to do when the server rejects a command. Decided by `reason` (a stable value), not by
 * the message text. Ebro account errors never use 401, which is reserved for the login.
 */
function handleCommandFailure(data, commandKey, commandName, extraParams) {
  const reason = data.reason || null;
  const detail = data.detail || data.message || 'Error al ejecutar el comando en el vehículo';
  if (reason === 'pin_required' || reason === 'pin_invalid' || reason === 'pin') {
    openPinModalForCommand(commandKey, commandName, extraParams, reason === 'pin_required' ? '' : detail);
  } else if (reason === 'reauth') {
    showToast('La sesión de tu cuenta Ebro ha caducado. Vuelve a conectarla en Ajustes → Cuenta Ebro.', 'error', 7000);
  } else if (data.code === 'A00084') {
    // The cloud refuses this command on this car: the server remembers it (`rejected_commands`) and the control says so from now on
    const v = (typeof currentVehicle === 'function') ? currentVehicle() : null;
    if (v) v.rejected_commands = Array.from(new Set([...(v.rejected_commands || []), commandKey]));
    if (typeof renderComfort === 'function') renderComfort();
    showToast(t('La nube de Ebro no permite «{p0}» en este coche.', {p0: commandName}), 'error', 7000);
  } else {
    showToast(`${escapeHtml(detail)}`, 'error', 6000);
  }
}

/**
 * Sends a command with unobtrusive visual feedback (toast + loading).
 * Commands that open or disarm ask for the PIN on every send; the PIN is not stored in the browser.
 */
async function executeCommandDirect(commandKey, commandName, extraParams = null, pin = null) {
  if (!(pin && pin.trim()) && await commandRequiresPin(commandKey)) {
    openPinModalForCommand(commandKey, commandName, extraParams);
    return false;
  }

  const btnId = getCommandButtonId(commandKey);
  const btn = btnId ? document.getElementById(btnId) : null;
  if (btn) {
    btn.disabled = true;
    btn.style.opacity = '0.7';
  }

  const toast = showToast(t('Enviando "{p0}" al vehículo...', {p0: escapeHtml(commandName)}), 'loading', 15000);
  let accepted = false;

  try {
    const res = await fetch(`/api/vehicles/${currentVin}/commands`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        command: commandKey,
        pin: (pin && pin.trim()) ? pin.trim() : null,
        params: extraParams
      })
    });

    const data = await res.json().catch(() => ({}));
    if (res.ok && data.success) {
      // One notice per order: this toast is updated when the car confirms over MQTT (websocket.js), instead of a second one
      toast.update(t('{p0}: orden aceptada por la nube Ebro', {p0: commandName}), 'success', 6000);
      rememberCommandToast(commandName, toast);
      accepted = true;
      window.dispatchEvent(new CustomEvent('evbro:command-accepted', { detail: { key: commandKey } }));
    } else {
      toast.remove();
      handleCommandFailure(data, commandKey, commandName, extraParams);
    }
  } catch (err) {
    toast.update(t('Error de red: {p0}', {p0: escapeHtml(err)}), 'error', 4500);
  } finally {
    if (btn) {
      btn.disabled = false;
      btn.style.opacity = '1';
    }
  }
  return accepted;
}

function getCommandButtonId(commandKey) {
  const map = {
    'bloquear': 'btnLockAction',
    'desbloquear': 'btnLockAction',
    'maletero_abrir': 'btnTrunkCard',
    'maletero_cerrar': 'btnTrunkCard',
    'ventanillas_abrir': 'btnWindowsCard',
    'ventanillas_cerrar': 'btnWindowsCard',
    'ventilar_ventanillas': 'btnWindowsCard',
    'techo_abrir': 'btnSunroofCard',
    'techo_cerrar': 'btnSunroofCard',
    'techo_ventilar': 'btnSunroofCard',
    'clima_on': 'btnClimatePowerToggle',
    'clima_off': 'btnClimatePowerToggle',
    'encontrar_coche_luces': 'btnFindCar',
    'defrost_parabrisas': 'btnDefrost',
    'carga_iniciar': 'btnToggleCharge',
    'carga_detener': 'btnToggleCharge'
  };
  return map[commandKey] || null;
}

function closePinModal() {
  document.getElementById('pinModal').classList.remove('open');
  const input = document.getElementById('pinInput');
  if (input) input.value = '';
  setPinModalError('');
  pendingCommand = null;
}

async function executePendingCommand() {
  if (!pendingCommand || !currentVin) return;
  const cmd = pendingCommand;

  const pin = document.getElementById('pinInput').value.trim();
  if (!pin) {
    setPinModalError('Introduce el PIN de tu cuenta Ebro.');
    return;
  }

  const rememberChk = document.getElementById('chkRememberPin');
  const remember = !!(rememberChk && rememberChk.checked) && !currentAccountInfo?.has_pin;

  const btn = document.getElementById('btnConfirmCommand');
  btn.innerText = "Enviando...";
  btn.disabled = true;
  setPinModalError('');

  try {
    const res = await fetch(`/api/vehicles/${currentVin}/commands`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ command: cmd.key, pin: pin, params: cmd.params })
    });

    const data = await res.json().catch(() => ({}));
    if (res.ok && data.success) {
      closePinModal();
      rememberCommandToast(cmd.name, showToast(t('{p0}: orden aceptada por la nube Ebro', {p0: cmd.name}), 'success', 6000));
      window.dispatchEvent(new CustomEvent('evbro:command-accepted', { detail: { key: cmd.key } }));
      // Only if the user asks for it, and only after checking the PIN is valid (the command went through).
      if (remember) {
        saveAccountPin(pin).catch((e) => showToast(t('No se pudo guardar el PIN: {p0}', {p0: e.message || e}), 'error', 6000));
      }
    } else if (data.reason === 'pin_invalid' || data.reason === 'pin' || data.reason === 'pin_required') {
      // The dialog stays open to retry.
      setPinModalError(data.detail || data.message || 'PIN incorrecto.');
      const input = document.getElementById('pinInput');
      input.value = '';
      input.focus();
    } else {
      closePinModal();
      handleCommandFailure(data, cmd.key, cmd.name, cmd.params);
    }
  } catch (err) {
    setPinModalError(t('Error de red al enviar el comando: {p0}', {p0: err}));
  } finally {
    btn.innerText = "Enviar Comando";
    btn.disabled = false;
  }
}

/**
 * Saves or updates the PIN on the server's active account so it is not asked for again.
 * @param {string} pin - the 6-digit PIN of the Ebro account.
 */
async function saveAccountPin(pin) {
  const res = await fetch('/api/account/pin', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ pin: pin })
  });
  const data = await res.json().catch(() => ({}));
  if (!res.ok) throw new Error(typeof data.detail === 'string' ? data.detail : res.status);   // it used to say «guardado» anyway
  await checkAccountStatus();
}

/**
 * Opens a dialog to change or save the PIN in Ajustes -> Cuenta Ebro.
 */
async function openUpdatePinModal() {
  const currentStatus = currentAccountInfo?.has_pin ? "Hay un PIN guardado en EVbro." : "No hay ningún PIN guardado en EVbro.";
  // What happens after saving depends on «Pedir el PIN» (services/command_policy.py): say only what is true for the current choice
  const policy = currentAccountInfo?.pin_policy || 'open';
  const policyNote = {
    open: t('Las órdenes que abren, desbloquean o desarman el coche te lo pedirán cada vez.'),
    always: t('Con «Pedir el PIN: Siempre», todas las órdenes te lo pedirán igualmente.'),
    never: t('Con «Pedir el PIN: Nunca», ninguna orden te lo pedirá.'),
  }[policy] || '';
  const newPin = await promptDialog(t('{p0}\nIntroduce el PIN de 6 dígitos que usará EVbro para las órdenes que no te lo pidan (déjalo en blanco para eliminarlo). Solo cambia la copia guardada en EVbro, no el PIN de Ebro: para eso está «Cambiar PIN de Ebro». {p1}', {p0: currentStatus, p1: policyNote}),
    { title: 'PIN guardado en EVbro', type: 'password', inputmode: 'numeric', placeholder: '••••••', maxLength: 6, confirmText: 'Guardar' });
  if (newPin === null) return; // Cancelled
  const pin = newPin.trim();
  if (pin && !/^[0-9]{6}$/.test(pin)) { showToast(t('El PIN de Ebro tiene 6 dígitos.'), 'warning', 6000); return; }

  try {
    await saveAccountPin(pin);
    showToast(pin ? t('PIN guardado. {p0}', {p0: policyNote}) : 'PIN eliminado. Las órdenes volverán a pedir el PIN.', 'success', 6000);
  } catch (e) {
    showToast(t('No se pudo guardar el PIN: {p0}', {p0: e.message || e}), 'error', 6000);
  }
}
