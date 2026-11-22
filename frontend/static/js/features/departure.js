/* EVBro — Scheduled departure (Confort): «Reserva tu viaje» of the official app. The car keeps the battery warm before the
 * time you leave. The plan lives in the car: it is read from what the car already reports and changed with one order, sent by
 * the server with the saved PIN (like the charge schedule). Only the time and the switch of each departure are edited here;
 * the days stay as the car has them (their numbering has not been established yet: services/departure.py).
 * (Classic script: shares the global scope with the other files; see the load order in templates/index.html.)
 */
(function () {
  const el = (id) => document.getElementById(id);
  const CONFIRM_AFTER_MS = 12000;      // the car takes a few seconds to report the plan back
  let plan = null;                     // { known, enabled, departures: [{time, days, enabled}] } as the car has it
  let draft = [];                      // what the form shows: [{time, enabled}]
  let saving = false;

  const daysText = (days) => ((days || []).length === 7 ? t('Todos los días') : t('{p0} días a la semana, los que tiene el coche', {p0: (days || []).length}));
  const dirty = () => !!plan && JSON.stringify(draft) !== JSON.stringify(plan.departures.map((d) => ({ time: d.time, enabled: d.enabled })));

  async function load() {
    if (typeof currentVin === 'undefined' || !currentVin || !el('departurePanel')) return;
    try {
      const res = await fetch(`/api/vehicles/${currentVin}/departure`);
      if (!res.ok) throw new Error(res.status);
      take(await res.json());
    } catch (e) {
      plan = null;
      render();
    }
  }

  function take(data) {
    plan = data;
    // A car that has never had a departure gets one row to fill in (the server creates it with every day of the week)
    if (plan.known && !plan.departures.length) plan.departures = [{ time: '07:00', days: [1, 2, 3, 4, 5, 6, 7], enabled: false }];
    draft = plan.departures.map((d) => ({ time: d.time, enabled: d.enabled }));
    render();
  }

  function render() {
    const list = el('departureList');
    if (!list) return;
    const badge = el('departureBadge');
    const pinHint = el('departurePinHint');
    if (pinHint) pinHint.hidden = !(typeof currentAccountInfo !== 'undefined' && currentAccountInfo && currentAccountInfo.has_pin === false);
    if (!plan || !plan.known) {
      if (badge) { badge.dataset.state = 'unknown'; badge.textContent = '—'; }
      list.innerHTML = `<p class="comfort-hint">${plan ? t('Este coche todavía no ha informado de su salida programada.') : t('No se pudo leer la salida programada.')}</p>`;
      el('departureSave').disabled = true;
      return;
    }
    if (badge) { badge.dataset.state = plan.enabled ? 'on' : 'off'; badge.textContent = plan.enabled ? 'ON' : 'OFF'; }
    list.innerHTML = draft.map((d, i) => `
      <div class="csched-row dep-row ${d.enabled ? '' : 'is-off'}">
        <div class="csched-main">
          <label class="csched-field dep-time"><span>${t('Hora de salida')}</span><input type="time" class="form-control" value="${escapeHtml(d.time)}" step="300" data-on-change="Departure.setTime(${i}, this.value)"></label>
          <small>${escapeHtml(daysText(plan.departures[i].days))}</small>
        </div>
        <label class="csched-switch" title="${d.enabled ? t('Activa') : t('En pausa')}">
          <input type="checkbox" ${d.enabled ? 'checked' : ''} data-on-change="Departure.setEnabled(${i}, this.checked)">
          <span>${d.enabled ? t('Activa') : t('En pausa')}</span>
        </label>
      </div>`).join('');
    el('departureSave').disabled = saving || !dirty();
  }

  function setTime(i, value) { if (draft[i] && value) { draft[i].time = value; render(); } }
  function setEnabled(i, on) { if (draft[i]) { draft[i].enabled = !!on; render(); } }

  async function save() {
    if (saving || !dirty()) return;
    saving = true;
    render();
    try {
      const res = await fetch(`/api/vehicles/${currentVin}/departure`, {
        method: 'PUT', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ departures: draft }),
      });
      const data = await res.json().catch(() => ({}));
      if (!res.ok) throw new Error(typeof data.detail === 'string' ? data.detail : res.status);
      const on = data.departures.filter((d) => d.enabled).map((d) => d.time);
      showToast(on.length ? t('Salida programada a las {p0}.', {p0: on.join(', ')}) : t('Salida programada desactivada.'), 'success');
      take(data);
      setTimeout(load, CONFIRM_AFTER_MS);             // what the car itself says once it has taken the order
    } catch (e) {
      showToast(t('No se pudo guardar la salida programada: {p0}', {p0: e.message || e}), 'error', 7000);
    } finally {
      saving = false;
      render();
    }
  }

  window.Departure = { load, save, setTime, setEnabled };
})();
