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

/** The bell of the header: the history with the unread notices marked, and from then on everything counts as seen. */
async function openNotificationsModal() {
  const modal = document.getElementById('notificationsCenterModal');
  if (modal) modal.classList.add('open');
  refreshPushHint();
  const painted = await loadNotificationLogsList();
  if (!painted) return;                                        // offline or no session: nothing was shown, nothing is marked
  try { await fetch('/api/notifications/seen', { method: 'POST' }); } catch (e) { /* offline: the next opening marks them */ }
  setNotificationDot(0);
}

function closeNotificationsModal() {
  const modal = document.getElementById('notificationsCenterModal');
  if (modal) modal.classList.remove('open');
}

function openNotificationSettings() {
  closeNotificationsModal();
  switchTab('settings');
  switchSettingsSubTab('notifs');
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

// ───────────────────────── notice history (bell of the header) ─────────────────────────
// Icon and colour of each kind of notice (the categories of services/notification_service.py)
const NOTICE_KIND = {
  charge_started: ['plug', 'charge'], charge_completed: ['battery', 'charge'],
  charge_interrupted: ['alert', 'warn'], charge_control: ['alert', 'warn'], low_battery: ['battery', 'warn'],
  fuel_low: ['fuel', 'warn'], tyre_pressure: ['alert', 'warn'], windows_open: ['window', 'warn'],
  geofence_enter: ['pin', 'place'], geofence_exit: ['pin', 'place'],
  maintenance: ['wrench', 'maint'], renewals: ['clock', 'maint'],
  security: ['siren', 'alarm'], vehicle_fault: ['alert', 'alarm'], account: ['key', 'alarm'],
};

/** A notice sent through several channels leaves one row per channel, written in the same second: here they are one notice. */
function groupNotices(logs) {
  const notices = [];
  const byKey = new Map();
  for (const row of logs) {
    const key = [row.category, row.title, row.message, String(row.timestamp || '').slice(0, 19)].join('\u0000');
    let notice = byKey.get(key);
    if (!notice) {
      notice = { id: row.id, category: row.category, title: row.title, message: row.message, timestamp: row.timestamp, unread: false, failures: [] };
      byKey.set(key, notice);
      notices.push(notice);
    }
    notice.unread = notice.unread || !!row.unread;
    if (!row.success) notice.failures.push(row);
  }
  return notices;
}

function noticeDay(d) {
  const yesterday = new Date();
  yesterday.setDate(yesterday.getDate() - 1);
  if (d.toDateString() === new Date().toDateString()) return t('Hoy');
  if (d.toDateString() === yesterday.toDateString()) return t('Ayer');
  return d.toLocaleDateString(I18n.locale, { weekday: 'long', day: 'numeric', month: 'long' });
}

function noticeChannel(channel) {
  return { webpush: t('notificación del móvil'), telegram: 'Telegram', pushover: 'Pushover', webhook: 'webhook' }[channel] || channel;
}

/** One row per notice, grouped by day and the newest first: the look of the timeline (kind icon, title, text and time). */
function renderNotices(list, logs) {
  const notices = groupNotices(logs || []);
  if (!notices.length) {
    list.innerHTML = `<div class="hist-empty">${t('Todavía no hay avisos.')}</div>`;
    return;
  }
  let day = null;
  const html = [];
  for (const n of notices) {
    const when = serverDate(n.timestamp);
    const label = noticeDay(when);
    if (label !== day) {
      day = label;
      html.push(`<div class="hist-month"><strong>${escapeHtml(label)}</strong></div>`);
    }
    const [ico, kind] = NOTICE_KIND[n.category] || ['bell', 'info'];
    const time = when.toLocaleTimeString(I18n.locale, { hour: '2-digit', minute: '2-digit' });
    const failures = n.failures.map((f) => `<small class="notice-fail">${escapeHtml(t('No se pudo enviar por {p0}: {p1}', {p0: noticeChannel(f.channel), p1: f.error || '—'}))}</small>`);
    html.push(`<div class="notice-row notice-${kind}${n.unread ? ' is-unread' : ''}" data-id="${Number(n.id)}">
      <span class="notice-ico" aria-hidden="true">${icon(ico, 16)}</span>
      <span class="notice-main"><strong>${escapeHtml(n.title)}</strong><span class="notice-text">${escapeHtml(n.message)}</span>${failures.join('')}</span>
      <span class="notice-time">${n.unread ? `<span class="notice-new">${t('Nuevo')}</span>` : ''}${escapeHtml(time)}<button type="button" class="notice-dismiss" aria-label="${t('Descartar aviso')}" title="${t('Descartar aviso')}">${icon('x', 14)}</button></span>
    </div>`);
  }
  list.innerHTML = html.join('');
  attachNoticeSwipe(list);
}

// ───────────────────────── dismissing one notice ─────────────────────────
// Like the notifications of Android: a notice swiped sideways goes away. With a mouse, the cross of the row does the same.
const NOTICE_SWIPE_SHARE = 0.35;                 // of the row's width, or a quick flick
const NOTICE_FLICK_PX_PER_MS = 0.6;

function attachNoticeSwipe(list) {
  if (list.dataset.swipe) return;
  list.dataset.swipe = '1';
  let drag = null;
  list.addEventListener('click', (ev) => {
    const btn = ev.target.closest('.notice-dismiss');
    if (btn) dismissNotice(btn.closest('.notice-row'), 1);
  });
  list.addEventListener('pointerdown', (ev) => {
    const row = ev.target.closest('.notice-row');
    if (!row || ev.button > 0 || ev.target.closest('button') || row.classList.contains('is-leaving')) return;
    drag = { row, pointer: ev.pointerId, x: ev.clientX, y: ev.clientY, dx: 0, active: false, at: performance.now() };
  });
  list.addEventListener('pointermove', (ev) => {
    if (!drag || ev.pointerId !== drag.pointer) return;
    const dx = ev.clientX - drag.x;
    const dy = ev.clientY - drag.y;
    if (!drag.active) {
      if (Math.abs(dy) > 10 && Math.abs(dy) >= Math.abs(dx)) { drag = null; return; }   // scrolling the list, not swiping
      if (Math.abs(dx) < 10) return;
      drag.active = true;
      drag.row.classList.add('is-dragging');
      try { drag.row.setPointerCapture(ev.pointerId); } catch (e) { /* the pointer may already be gone */ }
    }
    drag.dx = dx;
    drag.row.style.transform = `translateX(${dx}px)`;
    drag.row.style.opacity = String(Math.max(0.15, 1 - Math.abs(dx) / drag.row.offsetWidth));
  });
  const end = (ev) => {
    if (!drag || ev.pointerId !== drag.pointer) return;
    const { row, dx, active, at } = drag;
    drag = null;
    if (!active) return;
    row.classList.remove('is-dragging');
    const flick = Math.abs(dx) > 40 && Math.abs(dx) / Math.max(1, performance.now() - at) > NOTICE_FLICK_PX_PER_MS;
    if (ev.type === 'pointerup' && (Math.abs(dx) > row.offsetWidth * NOTICE_SWIPE_SHARE || flick)) {
      dismissNotice(row, Math.sign(dx) || 1);
    } else {
      row.style.transform = '';
      row.style.opacity = '';
    }
  };
  list.addEventListener('pointerup', end);
  list.addEventListener('pointercancel', end);
}

/** Slides the row out, removes the notice on the server (all its channels) and closes the gap; on failure the row comes back. */
async function dismissNotice(row, direction) {
  if (!row || row.classList.contains('is-leaving')) return;
  row.classList.add('is-leaving');
  row.style.transform = `translateX(${direction * 110}%)`;
  row.style.opacity = '0';
  let ok = false;
  try {
    ok = (await fetch(`/api/notifications/logs/${row.dataset.id}`, { method: 'DELETE' })).ok;
  } catch (e) { ok = false; }
  if (!ok) {
    row.classList.remove('is-leaving');
    row.style.transform = '';
    row.style.opacity = '';
    showToast(t('No se pudo descartar el aviso.'), 'error');
    return;
  }
  row.style.height = `${row.offsetHeight}px`;
  row.getBoundingClientRect();                                 // fix the height before collapsing it
  row.classList.add('is-gone');
  setTimeout(() => {
    const list = row.parentElement;
    row.remove();
    if (!list) return;
    // A day left without notices loses its heading; an empty history says so
    list.querySelectorAll('.hist-month').forEach((head) => {
      const next = head.nextElementSibling;
      if (!next || !next.classList.contains('notice-row')) head.remove();
    });
    if (!list.querySelector('.notice-row')) list.innerHTML = `<div class="hist-empty">${t('Todavía no hay avisos.')}</div>`;
  }, 220);
}

/** Paints the history in the bell's dialog. Returns whether it could (offline or without a session it could not). */
async function loadNotificationLogsList() {
  const list = document.getElementById('modalNotifLogsList');
  if (!list) return false;
  try {
    const res = await fetch('/api/notifications/logs?limit=100');
    if (!res.ok) throw new Error(res.status);
    renderNotices(list, await res.json());
    return true;
  } catch (err) {
    list.innerHTML = `<div class="hist-empty is-error">${t('No se pudo cargar el historial de avisos.')}</div>`;
    return false;
  }
}

async function clearNotificationLogs() {
  if (!await confirmDialog("¿Vaciar el historial de avisos?", { confirmText: 'Vaciar', danger: true })) return;
  try {
    await fetch('/api/notifications/logs', { method: 'DELETE' });
    loadNotificationLogsList();
  } catch (err) {
    showToast(t('No se pudo vaciar el historial: {p0}', {p0: err}), 'error');
  }
}

// ───────────────────────── dot of the bell ─────────────────────────
function setNotificationDot(count) {
  const dot = document.getElementById('headerNotifDot');
  if (dot) dot.classList.toggle('is-on', count > 0);
  const bell = document.getElementById('btnNotificationsBell');
  if (bell) bell.setAttribute('aria-label', count > 0 ? t('Avisos: {p0} sin leer', {p0: count}) : t('Avisos'));
}

/** Lights the dot when there are notices the account has not seen (on any of its devices: the mark is kept by the server). */
async function refreshNotificationDot() {
  try {
    const res = await fetch('/api/notifications/unread');
    if (res.ok) setNotificationDot((await res.json()).count || 0);
  } catch (e) { /* offline: the dot stays as it was */ }
}

// ───────────────────────── push on this device ─────────────────────────
/** The dialog suggests turning push on when this device can receive it and does not yet. */
async function refreshPushHint() {
  const box = document.getElementById('notifPushHint');
  if (!box) return;
  box.hidden = !(window.EbroPush && EbroPush.state) || (await EbroPush.state()) !== 'off';
}

async function enableNotificationsHere() {
  if (window.EbroPush && EbroPush.enable) await EbroPush.enable();
  refreshPushHint();
}

document.addEventListener('DOMContentLoaded', () => {
  const start = (signedIn) => {
    if (signedIn === false) return;
    refreshNotificationDot();
    setInterval(() => { if (!document.hidden) refreshNotificationDot(); }, 5 * 60 * 1000);
    document.addEventListener('visibilitychange', () => { if (!document.hidden) refreshNotificationDot(); });
  };
  if (window.EbroAuth && EbroAuth.ready) EbroAuth.ready().then(start); else start(true);
});

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
