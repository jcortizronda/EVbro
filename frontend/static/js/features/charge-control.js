/* EVBro — control de carga (pestaña Energía): objetivo de batería, parar/iniciar y carga programada.
 *
 * Ebro no deja arrancar ni parar la carga directamente: todo se hace con la PROGRAMACIÓN del coche, y el
 * servidor (`services/charge_manager.py`) se encarga de los detalles (ventana de pausa, renovarla, hora en UTC).
 * Aquí solo se muestra el estado real y se piden las acciones.
 */
(function () {
  'use strict';

  const $ = (id) => document.getElementById(id);
  const esc = (s) => String(s ?? '').replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
  const REFRESH_MS = 30000;
  let plan = null;
  let lastLoad = 0;
  let busy = false;
  let prefilled = false;
  let followUps = [];
  const CHOICES = Array.from({ length: 11 }, (_, i) => 50 + i * 5);   // 50, 55 … 100

  const fmtDuration = (min) => {
    if (min == null) return '—';
    const h = Math.floor(min / 60), m = min % 60;
    return m ? `${h} h ${m} min` : `${h} h`;
  };
  const hhmmToMin = (v) => { const [h, m] = String(v || '').split(':').map(Number); return Number.isFinite(h) ? h * 60 + m : null; };
  const durationBetween = (a, b) => ((b - a) % 1440 + 1440) % 1440;

  // ───────────────────────── descripción del estado ─────────────────────────
  function describe(p) {
    const target = p.target_pct;
    const limit = target < 100 ? ` ${t('Se pausará al llegar al {p0} %.', {p0: target})}` : '';
    const sched = p.schedule || {};
    switch (p.status) {
      case 'charging':
        return { tone: 'ok', title: 'Cargando', detail: t('El coche está cargando.{p0}', {p0: limit}) };
      case 'charging_scheduled':
        return { tone: 'ok', title: 'Cargando (programada)', detail: t('Dentro de la ventana {p0} → {p1}.{p2}', {p0: sched.start, p1: sched.end, p2: limit}) };
      case 'scheduled':
        return { tone: 'info', title: 'Carga programada', detail: t('Cargará de {p0} a {p1} ({p2}).{p3}', {p0: sched.start, p1: sched.end, p2: fmtDuration(sched.duration_min), p3: limit}) };
      case 'hold_user': {
        const refresh = p.hold_refresh_in_min != null ? ` ${t('EVBro renueva la pausa solo (próxima en unas {p0} h).', {p0: Math.max(1, Math.round(p.hold_refresh_in_min / 60))})}` : '';
        const back = sched.enabled ? ` ${t('Al iniciar se recupera tu programación ({p0} → {p1}).', {p0: sched.start, p1: sched.end})}` : ' Al iniciar, el coche carga en cuanto esté enchufado.';
        return { tone: 'warn', title: 'Carga en pausa',
          detail: t('No cargará hasta que pulses «Iniciar carga» o programes una carga.{p0}{p1}', {p0: back, p1: refresh}) };
      }
      case 'hold_target':
        return { tone: 'warn', title: t('Pausa: objetivo {p0} % alcanzado', {p0: target}),
          detail: t('Se reanuda sola al enchufar con la batería en {p0} % o menos, o al subir el objetivo.', {p0: target - 5}) };
      default:
        return { tone: 'neutral', title: 'Carga libre', detail: t('El coche carga en cuanto se enchufa.{p0}', {p0: limit}) };
    }
  }

  // ───────────────────────── pintado ─────────────────────────
  function render() {
    if (!plan) return;
    const d = describe(plan);
    const line = $('chargeStatusLine');
    if (line) { line.textContent = d.title; line.dataset.tone = d.tone; }
    if ($('chargeStatusTitle')) $('chargeStatusTitle').textContent = d.title;
    if ($('chargeStatusDetail')) $('chargeStatusDetail').textContent = d.detail;

    if (!dragging) setSlider(plan.target_pct);

    const paused = plan.status === 'hold_user' || plan.status === 'hold_target';
    $('btnChargeStop')?.setAttribute('aria-pressed', paused ? 'true' : 'false');
    $('btnChargeStart')?.setAttribute('aria-pressed', paused ? 'false' : 'true');
    $('btnChargeStop')?.classList.toggle('is-current', paused);
    $('btnChargeStart')?.classList.toggle('is-current', !paused);

    const warn = $('chargeWarning');
    if (warn) {
      const msg = plan.last_error ? t('El último cambio no llegó al coche: {p0}', {p0: plan.last_error}) : '';
      warn.textContent = msg; warn.hidden = !msg;
    }

    renderSchedule();
    summarize();
  }

  /** Estado de la programación, bien visible: ACTIVA / EN PAUSA / ACTIVA EN EL COCHE / SIN PROGRAMAR. */
  function renderSchedule() {
    const box = $('scheduleStatus');
    if (!box) return;
    const sched = plan.schedule;
    const car = plan.car_schedule;
    const win = (w) => `${w.start} → ${w.end}`;
    let state, badge, main, sub;
    if (plan.car_pending) {
      state = 'pending'; badge = 'Aplicando…'; main = 'Enviado al coche';
      sub = 'La nube lo aceptó; esperando a que el coche lo confirme (puede tardar unos segundos).';
      pendingRetry();
    } else if (plan.status === 'scheduled' || plan.status === 'charging_scheduled') {
      state = 'on'; badge = 'Activa';
      main = win(sched || car || {});
      sub = t('{p0} · todos los días', {p0: fmtDuration((sched || car || {}).duration_min)}) + (plan.status === 'charging_scheduled' ? ' · cargando ahora' : '');
    } else if (plan.status === 'hold_user' || plan.status === 'hold_target') {
      state = 'paused'; badge = 'En pausa';
      main = sched && sched.enabled ? win(sched) : 'Sin programación propia';
      sub = (plan.status === 'hold_target'
        ? 'EVBro pausó la carga al llegar al objetivo. '
        : 'La carga está parada. ') + (sched && sched.enabled ? 'Tu programación queda en espera y se recupera al iniciar la carga.' : 'Al iniciar, el coche carga en cuanto esté enchufado.');
    } else if (car && car.enabled) {
      state = 'external'; badge = 'Activa en el coche';
      main = win(car);
      sub = t('{p0} · creada fuera de EVBro (app oficial)', {p0: fmtDuration(car.duration_min)});
    } else if (car === null && lastLoad) {
      state = 'unknown'; badge = 'Sin lectura'; main = '—'; sub = 'No se pudo leer la programación del coche.';
    } else if (car === undefined) {
      state = 'unknown'; badge = 'Comprobando…'; main = '—'; sub = 'Leyendo la programación del coche…';
    } else {
      state = 'off'; badge = 'Sin programar'; main = 'Sin programación';
      sub = 'El coche carga en cuanto se enchufa.';
    }
    box.dataset.state = state;
    $('scheduleBadge').textContent = badge;
    $('scheduleMain').textContent = main;
    $('scheduleSub').textContent = sub;

    // Con programación activa se ofrece cambiarla o quitarla; sin ella, solo crearla
    const active = state === 'on' || state === 'external' || (state === 'paused' && sched && sched.enabled);
    const clear = $('btnScheduleClear');
    if (clear) clear.hidden = !active;
    const apply = $('btnScheduleApply');
    if (apply) apply.textContent = active ? 'Cambiar programación' : 'Programar';
    // La primera vez, el formulario muestra la programación vigente (no la de ejemplo)
    const shown = (state === 'on' || state === 'paused') && sched && sched.enabled ? sched : (state === 'external' ? car : null);
    if (shown && !prefilled && !$('scheduleForm').contains(document.activeElement)) {
      prefilled = true;
      if ($('schedStart')) $('schedStart').value = shown.start;
      if ($('schedEnd')) $('schedEnd').value = shown.end;
      summarize();
    }
  }

  function summarize() {
    const out = $('scheduleSummary');
    if (!out) return;
    const start = hhmmToMin($('schedStart')?.value);
    const mode = document.querySelector('input[name="schedMode"]:checked')?.value;
    let dur = null;
    if (mode === 'end') { const end = hhmmToMin($('schedEnd')?.value); dur = start == null || end == null ? null : durationBetween(start, end); }
    else dur = (Number($('schedHours')?.value) || 0) * 60 + (Number($('schedMinutes')?.value) || 0);
    if (start == null || !dur) { out.textContent = 'Indica la hora de inicio y el final.'; return; }
    const end = (start + dur) % 1440;
    const f = (m) => `${String(Math.floor(m / 60)).padStart(2, '0')}:${String(m % 60).padStart(2, '0')}`;
    out.textContent = dur < 60
      ? 'El coche exige al menos 1 hora de carga programada.'
      : t('Cargará de {p0} a {p1} ({p2}), todos los días.', {p0: f(start), p1: f(end), p2: fmtDuration(dur)});
  }

  // ───────────────────────── confirmación de que el coche lo aplicó ─────────────────────────
  // La nube acepta la orden antes de que el coche la ejecute: se vuelve a leer a los 12 y 30 s, y mientras haya
  // diferencias («Aplicando…») cada 8 s (como mucho 6 veces).
  function scheduleFollowUps() {
    followUps.forEach(clearTimeout);
    followUps = [12000, 30000].map((ms) => setTimeout(() => load(true), ms));
  }
  let pendingTries = 0, pendingTimer = null;
  function pendingRetry() {
    if (pendingTimer || pendingTries >= 6) return;
    pendingTries += 1;
    pendingTimer = setTimeout(() => { pendingTimer = null; load(true); }, 8000);
  }

  // ───────────────────────── objetivo: círculo deslizante sobre la barra ─────────────────────────
  let dragging = false;
  const slider = () => $('energyTargetSlider');

  function setSlider(pct) {
    const el = slider();
    if (!el) return;
    const unlimited = pct >= 100;
    el.querySelector('.energy-thumb').style.left = `${pct}%`;
    el.dataset.unlimited = String(unlimited);
    el.setAttribute('aria-valuenow', String(pct));
    el.setAttribute('aria-valuetext', `${pct} %`);
    $('energyTargetLabel').textContent = t('Objetivo: {p0} %', {p0: pct});
  }

  function pctFromEvent(ev) {
    const rect = slider().querySelector('.energy-progress-track').getBoundingClientRect();
    const raw = ((ev.clientX - rect.left) / rect.width) * 100;
    return CHOICES.reduce((best, c) => (Math.abs(c - raw) < Math.abs(best - raw) ? c : best), CHOICES[0]);
  }

  function initSlider() {
    const el = slider();
    if (!el) return;
    el.querySelector('.energy-ticks').innerHTML = CHOICES.map((c) => `<i data-css="left:${c}%"></i>`).join('');
    applyDataStyles(el.querySelector('.energy-ticks'));
    let value = 100;
    const commit = () => { if (plan && value !== plan.target_pct) setTarget(value); else if (!plan && value !== 100) setTarget(value); };
    el.addEventListener('pointerdown', (ev) => {
      if (el.classList.contains('is-busy')) return;
      dragging = true; el.classList.add('is-dragging'); el.setPointerCapture(ev.pointerId);
      value = pctFromEvent(ev); setSlider(value);
    });
    el.addEventListener('pointermove', (ev) => { if (dragging) { value = pctFromEvent(ev); setSlider(value); } });
    const end = (ev) => {
      if (!dragging) return;
      dragging = false; el.classList.remove('is-dragging');
      if (ev.type === 'pointercancel') { setSlider(plan ? plan.target_pct : 100); return; }
      commit();
    };
    el.addEventListener('pointerup', end);
    el.addEventListener('pointercancel', end);
    el.addEventListener('keydown', (ev) => {
      const cur = Number(el.getAttribute('aria-valuenow')) || 100;
      const i = CHOICES.indexOf(cur);
      let next = null;
      if (ev.key === 'ArrowRight' || ev.key === 'ArrowUp') next = CHOICES[Math.min(CHOICES.length - 1, i + 1)];
      else if (ev.key === 'ArrowLeft' || ev.key === 'ArrowDown') next = CHOICES[Math.max(0, i - 1)];
      else if (ev.key === 'Home') next = CHOICES[0];
      else if (ev.key === 'End') next = 100;
      if (next === null) return;
      ev.preventDefault(); value = next; setSlider(next);
      clearTimeout(initSlider.t); initSlider.t = setTimeout(commit, 500);   // varias flechas seguidas = una sola orden
    });
    setSlider(100);
  }

  // ───────────────────────── red ─────────────────────────
  async function api(method, path, body) {
    const res = await fetch(`/api/vehicles/${currentVin}/charge/${path}`, {
      method, headers: body ? { 'Content-Type': 'application/json' } : undefined, body: body ? JSON.stringify(body) : undefined });
    const data = await res.json().catch(() => ({}));
    return { ok: res.ok, status: res.status, data };
  }

  async function load(live = true) {
    if (typeof currentVin === 'undefined' || !currentVin) return;
    try {
      const res = await fetch(`/api/vehicles/${currentVin}/charge/plan${live ? '?live=1' : ''}`);
      if (!res.ok) return;
      plan = await res.json();
      lastLoad = Date.now();
      if (!plan.car_pending) pendingTries = 0;
      render();
    } catch (e) { /* sin red: se queda lo último */ }
  }

  function fail(data) {
    const detail = data.detail || 'No se pudo aplicar el cambio.';
    const hint = data.reason === 'pin' ? ' Guarda el PIN en Ajustes → Cuenta Ebro.' : (data.reason === 'reauth' ? ' Vuelve a conectar la cuenta Ebro en Ajustes.' : '');
    showToast(`${esc(detail)}${hint}`, 'error', 7000);
  }

  // `localLabel`: texto para órdenes que EVBro solo guarda y vigila (el objetivo): no se dice «enviado al coche»
  // salvo que la respuesta indique que sí se habló con el coche (la hora de la última acción cambió).
  async function act(label, method, path, body, localLabel) {
    if (busy) return;
    busy = true;
    const toast = showToast(`${label}…`, 'loading', 20000);
    document.querySelectorAll('#chargeControl button, #chargeSchedulePanel button').forEach((b) => { b.disabled = true; });
    $('energyTargetSlider')?.classList.add('is-busy');
    try {
      const r = await api(method, path, body);
      toast.remove();
      if (r.ok) {
        const touchedCar = !localLabel || r.data.last_action_at !== (plan && plan.last_action_at);
        plan = r.data; lastLoad = Date.now();
        showToast(touchedCar ? t('{p0}: enviado al coche', {p0: label}) : `${localLabel}`, 'success', 4000);
        render();
        if (touchedCar) { load(true); scheduleFollowUps(); }
      }
      else { fail(r.data); load(false); }
    } catch (e) {
      toast.remove(); showToast('Error de red', 'error', 4000);
    } finally {
      busy = false;
      document.querySelectorAll('#chargeControl button, #chargeSchedulePanel button').forEach((b) => { b.disabled = false; });
      $('energyTargetSlider')?.classList.remove('is-busy');
    }
  }

  // ───────────────────────── acciones ─────────────────────────
  function setTarget(value) {
    const pct = Number(value);
    // El coche no tiene «batería objetivo»: se guarda en EVBro, que pausa la carga al llegar a ella
    act(t('Objetivo {p0} %', {p0: pct}), 'PUT', 'target', { target_pct: pct },
        pct >= 100 ? 'Objetivo 100 %: sin límite' : t('Objetivo {p0} % guardado en EVBro (pausará la carga al llegar)', {p0: pct}));
  }
  function start() { act('Iniciar carga', 'POST', 'start'); }
  async function stop() {
    if (!await confirmDialog('¿Parar la carga?\n\nEl coche no cargará hasta que pulses «Iniciar carga» o programes una carga.')) return;
    act('Parar carga', 'POST', 'stop');
  }
  function clearSchedule() { act('Quitar la programación', 'DELETE', 'schedule'); }

  function submitSchedule(event) {
    event.preventDefault();
    const start = $('schedStart').value;
    const mode = document.querySelector('input[name="schedMode"]:checked')?.value;
    const body = { start };
    if (mode === 'end') body.end = $('schedEnd').value;
    else body.duration_min = (Number($('schedHours').value) || 0) * 60 + (Number($('schedMinutes').value) || 0);
    if (!start || (mode === 'end' && !body.end)) { showToast('Indica la hora de inicio y el final.', 'warning'); return; }
    act('Programar carga', 'PUT', 'schedule', body);
  }

  function onModeChange() {
    const mode = document.querySelector('input[name="schedMode"]:checked')?.value;
    $('schedEndRow').hidden = mode !== 'end';
    $('schedDurRow').hidden = mode !== 'duration';
    summarize();
  }

  document.addEventListener('DOMContentLoaded', () => {
    initSlider();
    $('scheduleForm')?.addEventListener('submit', submitSchedule);
    $('scheduleForm')?.addEventListener('input', summarize);
    document.querySelectorAll('input[name="schedMode"]').forEach((r) => r.addEventListener('change', onModeChange));
    onModeChange();
    // Se refresca mientras se ve la pestaña Energía (la lectura «en vivo» consulta al coche: no más de cada 30 s)
    setInterval(() => {
      const visible = $('tabCharges')?.classList.contains('active');
      if (visible && Date.now() - lastLoad > REFRESH_MS) load(true);
    }, 3000);
  });

  window.ChargeControl = { load, setTarget, start, stop, clearSchedule };
})();
