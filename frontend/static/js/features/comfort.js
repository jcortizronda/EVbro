/* EVBro — buttons with real state (ON/OFF): Confort tab (climate, windows, steering wheel, seats) and the
 * alarm switch on the home tab. (Charging and scheduled charging are handled by charge.js.)
 *
 * The state comes from the car's telemetry (`latestTelemetryData`), NOT from local flags: each button used to
 * toggle its own variable that started empty, so the first tap was always «turn on», it flipped even when the
 * command failed and it did not reflect what the car was doing.
 *
 * After an accepted order the expected state («ON…») is shown until the car confirms it (MQTT push or the
 * next reading) or a minute passes. The Chery gateway only says «accepted»: the car takes 3 to 15 s to
 * carry it out.
 */
(function () {
  'use strict';

  const OPTIMISTIC_TTL_MS = 60000;      // how long the expected state is kept if the car does not confirm it
  const REFRESH_AFTER_MS = 12000;       // an extra reading, when the car should already have carried out the order

  // `rawKeys`: car fields that report this function. If none has arrived, the state is «unknown».
  const SEAT_NAMES = [
    { id: 'driver', cmd: 'conductor', label: 'Conductor' },
    { id: 'passenger', cmd: 'pasajero', label: 'Copiloto' },
    { id: 'rear_left', cmd: 'tras_izq', label: 'Trasero izq.' },
    { id: 'rear_right', cmd: 'tras_der', label: 'Trasero der.' },
  ];
  const SEAT_RAW = {
    heat: { driver: 'dSeatHeatingState', passenger: 'pSeatHeatingState', rear_left: 'lSeatHeatingState2', rear_right: 'rSeatHeatingState2' },
    vent: { driver: 'dSeatVentilateState', passenger: 'pSeatVentilateState', rear_left: 'lSeatVentilateState2', rear_right: 'rSeatVentilateState2' },
  };

  // Demister and steering wheel icons: the window drawing (curved windscreen / rectangular rear window) with three
  // rising heat waves, and a heated steering wheel. They are the usual dashboard pictograms.
  const SVG = (inner) => `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">${inner}</svg>`;
  const HEAT_WAVES = (y) => [8.5, 12, 15.5].map((x) => `<path d="M${x} ${y} q1.1 -1.2 0 -2.4 t0 -2.4"/>`).join('');
  const ICONS = {
    windshield: SVG(`<path d="M2.5 19 L5.8 6.2 Q12 3.2 18.2 6.2 L21.5 19 Z"/>${HEAT_WAVES(16)}`),
    rear: SVG(`<rect x="2.5" y="5" width="19" height="14" rx="3"/>${HEAT_WAVES(16)}`),
    // heat (flame) and ventilation (snowflake): simple pictograms, not relying on the device's emoji font
    flame: SVG('<path d="M8.5 14.5A2.5 2.5 0 0 0 11 12c0-1.38-.5-2-1-3-1.072-2.143-.224-4.054 2-6 .5 2.5 2 4.9 4 6.5 2 1.6 3 3.5 3 5.5a7 7 0 1 1-14 0c0-1.153.433-2.294 1-3a2.5 2.5 0 0 0 2.5 2.5z"/>'),
    snow: SVG('<line x1="2" y1="12" x2="22" y2="12"/><line x1="12" y1="2" x2="12" y2="22"/><path d="m20 16-4-4 4-4"/><path d="m4 8 4 4-4 4"/><path d="m16 4-4 4-4-4"/><path d="m8 20 4-4 4 4"/>'),
    wheel: SVG('<circle cx="12" cy="14.5" r="7"/><circle cx="12" cy="14.5" r="1.9"/><path d="M5.2 13.3 L10.1 14 M18.8 13.3 L13.9 14 M12 16.4 V21.5"/><path d="M9 6 q1 -1.1 0 -2.2 t0 -2.2"/><path d="M15 6 q1 -1.1 0 -2.2 t0 -2.2"/>'),
  };

  const CONTROLS = [
    { id: 'ac', group: 'ac', label: 'Climatizador', rawKeys: ['frontHVACState'], read: (t) => t.climate_on,
      on: () => ['clima_on', t('Encender climatizador a {p0} °C', {p0: currentTargetTemp.toFixed(1)}), { temperature: currentTargetTemp.toFixed(1) }],
      off: () => ['clima_off', 'Apagar climatizador', null] },
    { id: 'windshield', group: 'defrost', icon: ICONS.windshield, label: 'Parabrisas', hint: 'Desempañar delantero',
      rawKeys: ['frontWindshieldHeat', 'fWinHeatingState'], read: (t) => t.front_windshield_heat,
      on: () => ['defrost_parabrisas', 'Desempañar parabrisas', null], off: () => ['defrost_parabrisas_off', 'Apagar desempañado del parabrisas', null] },
    { id: 'rear', group: 'defrost', icon: ICONS.rear, label: 'Luneta trasera', hint: 'Desempañar trasera',
      rawKeys: ['rWinHeatingState'], read: (t) => t.rear_windshield_heat,
      on: () => ['defrost_luneta', 'Desempañar luneta trasera', null], off: () => ['defrost_luneta_off', 'Apagar desempañado de la luneta', null] },
    { id: 'wheel', group: 'defrost', icon: ICONS.wheel, label: 'Volante', hint: 'Calefactado',
      rawKeys: ['steerWheelHeating'], read: (t) => t.steer_wheel_heat,
      on: () => ['volante_caliente', 'Encender volante calefactado', null], off: () => ['volante_caliente_off', 'Apagar volante calefactado', null] },
  ];
  for (const seat of SEAT_NAMES) {
    for (const [kind, cmdWord, field, verb] of [['heat', 'caliente', 'seat_heating', 'Calefacción'], ['vent', 'ventilacion', 'seat_ventilation', 'Ventilación']]) {
      CONTROLS.push({
        id: `seat_${seat.id}_${kind}`, group: 'seats', seat: seat.id, kind,
        label: kind === 'heat' ? 'Calor' : 'Ventilación', icon: kind === 'heat' ? ICONS.flame : ICONS.snow,
        rawKeys: [SEAT_RAW[kind][seat.id]], read: (t) => (t[field] || {})[seat.id],
        on: () => [`asiento_${seat.cmd}_${cmdWord}`, `${verb} asiento ${seat.label.toLowerCase()}`, null],
        off: () => [`asiento_${seat.cmd}_${cmdWord}_off`, t('Apagar {p0} del asiento {p1}', {p0: verb.toLowerCase(), p1: seat.label.toLowerCase()}), null],
      });
    }
  }
  // Switches on the home tab and in Energía. `titles` changes the button title by state.
  let alarmState = { on: null, ts: 0 };   // the alarm is not part of the telemetry: the car is asked
  CONTROLS.push(
    { id: 'alarm', group: 'home', label: 'Alarma', source: () => ({ on: alarmState.on, level: null }),
      on: () => ['alarma_on', 'Activar alarma antirrobo', null], off: () => ['alarma_off', 'Desactivar alarma antirrobo', null] },
  );
  const BY_ID = Object.fromEntries(CONTROLS.map((c) => [c.id, c]));
  // order → (control, resulting state): to set the expected state when the car accepts an order
  const BY_COMMAND = {};
  for (const c of CONTROLS) {
    BY_COMMAND[c.on()[0]] = { id: c.id, on: true };
    BY_COMMAND[c.off()[0]] = { id: c.id, on: false };
  }

  const optimistic = {};      // id → { on, ts }: what we expect after an accepted order
  const pending = new Set();  // ids with an order in flight

  // ───────────────────────── state ─────────────────────────
  /** State according to the car: {on: true|false|null, level: 0-3|null}. null = the car has not reported yet. */
  function readCar(control) {
    if (control.source) return control.source();
    const t = (typeof latestTelemetryData !== 'undefined' && latestTelemetryData) || null;
    if (!t) return { on: null, level: null };
    const raw = t.raw_data || t.raw || {};
    if (!control.rawKeys.some((k) => k in raw)) return { on: null, level: null };
    const v = control.read(t);
    if (v === null || v === undefined) return { on: null, level: null };   // the key arrived but the value could not be parsed
    if (typeof v === 'number') return { on: v > 0, level: v };
    return { on: v === true, level: null };
  }

  /** State to show: the car's, or the expected one if we just sent an order and it has not confirmed yet. */
  function effective(control) {
    const car = readCar(control);
    const o = optimistic[control.id];
    if (o) {
      if (Date.now() - o.ts > OPTIMISTIC_TTL_MS || car.on === o.on) delete optimistic[control.id];   // expired or confirmed
      else return { ...car, on: o.on, waiting: true };
    }
    return car;
  }

  /** The cloud refused this control's command on this car (A00084, remembered by the server): it says so instead of «—». */
  function rejected(control) {
    return typeof carRejects === 'function' && !!control.on && carRejects(control.on()[0]);
  }

  function stateText(control, st) {
    if (pending.has(control.id)) return '…';
    if (st.on === null) return rejected(control) ? t('No permitido') : '—';
    if (!st.on) return 'OFF';
    return st.level ? t('ON · {p0}', {p0: st.level}) : 'ON';      // the rear seats report a level (1-3)
  }
  function stateKey(control, st) {
    if (pending.has(control.id)) return 'pending';
    if (st.on === null) return rejected(control) ? 'rejected' : 'unknown';
    return st.on ? (st.waiting ? 'on-waiting' : 'on') : (st.waiting ? 'off-waiting' : 'off');
  }

  // ───────────────────────── rendering ─────────────────────────
  function tileHtml(control, compact) {
    const text = compact
      ? `<span class="comfort-icon" aria-hidden="true">${control.icon}</span><span class="comfort-text"><strong>${control.label}</strong></span>`
      : `<span class="comfort-icon" aria-hidden="true">${control.icon}</span><span class="comfort-text"><strong>${control.label}</strong><small>${control.hint}</small></span>`;
    return `<button type="button" class="comfort-tile${compact ? ' compact' : ''}" data-comfort-id="${control.id}" aria-pressed="false" data-on-click="toggleComfort('${control.id}')">`
      + `${text}<span class="comfort-state" data-state="unknown">—</span></button>`;
  }

  function build() {
    const defrost = document.getElementById('comfortDefrost');
    const seats = document.getElementById('comfortSeats');
    if (defrost && !defrost.children.length) {
      defrost.innerHTML = CONTROLS.filter((c) => c.group === 'defrost').map((c) => tileHtml(c, false)).join('');
    }
    if (seats && !seats.children.length) {
      seats.innerHTML = SEAT_NAMES.map((s) => `<div class="seat-card"><div class="seat-title">${s.label}</div>`
        + CONTROLS.filter((c) => c.seat === s.id).map((c) => tileHtml(c, true)).join('') + '</div>').join('');
    }
  }

  function paint(el, control) {
    const st = effective(control);
    const key = stateKey(control, st);
    el.setAttribute('aria-pressed', st.on === true ? 'true' : 'false');
    el.classList.toggle('is-on', st.on === true);
    el.classList.toggle('active', st.on === true);              // style of the round climate button
    el.disabled = pending.has(control.id);
    const refused = rejected(control);
    el.classList.toggle('is-rejected', refused);                // the car said no to this command: dimmed, still visible
    if (refused) { el.title = t('La nube de Ebro no permite este mando en este coche (A00084). Pulsa para volver a intentarlo.'); el.dataset.rejected = '1'; }
    else if (el.dataset.rejected) { el.title = ''; delete el.dataset.rejected; }
    const badge = el.querySelector('.comfort-state');          // the round climate button has no text
    if (badge) {
      badge.dataset.state = key;
      badge.textContent = stateText(control, st);
      badge.title = key.endsWith('waiting') ? 'Esperando a que el coche lo confirme' : '';
    }
    el.dataset.state = key;
    if (control.titles) {                                         // e.g. «Iniciar carga» ↔ «Detener carga»
      const title = el.querySelector('.card-title');
      if (title && st.on !== null) title.textContent = st.on ? control.titles.on : control.titles.off;
    }
  }

  function renderComfort() {
    build();
    document.querySelectorAll('[data-comfort-id]').forEach((el) => { const c = BY_ID[el.dataset.comfortId]; if (c) paint(el, c); });
    const ac = BY_ID.ac;
    const power = document.getElementById('btnClimatePowerToggle');
    if (power) paint(power, ac);                                  // the round button: active + aria-pressed
    const badge = document.getElementById('climateStateBadge');
    if (badge) { const st = effective(ac); badge.dataset.state = stateKey(ac, st); badge.textContent = stateText(ac, st); }
    const head = document.getElementById('climateStatusBadge');
    if (head) {
      const known = CONTROLS.filter((c) => (c.group === 'defrost' || c.group === 'seats') && effective(c).on === true).length + (effective(ac).on ? 1 : 0);
      head.textContent = effective(ac).on === null ? 'Sin datos' : (known ? `${known} activa${known > 1 ? 's' : ''}` : 'Todo apagado');
    }
  }

  // ───────────────────────── actions ─────────────────────────
  async function toggleComfort(id) {
    const control = BY_ID[id];
    if (!control || pending.has(id)) return;
    if (typeof currentVin === 'undefined' || !currentVin) { showToast('No hay ningún vehículo seleccionado.', 'warning'); return; }
    const desired = effective(control).on !== true;               // unknown counts as off: the first tap turns it on
    pending.add(id); renderComfort();
    const [key, name, params] = (desired ? control.on : control.off)();
    try { await executeCommandDirect(key, name, params); } finally { pending.delete(id); }
    renderComfort();                                              // if the car accepted the order, the event below already set the expected state
  }

  // Any accepted order (also one that goes through the PIN dialog) sets the state we expect to see.
  window.addEventListener('evbro:command-accepted', (event) => {
    const hit = BY_COMMAND[event.detail && event.detail.key];
    if (!hit) return;
    optimistic[hit.id] = { on: hit.on, ts: Date.now() };
    scheduleRefresh();
    if (hit.id === 'alarm') setTimeout(refreshAlarm, 6000);
    renderComfort();
  });

  /** Asks the car whether the alarm is armed (read only, no PIN). */
  async function refreshAlarm() {
    if (typeof currentVin === 'undefined' || !currentVin) return;
    try {
      const res = await fetch(`/api/vehicles/${currentVin}/alarm`);
      if (!res.ok) return;
      const data = await res.json();
      alarmState = { on: data.enabled === null ? null : !!data.enabled, ts: Date.now() };
    } catch (e) { /* no network: leave it as it was */ }
    renderComfort();
  }

  let refreshTimer = null;
  function scheduleRefresh() {
    clearTimeout(refreshTimer);
    refreshTimer = setTimeout(async () => {
      try { await fetch(`/api/vehicles/${currentVin}/poll`, { method: 'POST' }); } catch (e) { /* the normal reading will correct it */ }
      renderComfort();
    }, REFRESH_AFTER_MS);
  }

  /** Temperature preset: the same climate command with another temperature. Turns the climate control on. */
  async function comfortPreset(celsius) {
    currentTargetTemp = celsius;
    syncClimateUI();
    const id = 'ac';
    if (pending.has(id)) return;
    pending.add(id); renderComfort();
    try { await executeCommandDirect('clima_on', t('Encender climatizador a {p0} °C', {p0: celsius}), { temperature: celsius.toFixed(1) }); }
    finally { pending.delete(id); }
    renderComfort();
  }

  /** On releasing the slider: if the climate control is already on, the new temperature is applied; otherwise it is only selected. */
  function onClimateSliderChange(val) {
    currentTargetTemp = parseFloat(val);
    syncClimateUI();
    if (effective(BY_ID.ac).on === true) comfortPreset(currentTargetTemp);
  }

  document.addEventListener('DOMContentLoaded', () => {
    renderComfort();
    setInterval(renderComfort, 5000);       // expire the expected state even if no telemetry arrives
    // The alarm is queried from the car: when the app opens and, while the home tab is visible, at most once a minute
    setTimeout(refreshAlarm, 3000);
    setInterval(() => {
      const home = document.getElementById('tabOverview');
      if (home && home.classList.contains('active') && Date.now() - alarmState.ts > 60000) refreshAlarm();
    }, 10000);
  });

  Object.assign(window, { renderComfort, toggleComfort, comfortPreset, onClimateSliderChange, refreshAlarm,
    toggleClimatePower: () => toggleComfort('ac') });
})();
