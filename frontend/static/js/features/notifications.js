/* EVBro — Notifications: channels, events, history and in-app alerts.
 * (Classic script: shares the global scope with the other files; see the load order in templates/index.html.)
 */

async function loadNotificationConfig() {
  try {
    const res = await fetch('/api/notifications/config');
    if (!res.ok) return;
    const cfg = await res.json();

    const tgCheck = document.getElementById('notifTelegramEnabled');
    if (tgCheck) tgCheck.checked = !!cfg.telegram_enabled;
    const tgToken = document.getElementById('notifTelegramToken');
    if (tgToken) tgToken.value = cfg.telegram_bot_token || '';
    const tgChatId = document.getElementById('notifTelegramChatId');
    if (tgChatId) tgChatId.value = cfg.telegram_chat_id || '';

    // Event toggles
    const setCheck = (id, val) => {
      const el = document.getElementById(id);
      if (el) el.checked = val !== false;
    };
    setCheck('notifEvtGeofenceEnter', cfg.notify_geofence_enter);
    setCheck('notifEvtGeofenceExit', cfg.notify_geofence_exit);
    setCheck('notifEvtFuelLow', cfg.notify_fuel_low);
    // Car settings (thresholds, tyre margin) belong to the car: shown to everyone who sees it, editable by its owner only.
    const veh = (typeof currentVehicle === 'function') ? currentVehicle() : null;
    const isOwner = !veh || (veh.access_level || 'owner') === 'owner';
    const carName = document.getElementById('notifCarName');
    if (carName) carName.textContent = veh ? (veh.name || 'Ebro') : 'ningún coche';
    const fuelThresholdEl = document.getElementById('notifFuelLowThreshold');
    if (fuelThresholdEl) { fuelThresholdEl.value = veh?.fuel_low_threshold ?? 15; fuelThresholdEl.disabled = !isOwner; }

    setCheck('notifEvtVehicleFaults', cfg.notify_vehicle_faults);
    setCheck('notifEvtWindowsOpen', cfg.notify_windows_open);
    setCheck('notifEvtMaintenance', cfg.notify_maintenance_due);
    setCheck('notifEvtChargeStart', cfg.notify_charge_started);
    setCheck('notifEvtChargeDone', cfg.notify_charge_completed);
    setCheck('notifEvtChargeInterrupted', cfg.notify_charge_interrupted);
    setCheck('notifEvtSecurity', cfg.notify_security_alerts);
    setCheck('notifEvtTyres', cfg.notify_tyre_pressure);
    setCheck('notifEvtRenewals', cfg.notify_renewals);
    const tolEl = document.getElementById('notifTyreTol');
    if (tolEl) { tolEl.value = veh?.tyre_pressure_tolerance_pct ?? 10; tolEl.disabled = !isOwner; }
    setCheck('notifEvtLowBattery', cfg.notify_low_battery);
    const lowBatThresholdEl = document.getElementById('notifLowBatteryThreshold');
    if (lowBatThresholdEl) { lowBatThresholdEl.value = veh?.low_battery_threshold ?? 20; lowBatThresholdEl.disabled = !isOwner; }

  } catch (err) {
    console.warn("Error loading the notification settings:", err);
  }
}

