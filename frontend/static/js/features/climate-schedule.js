/* EVBro — Scheduled climate (Confort): at a set time on the chosen days, EVBro turns the climate control on at the
 * given temperature. The server sends it with the saved PIN (like the charge manager): the car wakes up.
 * (Classic script: shares the global scope with the other files; see the load order in templates/index.html.)
 */
(function () {
  const DAY_NAMES = ['L', 'M', 'X', 'J', 'V', 'S', 'D'];
  const DAY_LONG = [t('lunes'), t('martes'), t('miércoles'), t('jueves'), t('viernes'), t('sábado'), t('domingo')];
  let schedules = [];
  let formDays = [0, 1, 2, 3, 4];
  const el = (id) => document.getElementById(id);

  function daysText(days) {
    const d = [...days].sort();
    if (d.length === 7) return 'todos los días';
    if (d.join() === '0,1,2,3,4') return 'de lunes a viernes';
    if (d.join() === '5,6') return 'fines de semana';
    return d.map((i) => DAY_LONG[i]).join(', ');
  }

  async function load() {
    if (typeof currentVin === 'undefined' || !currentVin || !el('climateSchedulePanel')) return;
    try {
      const res = await fetch(`/api/vehicles/${currentVin}/climate-schedules`);
      if (!res.ok) throw new Error(res.status);
      schedules = await res.json();
      render();
    } catch (e) {
      console.warn('Could not load the climate schedule:', e);
    }
  }

  function render() {
    const list = el('climateScheduleList');
    if (!list) return;
    // Only worth saying when it is missing: without a saved PIN the server cannot send the order
    const pinHint = el('climateSchedulePinHint');
    if (pinHint) pinHint.hidden = !(typeof currentAccountInfo !== 'undefined' && currentAccountInfo && currentAccountInfo.has_pin === false);
    if (!schedules.length) {
      list.innerHTML = '<p class="comfort-hint">Sin programaciones.</p>';
      return;
    }
    list.innerHTML = schedules.map((s) => `
      <div class="csched-row ${s.enabled ? '' : 'is-off'}">
        <div class="csched-main">
          <strong>${escapeHtml(s.time_local)} · ${s.temperature.toLocaleString(I18n.locale)} °C</strong>
          <small>${escapeHtml(daysText(s.days))}${s.label ? ' · ' + escapeHtml(s.label) : ''}</small>
          ${s.last_result ? `<small class="csched-last">Última vez (${escapeHtml(s.last_run_on || '')}): ${escapeHtml(s.last_result)}</small>` : ''}
        </div>
        <label class="csched-switch" title="${s.enabled ? 'Activa' : 'En pausa'}">
          <input type="checkbox" ${s.enabled ? 'checked' : ''} data-on-change="ClimateSchedule.toggle(${s.id}, this.checked)">
          <span>${s.enabled ? 'Activa' : 'En pausa'}</span>
        </label>
        <button type="button" class="btn-secondary btn-sm is-danger" data-on-click="ClimateSchedule.remove(${s.id})" aria-label="Borrar programación">Borrar</button>
      </div>`).join('');
  }

  function renderDays() {
    const box = el('climateScheduleDays');
    if (!box) return;
    box.innerHTML = DAY_NAMES.map((n, i) => `<button type="button" class="csched-day ${formDays.includes(i) ? 'is-on' : ''}" aria-pressed="${formDays.includes(i)}" data-on-click="ClimateSchedule.toggleDay(${i})" title="${DAY_LONG[i]}">${n}</button>`).join('');
  }

  function toggleDay(i) {
    formDays = formDays.includes(i) ? formDays.filter((d) => d !== i) : [...formDays, i];
    renderDays();
  }

  async function add() {
    const time = el('climateScheduleTime').value;
    const temperature = parseFloat(el('climateScheduleTemp').value);
    if (!time) { showToast('Indica la hora.', 'warning'); return; }
    if (!formDays.length) { showToast('Elige al menos un día.', 'warning'); return; }
    try {
      const res = await fetch(`/api/vehicles/${currentVin}/climate-schedules`, {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ time_local: time, days: formDays, temperature, label: el('climateScheduleLabel').value.trim() || null }),
      });
      const data = await res.json().catch(() => ({}));
      if (!res.ok) throw new Error(Array.isArray(data.detail) ? data.detail.map((d) => d.msg).join(' · ') : (data.detail || res.status));
      showToast(t('Programado: {p0}, {p1}.', {p0: time, p1: daysText(formDays)}), 'success');
      el('climateScheduleLabel').value = '';
      load();
    } catch (e) {
      showToast(t('No se pudo programar: {p0}', {p0: e.message || e}), 'error', 6000);
    }
  }

  async function toggle(id, enabled) {
    const res = await fetch(`/api/climate-schedules/${id}`, { method: 'PUT', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ enabled }) });
    if (!res.ok) showToast('No se pudo cambiar la programación.', 'error');
    load();
  }

  async function remove(id) {
    if (!await confirmDialog('¿Borrar esta programación?', { confirmText: 'Borrar', danger: true })) return;
    const res = await fetch(`/api/climate-schedules/${id}`, { method: 'DELETE' });
    if (res.ok) showToast('Programación borrada', 'success');
    load();
  }

  function updateTempLabel() {
    const t = el('climateScheduleTemp');
    if (t && el('climateScheduleTempOut')) el('climateScheduleTempOut').textContent = `${Number(t.value).toLocaleString(I18n.locale)} °C`;
  }

  document.addEventListener('DOMContentLoaded', () => {
    renderDays();
    updateTempLabel();
  });

  window.ClimateSchedule = { load, add, toggle, remove, toggleDay, updateTempLabel };
})();
