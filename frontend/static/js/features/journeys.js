/* EVBro — «Viajes largos» (Más → Viajes): a named span of days (a holiday, a road trip) that adds up every drive, charge,
 * refuel and expense recorded between its dates. The server does the sums (services/journeys.py); here the list, the form with a
 * live preview, and the detail sheet. (Classic script: shares the global scope; feedback.js and history-list.js load before it.)
 */
window.Journeys = (function () {
  'use strict';

  const el = (id) => document.getElementById(id);
  const money = (n) => `${(n || 0).toLocaleString(I18n.locale, { minimumFractionDigits: 2, maximumFractionDigits: 2 })} €`;
  const num = (n, d = 1) => (n === null || n === undefined) ? '—' : n.toLocaleString(I18n.locale, { minimumFractionDigits: d, maximumFractionDigits: d });
  const dayText = (iso) => serverDate(iso).toLocaleDateString(I18n.locale, { day: 'numeric', month: 'short', year: 'numeric' });
  const toLocalInput = (date) => { const p = (n) => String(n).padStart(2, '0'); return `${date.getFullYear()}-${p(date.getMonth() + 1)}-${p(date.getDate())}T${p(date.getHours())}:${p(date.getMinutes())}`; };
  const show = (id) => el(id).classList.add('open');
  const hide = (id) => el(id).classList.remove('open');
  let items = [];
  let editing = null;
  let previewTimer = null;

  async function load() {
    if (!currentVin || !el('jnList')) return;
    try {
      const res = await fetch(`/api/vehicles/${currentVin}/journeys`);
      if (!res.ok) throw new Error(res.status);
      items = await res.json();
    } catch (e) { items = []; console.warn('journeys:', e); }
    render();
  }

  function render() {
    const list = el('jnList');
    if (!items.length) {
      list.innerHTML = '<div class="hist-empty">Marca el inicio y el final de unas vacaciones o una escapada y verás cuánto costó y consumió todo el viaje.</div>';
      return;
    }
    list.innerHTML = items.map((j) => {
      const s = j.summary;
      return `<button type="button" class="jn-row" data-on-click="Journeys.open(${j.id})">
        <div class="jn-row-main"><strong>${escapeHtml(j.name)}</strong>
          <small>${dayText(j.start_time)} – ${dayText(j.end_time)} · ${j.days} ${j.days === 1 ? t('día') : t('días')}</small></div>
        <div class="jn-row-side"><b>${money(s.totals.cost)}</b><small>${t('{p0} km · {p1} trayectos', {p0: num(s.drives.km, 0), p1: s.drives.count})}</small></div>
      </button>`;
    }).join('');
  }

  // ───────────── form (new / edit) ─────────────
  function openForm(journey) {
    editing = journey || null;
    el('jnFormTitle').textContent = journey ? 'Editar viaje' : 'Nuevo viaje';
    el('jnName').value = journey ? journey.name : '';
    const now = new Date();
    const weekAgo = new Date(now.getTime() - 7 * 86400000);
    el('jnStart').value = journey ? toLocalInput(serverDate(journey.start_time)) : toLocalInput(new Date(weekAgo.setHours(0, 0, 0, 0)));
    el('jnEnd').value = journey ? toLocalInput(serverDate(journey.end_time)) : toLocalInput(new Date(now.setHours(23, 59, 0, 0)));
    el('jnNotes').value = journey ? (journey.notes || '') : '';
    el('jnPreview').innerHTML = '';
    el('jnDelete').hidden = !journey;
    show('journeyModal');
    schedulePreview();
  }

  function schedulePreview() {
    clearTimeout(previewTimer);
    previewTimer = setTimeout(preview, 350);
  }

  function formBody() {
    return { name: el('jnName').value.trim() || 'Viaje', start_time: el('jnStart').value, end_time: el('jnEnd').value, notes: el('jnNotes').value.trim() || null };
  }

  async function preview() {
    const body = formBody();
    const box = el('jnPreview');
    if (!body.start_time || !body.end_time) { box.innerHTML = ''; return; }
    try {
      const res = await fetch(`/api/vehicles/${currentVin}/journeys/preview`, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) });
      const data = await res.json().catch(() => ({}));
      if (!res.ok) { box.innerHTML = `<div class="jn-preview-bad">${escapeHtml(data.detail || 'Fechas no válidas.')}</div>`; return; }
      const s = data.summary;
      box.innerHTML = `<div class="jn-preview">Entre esas fechas hay <b>${s.drives.count}</b> ${t('trayectos ({p0} km),', {p0: num(s.drives.km, 0)})} <b>${s.charges.count}</b> cargas,
        <b>${s.refuels.count}</b> repostajes y <b>${s.expenses.count}</b> gastos: <b>${money(s.totals.cost)}</b> en total.</div>`;
    } catch (e) { box.innerHTML = ''; }
  }

  async function save() {
    const body = formBody();
    if (!body.start_time || !body.end_time) { showToast('Indica el inicio y el final.', 'warning'); return; }
    const url = editing ? `/api/vehicles/${currentVin}/journeys/${editing.id}` : `/api/vehicles/${currentVin}/journeys`;
    try {
      const res = await fetch(url, { method: editing ? 'PUT' : 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) });
      const data = await res.json().catch(() => ({}));
      if (!res.ok) throw new Error(Array.isArray(data.detail) ? data.detail.map((d) => d.msg).join(' · ') : (data.detail || res.status));
      hide('journeyModal');
      showToast(editing ? 'Viaje actualizado.' : 'Viaje guardado.', 'success');
      await load();
      open(data.id);
    } catch (e) {
      showToast(t('No se pudo guardar: {p0}', {p0: e.message || e}), 'error', 6000);
    }
  }

  async function remove() {
    if (!editing) return;
    const sure = await confirmDialog(t('¿Borrar el viaje «{p0}»? Los trayectos, cargas y gastos que suma se quedan donde están.', {p0: editing.name}),
      { title: 'Borrar viaje', confirmText: 'Borrar', danger: true });
    if (!sure) return;
    try {
      const res = await fetch(`/api/vehicles/${currentVin}/journeys/${editing.id}`, { method: 'DELETE' });
      if (!res.ok) throw new Error(res.status);
      hide('journeyModal'); hide('journeyDetailModal');
      showToast('Viaje borrado.', 'success');
      await load();
    } catch (e) { showToast('No se pudo borrar.', 'error'); }
  }

  // ───────────── detail ─────────────
  async function open(id) {
    try {
      const res = await fetch(`/api/vehicles/${currentVin}/journeys/${id}`);
      if (!res.ok) throw new Error(res.status);
      renderDetail(await res.json());
      show('journeyDetailModal');
    } catch (e) { showToast('No se pudo abrir el viaje.', 'error'); }
  }

  function renderDetail(j) {
    const s = j.summary;
    const d = s.drives;
    el('jnDetailTitle').textContent = j.name;
    el('jnDetailDates').textContent = `${dayText(j.start_time)} – ${dayText(j.end_time)} · ${j.days} ${j.days === 1 ? t('día') : t('días')}${d.first_address ? ` · ${d.first_address} → ${d.last_address}` : ''}`;
    const kpi = (label, value, sub) => `<div class="jn-kpi"><small>${label}</small><strong>${value}</strong>${sub ? `<span>${sub}</span>` : ''}</div>`;
    const cat = (k) => (typeof expenseCategories !== 'undefined' && expenseCategories.find((c) => c.key === k)?.label) || k;
    const unpriced = (s.charges.unpriced || 0) + (s.refuels.unpriced || 0);
    el('jnDetailBody').innerHTML = `
      <div class="jn-hero"><small>Coste total del viaje</small><strong>${money(s.totals.cost)}</strong>
        <span>${s.totals.cost_per_km !== null ? `${num(s.totals.cost_per_km * 100, 2)} € cada 100 km · ` : ''}${money(s.totals.cost_per_day)} al día${unpriced ? ` · ${unpriced} sin precio` : ''}</span></div>
      <div class="jn-kpis">
        ${kpi('Kilómetros', `${num(d.km, 0)} km`, `${d.count} trayectos · ${num(d.hours)} h al volante`)}
        ${kpi('Cargas', money(s.charges.cost), `${s.charges.count} cargas · ${num(s.charges.kwh)} kWh${s.charges.dc_count ? ` · ${s.charges.dc_count} rápidas` : ''}`)}
        ${kpi('Gasolina', money(s.refuels.cost), `${s.refuels.count} repostajes · ${num(s.refuels.liters)} l`)}
        ${kpi('Gastos', money(s.expenses.total), Object.keys(s.expenses.by_category).map((k) => `${cat(k)} ${money(s.expenses.by_category[k])}`).join(' · ') || 'Peajes, parking, multas…')}
        ${kpi('Consumo', `${d.kwh_per_100km !== null ? `${num(d.kwh_per_100km)} kWh` : '—'} · ${d.l_per_100km !== null ? `${num(d.l_per_100km)} l` : '—'}`, 'por 100 km, eléctrico · gasolina')}
        ${kpi('Taller', money(s.maintenance.cost), `${s.maintenance.count} intervenciones`)}
      </div>
      ${s.per_day.length ? `<div class="jn-days">${s.per_day.map((p) => `<div class="jn-day"><small>${serverDate(p.day + 'T12:00:00').toLocaleDateString(I18n.locale, { weekday: 'short', day: 'numeric', month: 'short' })}</small>
        <div class="jn-day-bar"><i style="width:${Math.max(4, Math.round(p.km / Math.max(...s.per_day.map((q) => q.km || 0), 1) * 100))}%"></i></div><b>${num(p.km, 0)} km</b></div>`).join('')}</div>` : ''}
      ${j.notes ? `<p class="jn-notes">${escapeHtml(j.notes)}</p>` : ''}
      <details class="jn-items"><summary>Lo que suma (${s.items.drives.length + s.items.charges.length + s.items.refuels.length + s.items.expenses.length})</summary>
        ${s.items.drives.map((x) => `<button type="button" class="jn-item" data-on-click="openRouteModal(${x.id})"><span>${dayText(x.start_time)} · ${escapeHtml(x.from || '')} → ${escapeHtml(x.to || '')}</span><b>${num(x.km, 0)} km</b></button>`).join('')}
        ${s.items.charges.map((x) => `<div class="jn-item"><span>${dayText(x.start_time)} · Carga${x.type === 'dc' ? ' rápida' : ''} ${escapeHtml(x.address || '')}</span><b>${x.cost === null ? 'sin precio' : money(x.cost)}</b></div>`).join('')}
        ${s.items.refuels.map((x) => `<div class="jn-item"><span>${dayText(x.timestamp)} · Repostaje ${escapeHtml(x.station || '')}</span><b>${x.cost === null ? 'sin precio' : money(x.cost)}</b></div>`).join('')}
        ${s.items.expenses.map((x) => `<div class="jn-item"><span>${dayText(x.date)} · ${escapeHtml(x.title)}</span><b>${money(x.amount)}</b></div>`).join('')}
      </details>`;
    el('jnDetailEdit').onclick = () => { hide('journeyDetailModal'); openForm(j); };
  }

  return { load, openForm, schedulePreview, save, remove, open, close: hide };
})();
