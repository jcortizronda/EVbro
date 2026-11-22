/* EVBro — Scheduled battery preheating (Confort): «Reserva tu viaje» of the official app. The car warms the battery before
 * the time you leave. The plan lives in the car: it is read from what the car already reports (so «Activado» / «Desactivado» is
 * what the car says, not a local flag) and changed with one order, sent by the server with the saved PIN (like the charge
 * schedule). One schedule, like the official app: a time, its days (1 = Monday … 7 = Sunday, services/departure.py) and its
 * switch. The server answers with a list of one.
 * (Classic script: shares the global scope with the other files; see the load order in templates/index.html.)
 */
(function () {
  const el = (id) => document.getElementById(id);
  const CONFIRM_AFTER_MS = 12000;      // the car takes a few seconds to report the plan back
  let plan = null;                     // { known, enabled, departures: [{time, days, enabled}] } as the car has it
  let draft = [];                      // what the form shows: [{time, days, enabled}]
  const DAY_NAMES = ['L', 'M', 'X', 'J', 'V', 'S', 'D'];              // chip i is weekday i + 1
  const DAY_LONG = [t('lunes'), t('martes'), t('miércoles'), t('jueves'), t('viernes'), t('sábado'), t('domingo')];
  let saving = false;

  const shape = (d) => ({ time: d.time, days: [...(d.days || [])].sort(), enabled: !!d.enabled });
  const dirty = () => !!plan && JSON.stringify(draft) !== JSON.stringify(plan.departures.map(shape));

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
    draft = plan.departures.map(shape);
    render();
  }

  function render() {
    const list = el('departureList');
    if (!list) return;
    const pinHint = el('departurePinHint');
    if (pinHint) pinHint.hidden = !(typeof currentAccountInfo !== 'undefined' && currentAccountInfo && currentAccountInfo.has_pin === false);
    if (!plan || !plan.known) {
      list.innerHTML = `<p class="comfort-hint">${plan ? t('Este coche todavía no ha informado de su precalentamiento programado.') : t('No se pudo leer el precalentamiento programado.')}</p>`;
      el('departureSave').disabled = true;
      return;
    }
    list.innerHTML = draft.map((d, i) => `
      <div class="csched-row dep-row ${d.enabled ? '' : 'is-off'}">
        <div class="csched-main">
          <label class="csched-field dep-time"><span>${t('Hora de salida')}</span><input type="time" class="form-control" value="${escapeHtml(d.time)}" step="300" data-on-change="Departure.setTime(${i}, this.value)"></label>
        </div>
        <label class="csched-switch" title="${d.enabled ? t('Activado') : t('Desactivado')}">
          <input type="checkbox" ${d.enabled ? 'checked' : ''} data-on-change="Departure.setEnabled(${i}, this.checked)">
          <span>${d.enabled ? t('Activado') : t('Desactivado')}</span>
        </label>
        <div class="csched-days dep-days" role="group" aria-label="${t('Días de la semana')}">${DAY_NAMES.map((n, k) => {
          const on = d.days.includes(k + 1);
          return `<button type="button" class="csched-day ${on ? 'is-on' : ''}" aria-pressed="${on}" title="${DAY_LONG[k]}" data-on-click="Departure.toggleDay(${i}, ${k + 1})">${n}</button>`;
        }).join('')}</div>
      </div>`).join('');
    el('departureSave').disabled = saving || !dirty();
  }

  function setTime(i, value) { if (draft[i] && value) { draft[i].time = value; render(); } }
  function setEnabled(i, on) { if (draft[i]) { draft[i].enabled = !!on; render(); } }
  /** A departure keeps at least one day: the last one cannot be taken away (to stop it, turn it off). */
  function toggleDay(i, day) {
    const d = draft[i];
    if (!d) return;
    if (!d.days.includes(day)) d.days = [...d.days, day].sort();
    else if (d.days.length > 1) d.days = d.days.filter((x) => x !== day);
    else showToast(t('Deja al menos un día; para pararlo, desactívalo.'), 'info');
    render();
  }

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
      showToast(on.length ? t('Precalentamiento de batería programado a las {p0}.', {p0: on.join(', ')}) : t('Precalentamiento de batería desactivado.'), 'success');
      take(data);
      setTimeout(load, CONFIRM_AFTER_MS);             // what the car itself says once it has taken the order
    } catch (e) {
      showToast(t('No se pudo guardar el precalentamiento programado: {p0}', {p0: e.message || e}), 'error', 7000);
    } finally {
      saving = false;
      render();
    }
  }

  window.Departure = { load, save, setTime, setEnabled, toggleDay };
})();
