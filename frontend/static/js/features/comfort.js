/* EVBro — botones con estado real (ON/OFF): pestaña Confort (clima, lunas, volante, asientos) y los
 * interruptor de la alarma en la portada. (La carga y la carga programada las lleva charge.js.)
 *
 * El estado sale de la telemetría del coche (`latestTelemetryData`), NO de banderas locales: antes cada
 * botón alternaba una variable propia que empezaba vacía, así que el primer toque era siempre «encender»,
 * se invertía aunque el comando fallase y no reflejaba lo que hacía el coche.
 *
 * Tras una orden aceptada se muestra el estado esperado («ON…») hasta que el coche lo confirma (push MQTT
 * o la siguiente lectura) o pasa un minuto. La pasarela de Chery solo dice «aceptado»: el coche tarda
 * entre 3 y 15 s en ejecutarla.
 */
(function () {
  'use strict';

  const OPTIMISTIC_TTL_MS = 60000;      // cuánto se mantiene el estado esperado si el coche no lo confirma
  const REFRESH_AFTER_MS = 12000;       // una lectura extra, cuando el coche ya debería haber ejecutado la orden

  // `rawKeys`: campos del coche que informan de esta función. Si ninguno ha llegado, el estado es «desconocido».
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

  // Iconos de desempañado y volante: el dibujo de la luna (parabrisas curvo / luneta rectangular) con tres
  // ondas de calor que suben, y un volante con calor. Son los pictogramas habituales del salpicadero.
  const SVG = (inner) => `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">${inner}</svg>`;
  const HEAT_WAVES = (y) => [8.5, 12, 15.5].map((x) => `<path d="M${x} ${y} q1.1 -1.2 0 -2.4 t0 -2.4"/>`).join('');
  const ICONS = {
    windshield: SVG(`<path d="M2.5 19 L5.8 6.2 Q12 3.2 18.2 6.2 L21.5 19 Z"/>${HEAT_WAVES(16)}`),
    rear: SVG(`<rect x="2.5" y="5" width="19" height="14" rx="3"/>${HEAT_WAVES(16)}`),
    // calor (llama) y ventilación (copo): pictogramas simples, sin depender de la fuente de emojis del dispositivo
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
  // Interruptores de la portada y de Energía. `titles` cambia el título del botón según el estado.
  let alarmState = { on: null, ts: 0 };   // la alarma no viaja en la telemetría: se le pregunta al coche
  CONTROLS.push(
    { id: 'alarm', group: 'home', label: 'Alarma', source: () => ({ on: alarmState.on, level: null }),
      on: () => ['alarma_on', 'Activar alarma antirrobo', null], off: () => ['alarma_off', 'Desactivar alarma antirrobo', null] },
  );
  const BY_ID = Object.fromEntries(CONTROLS.map((c) => [c.id, c]));
  // orden → (control, estado que deja): para fijar el estado esperado cuando el coche acepta una orden
  const BY_COMMAND = {};
  for (const c of CONTROLS) {
    BY_COMMAND[c.on()[0]] = { id: c.id, on: true };
    BY_COMMAND[c.off()[0]] = { id: c.id, on: false };
  }

  const optimistic = {};      // id → { on, ts }: lo que esperamos tras una orden aceptada
  const pending = new Set();  // ids con una orden en vuelo

  // ───────────────────────── estado ─────────────────────────
  /** Estado según el coche: {on: true|false|null, level: 0-3|null}. null = el coche aún no ha informado. */
  function readCar(control) {
    if (control.source) return control.source();
    const t = (typeof latestTelemetryData !== 'undefined' && latestTelemetryData) || null;
    if (!t) return { on: null, level: null };
    const raw = t.raw_data || t.raw || {};
    if (!control.rawKeys.some((k) => k in raw)) return { on: null, level: null };
    const v = control.read(t);
    if (v === null || v === undefined) return { on: null, level: null };   // la clave llegó pero el valor no se pudo interpretar
    if (typeof v === 'number') return { on: v > 0, level: v };
    return { on: v === true, level: null };
  }

  /** Estado que se enseña: el del coche, o el esperado si acabamos de mandar una orden y aún no lo confirma. */
  function effective(control) {
    const car = readCar(control);
    const o = optimistic[control.id];
    if (o) {
      if (Date.now() - o.ts > OPTIMISTIC_TTL_MS || car.on === o.on) delete optimistic[control.id];   // caducado o confirmado
      else return { ...car, on: o.on, waiting: true };
    }
    return car;
  }

  function stateText(control, st) {
    if (pending.has(control.id)) return '…';
    if (st.on === null) return '—';
    if (!st.on) return 'OFF';
    return st.level ? t('ON · {p0}', {p0: st.level}) : 'ON';      // los asientos traseros informan de nivel (1-3)
  }
  function stateKey(control, st) {
    if (pending.has(control.id)) return 'pending';
    if (st.on === null) return 'unknown';
    return st.on ? (st.waiting ? 'on-waiting' : 'on') : (st.waiting ? 'off-waiting' : 'off');
  }

  // ───────────────────────── pintado ─────────────────────────
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
    el.classList.toggle('active', st.on === true);              // estilo del botón redondo del climatizador
    el.disabled = pending.has(control.id);
    const badge = el.querySelector('.comfort-state');          // el botón redondo del climatizador no lleva texto
    if (badge) {
      badge.dataset.state = key;
      badge.textContent = stateText(control, st);
      badge.title = key.endsWith('waiting') ? 'Esperando a que el coche lo confirme' : '';
    }
    el.dataset.state = key;
    if (control.titles) {                                         // p. ej. «Iniciar carga» ↔ «Detener carga»
      const title = el.querySelector('.card-title');
      if (title && st.on !== null) title.textContent = st.on ? control.titles.on : control.titles.off;
    }
  }

  function renderComfort() {
    build();
    document.querySelectorAll('[data-comfort-id]').forEach((el) => { const c = BY_ID[el.dataset.comfortId]; if (c) paint(el, c); });
    const ac = BY_ID.ac;
    const power = document.getElementById('btnClimatePowerToggle');
    if (power) paint(power, ac);                                  // el botón redondo: activo + aria-pressed
    const badge = document.getElementById('climateStateBadge');
    if (badge) { const st = effective(ac); badge.dataset.state = stateKey(ac, st); badge.textContent = stateText(ac, st); }
    const head = document.getElementById('climateStatusBadge');
    if (head) {
      const known = CONTROLS.filter((c) => (c.group === 'defrost' || c.group === 'seats') && effective(c).on === true).length + (effective(ac).on ? 1 : 0);
      head.textContent = effective(ac).on === null ? 'Sin datos' : (known ? `${known} activa${known > 1 ? 's' : ''}` : 'Todo apagado');
    }
  }

  // ───────────────────────── acciones ─────────────────────────
  async function toggleComfort(id) {
    const control = BY_ID[id];
    if (!control || pending.has(id)) return;
    if (typeof currentVin === 'undefined' || !currentVin) { showToast('No hay ningún vehículo seleccionado.', 'warning'); return; }
    const desired = effective(control).on !== true;               // desconocido cuenta como apagado: el primer toque enciende
    pending.add(id); renderComfort();
    const [key, name, params] = (desired ? control.on : control.off)();
    try { await executeCommandDirect(key, name, params); } finally { pending.delete(id); }
    renderComfort();                                              // si el coche aceptó la orden, el evento de abajo ya fijó el estado esperado
  }

  // Cualquier orden aceptada (también la que pasa por el diálogo del PIN) fija el estado que esperamos ver.
  window.addEventListener('evbro:command-accepted', (event) => {
    const hit = BY_COMMAND[event.detail && event.detail.key];
    if (!hit) return;
    optimistic[hit.id] = { on: hit.on, ts: Date.now() };
    scheduleRefresh();
    if (hit.id === 'alarm') setTimeout(refreshAlarm, 6000);
    renderComfort();
  });

  /** Pregunta al coche si la alarma está activada (solo lectura, sin PIN). */
  async function refreshAlarm() {
    if (typeof currentVin === 'undefined' || !currentVin) return;
    try {
      const res = await fetch(`/api/vehicles/${currentVin}/alarm`);
      if (!res.ok) return;
      const data = await res.json();
      alarmState = { on: data.enabled === null ? null : !!data.enabled, ts: Date.now() };
    } catch (e) { /* sin red: se queda como estaba */ }
    renderComfort();
  }

  let refreshTimer = null;
  function scheduleRefresh() {
    clearTimeout(refreshTimer);
    refreshTimer = setTimeout(async () => {
      try { await fetch(`/api/vehicles/${currentVin}/poll`, { method: 'POST' }); } catch (e) { /* la lectura normal lo corregirá */ }
      renderComfort();
    }, REFRESH_AFTER_MS);
  }

  /** Preajuste de temperatura: es el mismo comando de clima, con otra temperatura. Enciende el climatizador. */
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

  /** Al soltar el deslizador: si el climatizador ya está encendido se aplica la nueva temperatura; si no, solo se elige. */
  function onClimateSliderChange(val) {
    currentTargetTemp = parseFloat(val);
    syncClimateUI();
    if (effective(BY_ID.ac).on === true) comfortPreset(currentTargetTemp);
  }

  document.addEventListener('DOMContentLoaded', () => {
    renderComfort();
    setInterval(renderComfort, 5000);       // vence el estado esperado aunque no llegue telemetría
    // La alarma se consulta al coche: al abrir la app y, mientras se ve la portada, como mucho una vez por minuto
    setTimeout(refreshAlarm, 3000);
    setInterval(() => {
      const home = document.getElementById('tabOverview');
      if (home && home.classList.contains('active') && Date.now() - alarmState.ts > 60000) refreshAlarm();
    }, 10000);
  });

  Object.assign(window, { renderComfort, toggleComfort, comfortPreset, onClimateSliderChange, refreshAlarm,
    toggleClimatePower: () => toggleComfort('ac') });
})();