async function saveNotificationConfig() {
  const payload = {
    telegram_enabled: document.getElementById('notifTelegramEnabled')?.checked || false,
    telegram_bot_token: document.getElementById('notifTelegramToken')?.value.trim() || '',
    telegram_chat_id: document.getElementById('notifTelegramChatId')?.value.trim() || '',

    notify_geofence_enter: document.getElementById('notifEvtGeofenceEnter')?.checked ?? true,
    notify_geofence_exit: document.getElementById('notifEvtGeofenceExit')?.checked ?? true,
    notify_fuel_low: document.getElementById('notifEvtFuelLow')?.checked ?? true,
    notify_vehicle_faults: document.getElementById('notifEvtVehicleFaults')?.checked ?? true,
    notify_windows_open: document.getElementById('notifEvtWindowsOpen')?.checked ?? true,
    notify_maintenance_due: document.getElementById('notifEvtMaintenance')?.checked ?? true,
    notify_charge_started: document.getElementById('notifEvtChargeStart')?.checked ?? true,
    notify_charge_completed: document.getElementById('notifEvtChargeDone')?.checked ?? true,
    notify_charge_interrupted: document.getElementById('notifEvtChargeInterrupted')?.checked ?? true,
    notify_security_alerts: document.getElementById('notifEvtSecurity')?.checked ?? true,
    notify_tyre_pressure: document.getElementById('notifEvtTyres')?.checked ?? true,
    notify_renewals: document.getElementById('notifEvtRenewals')?.checked ?? true,
    notify_low_battery: document.getElementById('notifEvtLowBattery')?.checked ?? true,
  };

  // What the CAR needs (thresholds and the tyre margin) is saved in the car, and only by its owner; the rest is this account's choice.
  const veh = (typeof currentVehicle === 'function') ? currentVehicle() : null;
  if (veh && (veh.access_level || 'owner') === 'owner') {
    const num = (id) => parseFloat(document.getElementById(id)?.value);
    const wanted = { tyre_pressure_tolerance_pct: num('notifTyreTol'), fuel_low_threshold: num('notifFuelLowThreshold'), low_battery_threshold: num('notifLowBatteryThreshold') };
    const ranges = { tyre_pressure_tolerance_pct: [1, 50, 'El margen de la presión'], fuel_low_threshold: [1, 100, 'El umbral de combustible'], low_battery_threshold: [1, 100, 'El umbral de batería'] };
    const changed = {};
    for (const [k, v] of Object.entries(wanted)) {
      if (isNaN(v) || v === veh[k]) continue;
      const [lo, hi, label] = ranges[k];
      if (v < lo || v > hi) { showToast(t('{p0} debe estar entre {p1} y {p2}.', {p0: label, p1: lo, p2: hi}), 'warning'); return; }
      changed[k] = k === 'tyre_pressure_tolerance_pct' ? v : Math.round(v);
    }
    if (Object.keys(changed).length) {
      const r = await fetch(`/api/vehicles/${veh.vin}`, { method: 'PUT', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(changed) });
      if (r.ok && typeof replaceVehicle === 'function') replaceVehicle(await r.json());
    }
  }

  try {
    const res = await fetch('/api/notifications/config', {
      method: 'PUT',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(payload)
    });
    if (res.ok) {
      showToast('Ajustes de avisos guardados.', 'success');
    } else {
      const err = await res.json();
      showToast(t('No se pudo guardar: {p0}', {p0: err.detail || 'error desconocido'}), 'error', 6000);
    }
  } catch (err) {
    showToast(t('Error de red: {p0}', {p0: err}), 'error');
  }
}

function openNotificationsModal() {
  const modal = document.getElementById('notificationsCenterModal');
  if (modal) modal.classList.add('open');
  loadNotificationLogsList();
}

function closeNotificationsModal() {
  const modal = document.getElementById('notificationsCenterModal');
  if (modal) modal.classList.remove('open');
}

async function testNotificationChannel(channel) {
  let body = { channel: channel };

  if (channel === 'telegram') {
    const token = document.getElementById('notifTelegramToken')?.value.trim();
    const chatId = document.getElementById('notifTelegramChatId')?.value.trim();
    if (!token || !chatId) {
      showToast('Indica el Bot Token y el Chat ID de Telegram.', 'warning');
      return;
    }
    body.bot_token = token;
    body.chat_id = chatId;
  }

  try {
    const res = await fetch('/api/notifications/test', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body)
    });

    if (res.ok) {
      showToast(t('Aviso de prueba enviado a {p0}. Comprueba tu dispositivo.', {p0: channel}), 'success', 5000);
    } else {
      const err = await res.json();
      showToast(t('No se pudo enviar a {p0}: {p1}', {p0: channel, p1: err.detail || 'revisa las credenciales o la conexión.'}), 'error', 7000);
    }
  } catch (err) {
    showToast(t('Error de red al probar el aviso: {p0}', {p0: err}), 'error');
  }
}

function toggleNotificationLogs() {
  const container = document.getElementById('notifLogsContainer');
  if (!container) return;
  const isHidden = container.style.display === 'none';
  container.style.display = isHidden ? 'block' : 'none';
  if (isHidden) {
    loadNotificationLogs();
  }
}

