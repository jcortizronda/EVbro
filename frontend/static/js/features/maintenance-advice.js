/* EVbro — Mantenimiento → «Recomendaciones de EVbro»: checks outside the brand's plan, timed by how the car is used (brakes, old
 * petrol, tyres, 12 V battery, wipers). Each one that is due is answered with «Revisado, está bien», «Registrar intervención» or
 * «Recuérdamelo más adelante», and any of them can be switched off. Data and rules: backend services/maintenance_advice.py
 * (GET /api/vehicles/{vin}/maintenance/advice).
 * (Classic script: shares the global scope with the other files; see the load order in templates/index.html.)
 */
(function () {
  'use strict';

  const $ = (id) => document.getElementById(id);
  let data = null;
  let busy = false;

  const locale = () => (window.I18n && I18n.locale) || 'es-ES';
  const num = (n) => Math.round(n).toLocaleString(locale());
  const day = (iso) => serverDate(iso).toLocaleDateString(locale(), { day: 'numeric', month: 'short', year: 'numeric' });

  /** A span of days in the unit that reads best: days, months or years. */
  function span(days) {
    const d = Math.abs(Math.round(days));
    if (d < 60) return d === 1 ? t('1 día') : t('{p0} días', { p0: d });
    if (d < 730) return t('{p0} meses', { p0: Math.round(d / 30.44) });
    return t('{p0} años', { p0: (d / 365.25).toLocaleString(locale(), { maximumFractionDigits: 1 }) });
  }

  const BASE = { check: 'la última revisión', part: 'el montaje', start: 'la entrega del coche', refuel: 'el último repostaje' };

  /** How often: «Cada 15.000 km o 12 meses», «A los 5 años y después cada año»… */
  function every(a) {
    if (a.key === 'fuel_age') return t('A los {p0} meses sin repostar', { p0: a.interval_months });
    if (a.key === 'tyre_age' || a.key === 'battery_12v') return t('A los {p0} años y después cada año', { p0: a.interval_months / 12 });
    if (a.interval_km && a.interval_months) return t('Cada {p0} km o {p1} meses', { p0: num(a.interval_km), p1: a.interval_months });
    if (a.interval_km) return t('Cada {p0} km', { p0: num(a.interval_km) });
    return t('Cada {p0} meses', { p0: a.interval_months });
  }

  /** Where it stands: what has gone by when it is due, what is left when it is not. */
  function standing(a) {
    if (a.status === 'off') return t('Desactivada');
    if (a.status === 'snoozed') {
      return a.snooze_odometer
        ? t('Aplazada hasta el {p0} o los {p1} km', { p0: day(a.snooze_until), p1: num(a.snooze_odometer) })
        : t('Aplazada hasta el {p0}', { p0: day(a.snooze_until) });
    }
    const from = t(BASE[a.base_kind] || BASE.start);
    if (a.status === 'due') {
      const gone = [];
      if (a.due_odometer !== null && a.remaining_km <= 0) gone.push(t('{p0} km desde {p1}', { p0: num(data.odometer - a.base_odometer), p1: from }));
      if (a.due_date && a.remaining_days <= 0 && a.base_date) {
        gone.push(t('{p0} desde {p1}', { p0: span((Date.now() - serverDate(a.base_date).getTime()) / 86400000), p1: from }));
      }
      return gone.join(' · ');
    }
    const km = a.due_odometer !== null ? t('{p0} km', { p0: num(a.remaining_km) }) : null;
    const time = a.due_date ? span(a.remaining_days) : null;
    return km && time ? t('Faltan {p0} o {p1}, lo que llegue antes', { p0: km, p1: time }) : t('Faltan {p0}', { p0: km || time });
  }

  function button(text, cls, action) {
    const b = document.createElement('button');
    b.type = 'button';
    b.className = cls;
    b.textContent = text;
    b.dataset.onClick = action;
    return b;
  }

  function el(tag, cls, text) {
    const e = document.createElement(tag);
    if (cls) e.className = cls;
    if (text !== undefined && text !== null) e.textContent = text;
    return e;
  }

  function row(a) {
    const open = a.status === 'due' || a.status === 'snoozed';
    const li = el('li', 'ma-item');
    li.dataset.status = a.status;
    li.dataset.key = a.key;
    const head = el('div', 'ma-head');
    const title = el('div', 'ma-title');
    title.append(el('strong', '', a.name), el('span', 'ma-standing', standing(a)));
    head.append(title, el('span', 'ma-pill', { due: t('Toca ya'), snoozed: t('Aplazada'), ok: t('Al día'), off: t('Desactivada') }[a.status]));
    li.appendChild(head);
    if (open) {
      li.appendChild(el('p', 'ma-why', a.why));
      if (a.note) li.appendChild(el('p', 'ma-note', a.note));
    }
    li.appendChild(el('small', 'ma-every', every(a)));
    const actions = el('div', 'ma-actions needs-edit');
    if (open) {
      actions.appendChild(button(t('Revisado, está bien'), 'btn-primary', `MaintAdvice.check('${a.key}')`));
      if (a.can_log) actions.appendChild(button(t('Registrar intervención'), 'btn-secondary', `MaintAdvice.log('${a.key}')`));
      if (a.status === 'due') actions.appendChild(button(t('Recuérdamelo más adelante'), 'btn-secondary', `MaintAdvice.later('${a.key}')`));
      actions.appendChild(button(t('No me interesa'), 'link-btn ma-link', `MaintAdvice.enable('${a.key}', false)`));
    } else if (a.status === 'ok') {
      actions.appendChild(button(t('Lo he revisado hoy'), 'link-btn ma-link', `MaintAdvice.check('${a.key}')`));
      actions.appendChild(button(t('Desactivar'), 'link-btn ma-link', `MaintAdvice.enable('${a.key}', false)`));
    } else {
      actions.appendChild(button(t('Activar'), 'link-btn ma-link', `MaintAdvice.enable('${a.key}', true)`));
    }
    li.appendChild(actions);
    return li;
  }

  function render() {
    const card = $('maintAdviceCard');
    if (!card) return;
    const items = (data && data.advice) || [];
    card.hidden = !items.length;
    const list = $('maintAdviceList');
    list.replaceChildren(...items.map(row));
    const count = $('maintAdviceCount');
    count.hidden = !data || !data.due;
    count.textContent = data && data.due ? String(data.due) : '';
  }

  async function load() {
    if (!$('maintAdviceCard') || typeof currentVin === 'undefined' || !currentVin) return;
    try {
      const res = await fetch(`/api/vehicles/${encodeURIComponent(currentVin)}/maintenance/advice`);
      if (!res.ok) throw new Error(res.status);
      data = await res.json();
    } catch (e) {
      data = null;                                        // the card is simply not shown: the plan above does not depend on it
    }
    render();
  }

  async function send(method, path, body, done) {
    if (busy || !currentVin) return;
    busy = true;
    try {
      const res = await fetch(`/api/vehicles/${encodeURIComponent(currentVin)}/maintenance/advice/${path}`,
        { method, headers: { 'Content-Type': 'application/json' }, body: body ? JSON.stringify(body) : undefined });
      const answer = await res.json().catch(() => ({}));
      if (!res.ok) throw new Error(answer.detail || res.status);
      data = answer;
      render();
      if (done) showToast(done, 'success');
    } catch (e) {
      showToast(t('No se pudo guardar: {p0}', { p0: e.message || e }), 'error', 6000);
    } finally {
      busy = false;
    }
  }

  const check = (key) => send('POST', `${key}/check`, null, t('Anotado: se cuenta de nuevo desde hoy.'));
  const later = (key) => send('POST', `${key}/snooze`, null, t('Te lo recordaré más adelante.'));
  const enable = (key, on) => send('PUT', key, { enabled: on }, on ? t('Recomendación activada.') : t('Recomendación desactivada.'));

  /** «Registrar intervención»: the usual one-off intervention, started with this recommendation (it answers it when saved). */
  function log(key) {
    const a = ((data && data.advice) || []).find((x) => x.key === key);
    if (!a || typeof showMaintEventModal !== 'function') return;
    showMaintEventModal();
    $('evTitle').value = a.name;
    const cat = $('evCat');
    if (a.category && [...cat.options].some((o) => o.value === a.category || o.textContent === a.category)) cat.value = a.category;
    window.maintEventAdviceKey = key;                     // sent with the intervention (features/maintenance.js, submitMaintEvent)
  }

  window.MaintAdvice = { load, check, later, enable, log };
})();
