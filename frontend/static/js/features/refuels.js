/* EVBro — Refuels (Más → Repostajes): real fuel consumption between fill-ups, cost per 100 km, price per litre,
 * km between refuels, monthly totals and the list of refuels (detected or manual) with its form.
 * The overall money balance stays in Costes; here only fuel.
 * (Classic script: shares the global scope with the other files; see the load order in templates/index.html.)
 */

let refuelsData = [];
let refuelsPicker = null;
const refuelsPickerOf = () => refuelsPicker || (refuelsPicker = PeriodPicker.create('refuelsPeriod', { storageKey: 'evbro_period_refuels', presets: ['all', 'year', 'last_year', '12m', 'month'], onChange: loadRefuels }));

const rfEl = (id) => document.getElementById(id);
const rfMoney = (n) => `${(n || 0).toLocaleString(I18n.locale, { minimumFractionDigits: 2, maximumFractionDigits: 2 })} €`;
const rfNum = (n, digits = 1) => (n === null || n === undefined) ? '--' : n.toLocaleString(I18n.locale, { minimumFractionDigits: digits, maximumFractionDigits: digits });
const rfDay = (iso) => serverDate(iso).toLocaleDateString(I18n.locale);

async function loadRefuels() {
  if (!currentVin || !rfEl('tabRefuels')) return;
  try {
    const res = await fetch(`/api/vehicles/${currentVin}/refuels/summary?${refuelsPickerOf().query()}`);
    if (!res.ok) throw new Error(res.status);
    const data = await res.json();
    refuelsData = data.refuels;
    renderRefuels(data);
  } catch (err) {
    console.warn('Could not load the refuels:', err);
  }
}

function setRefuelsPeriod(period) {
  refuelsPickerOf().set({ period });                                  // the picker calls loadRefuels()
}

function renderRefuels(d) {
  const real = d.l_per_100km !== null;
  const cons = real ? d.l_per_100km : d.gauge_l_per_100km;
  rfEl('rfHeroValue').textContent = cons === null ? '--' : `${rfNum(cons)} l/100 km`;
  rfEl('rfHeroLabel').textContent = t('Consumo de gasolina · {p0}', {p0: refuelsPickerOf().isEverything() ? 'desde el principio' : refuelsPickerOf().label().toLowerCase()});
  rfEl('rfHeroSub').textContent = real
    ? t('Medido entre {p0} {p1} completos: litros echados ÷ km recorridos (eléctricos incluidos).', {p0: d.l_per_100km_segments, p1: d.l_per_100km_segments === 1 ? 'llenado' : 'llenados'})
    : cons !== null
      ? 'Estimado por lo que bajó el indicador del depósito. Marca «Depósito lleno» en tus repostajes para medirlo con los litros reales.'
      : 'Hacen falta dos repostajes con el depósito lleno y el cuentakilómetros para medir el consumo real.';
  rfEl('rfCostPer100').textContent = d.fuel_cost_per_100km === null ? '--' : rfMoney(d.fuel_cost_per_100km);
  rfEl('rfPriceLiter').textContent = d.avg_price_per_liter === null ? '--' : `${rfNum(d.avg_price_per_liter, 3)} €/l`;
  rfEl('rfRange').textContent = d.full_tank_range_km === null ? '--' : `${rfNum(d.full_tank_range_km, 0)} km`;
  rfEl('rfKmBetween').textContent = d.avg_km_between_refuels === null ? '--' : `${rfNum(d.avg_km_between_refuels, 0)} km`;
  rfEl('rfTotalLiters').textContent = `${rfNum(d.total_liters)} l`;
  rfEl('rfTotalCost').textContent = rfMoney(d.total_cost);
  rfEl('rfCount').textContent = `${d.count} ${d.count === 1 ? 'repostaje' : 'repostajes'}${d.unpriced ? ` · ${d.unpriced} sin precio` : ''}`;
  renderRefuelBars(d.months);
  renderRefuelList();
}

function renderRefuelBars(months) {
  const box = rfEl('rfBars');
  const data = (months || []).slice(0, 12).reverse();
  if (!data.length) { box.innerHTML = '<div class="costs-empty">Sin repostajes en este periodo.</div>'; return; }
  const max = Math.max(...data.map((m) => m.liters), 1);
  box.innerHTML = data.map((m) => {
    const [y, mo] = m.month.split('-');
    const label = new Date(Number(y), Number(mo) - 1, 1).toLocaleDateString(I18n.locale, { month: 'short' });
    return `<div class="costs-bar" title="${m.month}: ${rfNum(m.liters)} l · ${rfMoney(m.cost)}">
      <span class="costs-bar-total">${rfNum(m.liters, 0)} l</span>
      <div class="costs-bar-stack rf-bar-stack"><i class="c-refuels" data-css="height:${Math.round(m.liters / max * 100)}%"></i></div>
      <span class="costs-bar-label">${label}</span></div>`;
  }).join('');
  applyDataStyles(box);
}