async function loadNotificationLogs() {
  const list = document.getElementById('notifLogsList');
  if (!list) return;

  try {
    const res = await fetch('/api/notifications/logs');
    if (!res.ok) return;
    const logs = await res.json();

    if (!logs || logs.length === 0) {
      list.innerHTML = '<div class="u-color-var-text-muted_text-align-center_padding-1rem">No hay registros de notificaciones aún.</div>';
      return;
    }

    list.innerHTML = logs.map(l => {
      const dt = serverDate(l.timestamp).toLocaleString(I18n.locale);
      const badgeClass = l.success ? 'class="notif-log-ok"' : 'class="notif-log-fail"';
      const statusText = l.success ? 'Enviada' : t('Error: {p0}', {p0: escapeHtml(l.error || 'Fallo')});
      return `
        <div class="u-border-bottom-1px-solid-rgba-255-255-255-0p06_padding-0p5rem-0">
          <div class="u-display-flex_justify-content-space-between_align-items-center">
            <strong>${escapeHtml(l.title)}</strong>
            <span class="u-font-size-0p72rem_color-var-text-muted">${dt}</span>
          </div>
          <div class="u-color-var-text-secondary_margin-0p2rem-0_white-space-pre-wrap">${escapeHtml(l.message)}</div>
          <div class="u-display-flex_gap-0p6rem_font-size-0p72rem">
            <span class="maint-category-badge">${escapeHtml((l.channel || '').toUpperCase())}</span>
            <span ${badgeClass}>${statusText}</span>
          </div>
        </div>
      `;
    }).join('');
  } catch (err) {
    list.innerHTML = `<div class="u-color-var-accent-red_padding-0p5rem">${t('Error cargando logs: {p0}', {p0: escapeHtml(err)})}</div>`;
  }
}

async function loadNotificationLogsList() {
  await loadNotificationLogs();
  const modalList = document.getElementById('modalNotifLogsList');
  const mainList = document.getElementById('notifLogsList');
  if (modalList && mainList) {
    modalList.innerHTML = mainList.innerHTML;
  }
}

async function clearNotificationLogs() {
  if (!await confirmDialog("¿Deseas vaciar el historial de notificaciones?", { confirmText: 'Borrar', danger: true })) return;
  try {
    await fetch('/api/notifications/logs', { method: 'DELETE' });
    loadNotificationLogsList();
  } catch (err) {
    showToast(t('No se pudo vaciar el historial: {p0}', {p0: err}), 'error');
  }
}

function showAppNotificationBanner(title, message, category) {
  // 1. System notification if allowed
  if ("Notification" in window && Notification.permission === "granted") {
    try {
      new Notification(title, {
        body: message,
        icon: "/static/icons/icon-192.png",
      });
    } catch (e) {
      console.debug("Notification error:", e);
    }
  }

  // 2. In-app banner: it joins the toast column (one stack for every notice, nothing on top of anything), and the same title and
  //    text within a few seconds is one banner
  let box = document.getElementById('appToastContainer');
  if (!box) {
    box = document.createElement('div');
    box.id = 'appToastContainer';
    box.className = 'app-toast-container';
    document.body.appendChild(box);
  }
  const key = `${title}\u0000${message}`;
  const twin = [...box.querySelectorAll('.app-banner')].find((b) => b.dataset.key === key);
  if (twin) { clearTimeout(twin._timer); twin._timer = setTimeout(() => twin.remove(), 7000); return; }
  const banners = [...box.querySelectorAll('.app-banner')];
  while (banners.length >= 3) banners.shift().remove();

  const banner = document.createElement('div');
  banner.className = 'app-banner';
  banner.dataset.key = key;
  banner.innerHTML = `
    <div class="app-banner-head">
      <strong>${escapeHtml(title)}</strong>
      <button type="button" class="app-banner-close" aria-label="Cerrar" data-on-click="removeClosest(this, '.app-banner')">✕</button>
    </div>
    <div class="app-banner-body">${escapeHtml(message)}</div>
  `;
  box.appendChild(banner);
  banner._timer = setTimeout(() => { if (banner.parentElement) banner.remove(); }, 7000);
}