function renderRefuelList() {
  const box = rfEl('rfList');
  if (!refuelsData.length) { box.innerHTML = '<div class="costs-empty">Aún no hay repostajes. Se detectan solos cuando sube el depósito, o puedes añadir uno.</div>'; return; }
  box.innerHTML = refuelsData.map((r) => {
    const meta = [];
    if (r.km_since_previous) meta.push(t('{p0} km desde el anterior', {p0: rfNum(r.km_since_previous, 0)}));
    if (r.l_per_100km !== null && r.l_per_100km !== undefined) meta.push(`<strong class="rf-cons">${rfNum(r.l_per_100km)} l/100 km</strong>`);
    if (r.price_per_liter) meta.push(`${rfNum(r.price_per_liter, 3)} €/l`);
    const where = escapeHtml(r.station || r.address || (r.detected ? 'Detectado por el coche' : 'Añadido a mano'));
    return `
    <button type="button" class="costs-row costs-row-btn rf-row" data-on-click="openRefuelModal(${r.id})">
      <div class="costs-row-main">
        <strong>${rfDay(r.timestamp)} · ${rfNum(r.liters)} l${r.liters_estimated ? ' <span class="rf-tag">estimados</span>' : ''}${r.full_tank ? ' <span class="rf-tag rf-tag-full">lleno</span>' : ''}</strong>
        <small>${where}${meta.length ? ' · ' + meta.join(' · ') : ''}</small>
      </div>
      <span class="costs-row-amount ${r.needs_price ? 'is-pending' : ''}">${r.needs_price ? 'Añadir precio' : rfMoney(r.total_cost)}</span>
    </button>`;
  }).join('');
}

// ───────────────────────── Form ─────────────────────────
function setRefuelMode(mode) {
  rfEl('rfPrice').hidden = mode !== 'price';
  rfEl('rfTotal').hidden = mode !== 'total';
}

function openRefuelModal(id) {
  const r = id ? refuelsData.find((x) => x.id === id) : null;
  rfEl('rfId').value = r ? r.id : '';
  rfEl('refuelTitle').textContent = r ? 'Editar repostaje' : 'Añadir repostaje';
  rfEl('rfDate').value = (r ? r.timestamp : new Date().toISOString()).slice(0, 10);
  rfEl('rfLiters').value = r ? r.liters ?? '' : '';
  rfEl('rfOdometer').value = r && r.odometer ? Math.round(r.odometer) : '';
  rfEl('rfFull').checked = r ? !!r.full_tank : true;
  rfEl('rfPrice').value = r && r.price_per_liter ? r.price_per_liter : '';
  rfEl('rfTotal').value = '';
  rfEl('rfStation').value = r ? r.station || '' : '';
  rfEl('rfNotes').value = r ? r.notes || '' : '';
  document.querySelector('input[name="rfMode"][value="price"]').checked = true;
  setRefuelMode('price');
  rfEl('rfDelete').hidden = !r;
  const note = rfEl('rfEstimateNote');
  note.hidden = !(r && r.liters_estimated);
  if (r && r.liters_estimated) note.textContent = t('El depósito pasó del {p0} % al {p1} %: los litros son una estimación. Pon los del ticket para que el precio por litro y el consumo sean exactos.', {p0: Math.round(r.fuel_before_pct), p1: Math.round(r.fuel_after_pct)});
  rfEl('refuelModal').classList.add('open');
}
function closeRefuelModal() { rfEl('refuelModal').classList.remove('open'); }

async function saveRefuel() {
  const num = (id) => { const v = parseFloat(rfEl(id).value.replace(',', '.')); return isNaN(v) ? null : v; };
  const id = rfEl('rfId').value;
  const liters = num('rfLiters');
  if (!id && (!liters || liters <= 0)) { showToast('Indica los litros.', 'warning'); return; }
  const mode = document.querySelector('input[name="rfMode"]:checked').value;
  const body = { station: rfEl('rfStation').value.trim(), notes: rfEl('rfNotes').value.trim() || null, full_tank: rfEl('rfFull').checked };
  if (liters) body.liters = liters;
  if (num('rfOdometer') !== null) body.odometer = num('rfOdometer');
  if (mode === 'price' && num('rfPrice') !== null) body.price_per_liter = num('rfPrice');
  if (mode === 'total' && num('rfTotal') !== null) body.total_cost = num('rfTotal');
  if (rfEl('rfDate').value) body.timestamp = `${rfEl('rfDate').value}T12:00:00`;
  try {
    const res = await fetch(id ? `/api/refuels/${id}` : `/api/vehicles/${currentVin}/refuels`, {
      method: id ? 'PUT' : 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) });
    const data = await res.json().catch(() => ({}));
    if (!res.ok) throw new Error(Array.isArray(data.detail) ? data.detail.map((d) => d.msg).join(' · ') : (data.detail || res.status));
    closeRefuelModal();
    showToast('Repostaje guardado', 'success');
    loadRefuels();
  } catch (e) {
    showToast(t('No se pudo guardar: {p0}', {p0: e.message || e}), 'error', 6000);
  }
}

async function deleteRefuel() {
  const id = rfEl('rfId').value;
  if (!id || !await confirmDialog('¿Borrar este repostaje?', { confirmText: 'Borrar', danger: true })) return;
  const res = await fetch(`/api/refuels/${id}`, { method: 'DELETE' });
  if (res.ok) { closeRefuelModal(); showToast('Repostaje borrado', 'success'); loadRefuels(); }
}

document.addEventListener('DOMContentLoaded', () => {
  document.querySelectorAll('input[name="rfMode"]').forEach((r) => r.addEventListener('change', () => setRefuelMode(r.value)));
});
