/* EVbro — Refuels (Más → Repostajes): real fuel consumption between fill-ups, cost per 100 km, price per litre,
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
  if (window.FuelStations) FuelStations.load();          // the stations around the car, at the top of the page (features/fuel-stations.js)
  try {
    const res = await fetch(`/api/vehicles/${currentVin}/refuels/summary?${refuelsPickerOf().query()}`);
    if (!res.ok) throw new Error(res.status);
    const data = await res.json();
    refuelsData = data.refuels;
    fillStationList(data.stations || []);
    renderRefuels(data);
  } catch (err) {
    console.warn('Could not load the refuels:', err);
  }
}

// The stations already used, most used first: the «Gasolinera» field suggests them as you type
function fillStationList(stations) {
  const list = rfEl('rfStationList');
  if (!list) return;
  list.replaceChildren(...stations.map((s) => Object.assign(document.createElement('option'), { value: s.name })));
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
  const days = d.avg_days_between_refuels;          // average of the gaps between one refuel and the next (it replaced the full-tank range)
  rfEl('rfDaysBetween').textContent = days === null ? '--' : t('{p0} días', {p0: rfNum(days, days < 10 ? 1 : 0)});
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
        <strong>${rfDay(r.timestamp)} · ${rfNum(r.liters, 2)} l${r.liters_estimated ? ' <span class="rf-tag">estimados</span>' : ''}${r.full_tank ? ' <span class="rf-tag rf-tag-full">lleno</span>' : ''}</strong>
        <small>${where}${meta.length ? ' · ' + meta.join(' · ') : ''}</small>
      </div>
      <span class="costs-row-amount ${r.needs_price ? 'is-pending' : ''}">${r.needs_price ? 'Añadir precio' : rfMoney(r.total_cost)}</span>
    </button>`;
  }).join('');
}

// ───────────────────────── Form ─────────────────────────
// Litres, price per litre and total paid: any two give the third (as in «Añadir carga», features/charge-manual.js). `rfTyped` keeps
// the ones the person wrote, the most recent first; the third one is worked out and shown as such. Only the typed ones are sent.
const RF_TRIO = { liters: 'rfLiters', price: 'rfPrice', total: 'rfTotal' };
let rfTyped = [];
const rfNumOf = (id) => { const v = parseFloat(String(rfEl(id).value).replace(',', '.')); return Number.isFinite(v) ? v : null; };
const rfError = (text) => { const box = rfEl('rfError'); box.textContent = text || ''; box.hidden = !text; };

function rfEdited(key) {
  rfError('');
  rfTyped = rfTyped.filter((k) => k !== key);
  if (rfEl(RF_TRIO[key]).value.trim() !== '') rfTyped.unshift(key);
  rfRecompute();
}

function rfRecompute() {
  const [a, b] = rfTyped;
  const third = a && b ? Object.keys(RF_TRIO).find((k) => k !== a && k !== b) : null;
  Object.entries(RF_TRIO).forEach(([k, id]) => {
    const input = rfEl(id);
    const auto = k === third;
    if (!auto && !rfTyped.includes(k) && input.classList.contains('is-auto')) input.value = '';
    input.classList.toggle('is-auto', auto);
  });
  if (!third) return;
  const liters = rfNumOf('rfLiters'), price = rfNumOf('rfPrice'), total = rfNumOf('rfTotal');
  const round = (v, d) => String(Math.round(v * 10 ** d) / 10 ** d);
  let value = null;
  if (third === 'total' && liters !== null && price !== null) value = round(liters * price, 2);
  if (third === 'price' && liters && total !== null) value = round(total / liters, 3);
  if (third === 'liters' && price && total !== null) value = round(total / price, 2);
  rfEl(RF_TRIO[third]).value = value === null ? '' : value;
}

const rfPad = (n) => String(n).padStart(2, '0');
const rfLocalDay = (d) => `${d.getFullYear()}-${rfPad(d.getMonth() + 1)}-${rfPad(d.getDate())}`;

function openRefuelModal(id) {
  const r = id ? refuelsData.find((x) => x.id === id) : null;
  rfEl('rfId').value = r ? r.id : '';
  rfEl('refuelTitle').textContent = r ? t('Editar repostaje') : t('Añadir repostaje');
  const when = r ? serverDate(r.timestamp) : new Date();
  rfEl('rfDate').value = rfLocalDay(when);
  rfEl('rfDate').max = rfLocalDay(new Date());
  // a refuel added without an hour is kept at midday (local, or UTC for the old ones): the field stays empty for it
  const noHour = r && !r.detected && ((when.getHours() === 12 && when.getMinutes() === 0) || /T12:00:00$/.test(r.timestamp));
  rfEl('rfHour').value = r && !noHour ? `${rfPad(when.getHours())}:${rfPad(when.getMinutes())}` : '';
  // what the refuel has comes back as typed: the litres and the total (the price follows), or the litres alone
  rfEl('rfLiters').value = r && r.liters !== null && r.liters !== undefined ? r.liters : '';
  rfEl('rfTotal').value = r && r.total_cost !== null && r.total_cost !== undefined ? r.total_cost : '';
  rfEl('rfPrice').value = '';
  rfTyped = ['total', 'liters'].filter((k) => rfEl(RF_TRIO[k]).value !== '');
  rfEl('rfOdometer').value = r && r.odometer ? Math.round(r.odometer) : '';
  rfEl('rfFull').checked = r ? !!r.full_tank : true;
  // A refuel without a name at the spot of an earlier one gets that station's name (the hint says so)
  const suggested = r && !r.station && r.station_suggestion;
  rfEl('rfStation').value = r ? r.station || suggested || '' : '';
  rfEl('rfStationHint').hidden = !suggested;
  rfEl('rfNotes').value = r ? r.notes || '' : '';
  rfRecompute();
  rfEl('rfDelete').hidden = !r;
  rfError('');
  const note = rfEl('rfEstimateNote');
  note.hidden = !(r && r.liters_estimated);
  if (r && r.liters_estimated) note.textContent = t('El depósito pasó del {p0} % al {p1} %: los litros son una estimación. Pon los del ticket para que el precio por litro y el consumo sean exactos.', {p0: Math.round(r.fuel_before_pct), p1: Math.round(r.fuel_after_pct)});
  rfEl('refuelModal').classList.add('open');
  rfEl('refuelModal').querySelector('.modal-card').scrollTop = 0;
}
function closeRefuelModal() { rfEl('refuelModal').classList.remove('open'); }

async function saveRefuel() {
  const id = rfEl('rfId').value;
  const sent = (k) => (rfTyped.includes(k) ? rfNumOf(RF_TRIO[k]) : null);      // the worked-out one is the server's to work out
  const liters = sent('liters'), price = sent('price'), total = sent('total');
  if (!id && !liters && !(price && total !== null)) {
    rfError(t('Indica los litros, o el total y el precio por litro.'));
    rfEl('rfLiters').focus();
    return;
  }
  if (!rfEl('rfDate').value) { rfError(t('Indica la fecha.')); rfEl('rfDate').focus(); return; }
  const body = { station: rfEl('rfStation').value.trim(), notes: rfEl('rfNotes').value.trim() || null, full_tank: rfEl('rfFull').checked };
  if (liters) body.liters = liters;
  if (price !== null) body.price_per_liter = price;
  if (total !== null) body.total_cost = total;
  if (rfNumOf('rfOdometer') !== null) body.odometer = rfNumOf('rfOdometer');
  // the local day and hour (midday when there is none), sent as UTC
  const [y, m, d] = rfEl('rfDate').value.split('-').map(Number);
  const [hh, mm] = rfEl('rfHour').value ? rfEl('rfHour').value.split(':').map(Number) : [12, 0];
  body.timestamp = new Date(y, m - 1, d, hh, mm).toISOString();
  const btn = rfEl('rfSave');
  btn.disabled = true;
  try {
    const res = await fetch(id ? `/api/refuels/${id}` : `/api/vehicles/${currentVin}/refuels`, {
      method: id ? 'PUT' : 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) });
    const data = await res.json().catch(() => ({}));
    if (!res.ok) throw new Error(Array.isArray(data.detail) ? data.detail.map((x) => x.msg).join(' · ') : (data.detail || res.status));
    closeRefuelModal();
    showToast(t('Repostaje guardado'), 'success');
    loadRefuels();
  } catch (e) {
    rfError(t('No se pudo guardar: {p0}', {p0: e.message || e}));
  } finally {
    btn.disabled = false;
  }
}

async function deleteRefuel() {
  const id = rfEl('rfId').value;
  if (!id || !await confirmDialog(t('¿Borrar este repostaje?'), { confirmText: t('Borrar'), danger: true })) return;
  const res = await fetch(`/api/refuels/${id}`, { method: 'DELETE' });
  if (res.ok) { closeRefuelModal(); showToast(t('Repostaje borrado'), 'success'); loadRefuels(); }
}

// ───────────────────────── From a spreadsheet ─────────────────────────
// The same three steps as «Importar cargas» (features/charge-manual.js); server side: services/refuel_import.py.
const RefuelImport = (function () {
  const FIELD_LABEL = { date: 'Fecha', time: 'Hora', liters: 'Litros', price: 'Precio por litro', total: 'Total pagado', odometer: 'Cuentakilómetros (km)', station: 'Gasolinera', full: 'Depósito lleno', notes: 'Notas' };
  const FIELD_ORDER = ['date', 'liters', 'time', 'total', 'price', 'odometer', 'full', 'station', 'notes'];
  const STATUS = { ok: ['ok', 'Listo'], duplicate: ['dup', 'Ya estaba'], error: ['bad', 'Revisar'] };
  // why a row is not brought in (the codes of services/refuel_import.py)
  const PROBLEM = {
    no_date: 'falta la fecha', bad_date: 'la fecha no se entiende', future: 'la fecha es futura', no_liters: 'faltan los litros (o el total y el precio por litro)',
    liters: 'los litros no son válidos', cost: 'el coste no es válido', odometer: 'el cuentakilómetros no es válido',
    already: 'ya hay un repostaje parecido ese día', repeated: 'está repetido en el fichero',
  };
  let file = null;
  let review = null;
  let lastBatch = null;
  const fail = async (res) => { const d = await res.json().catch(() => ({})); return new Error(typeof d.detail === 'string' ? d.detail : `HTTP ${res.status}`); };
  const error = (id, text) => { const box = rfEl(id); box.textContent = text || ''; box.hidden = !text; };

  function step(n) {
    [1, 2, 3].forEach((i) => {
      rfEl(`riStep${i}`).hidden = i !== n;
      const tab = rfEl(`riTab${i}`);
      tab.classList.toggle('is-on', i === n);
      tab.classList.toggle('is-done', i < n || n === 3);
    });
  }

  function open() {
    if (typeof currentVin === 'undefined' || !currentVin) return;
    file = null; review = null; lastBatch = null;
    rfEl('riFile').value = '';
    rfEl('riTemplate').href = `/api/vehicles/${currentVin}/refuels/import/template`;
    error('riError1', ''); error('riError2', '');
    step(1);
    rfEl('refuelImportModal').classList.add('open');
  }

  function close() {
    rfEl('refuelImportModal').classList.remove('open');
    if (lastBatch !== null) loadRefuels();
  }

  async function send(path, mapping) {
    const form = new FormData();
    form.append('file', file);
    if (mapping) form.append('mapping', JSON.stringify(mapping));
    const res = await fetch(`/api/vehicles/${currentVin}/refuels/import${path}`, { method: 'POST', body: form });
    if (!res.ok) throw await fail(res);
    return res.json();
  }

  /** A file was chosen (or a column was reassigned): ask the server how it understands it. Nothing is stored. */
  async function preview(mapping = null) {
    error('riError1', ''); error('riError2', '');
    try {
      review = await send('/preview', mapping);
    } catch (e) {
      error(review ? 'riError2' : 'riError1', e.message || String(e));
      return;
    }
    draw();
    step(2);
  }

  function fileChosen(input) {
    file = input.files && input.files[0] ? input.files[0] : null;
    review = null;
    if (file) preview();
  }

  function mappingNow() {
    const mapping = {};
    FIELD_ORDER.forEach((f) => { const v = rfEl(`riMap-${f}`).value; mapping[f] = v === '' ? null : Number(v); });
    return mapping;
  }

  const day = (iso) => (iso ? new Date(`${iso}T12:00:00`).toLocaleDateString(I18n.locale, { day: 'numeric', month: 'short', year: 'numeric' }) : t('Sin fecha'));
  const has = (v) => v !== null && v !== undefined;

  function draw() {
    rfEl('riFileName').textContent = t('{p0} · {p1} filas. Así ha entendido EVbro las columnas:', {p0: review.filename || file.name, p1: review.total});
    rfEl('riMap').innerHTML = FIELD_ORDER.map((f) => {
      const options = [`<option value="">${t('No está')}</option>`]
        .concat(review.columns.map((title, i) => `<option value="${i}" ${review.mapping[f] === i ? 'selected' : ''}>«${escapeHtml(title)}»</option>`));
      return `<div><label for="riMap-${f}">${t(FIELD_LABEL[f])}</label><select id="riMap-${f}" class="form-control" data-on-change="RefuelImport.remap()">${options.join('')}</select></div>`;
    }).join('');
    const c = review.counts;
    rfEl('riCounts').innerHTML = [
      `<span class="ci-chip is-ok">${t('Listos: {p0}', {p0: c.ok})}</span>`,
      c.duplicate ? `<span class="ci-chip is-dup">${t('Ya estaban: {p0}', {p0: c.duplicate})}</span>` : '',
      c.error ? `<span class="ci-chip is-bad">${t('Para revisar: {p0}', {p0: c.error})}</span>` : '',
    ].join('');
    rfEl('riRows').innerHTML = review.rows.map((r) => {
      const [cls, label] = STATUS[r.status];
      const main = r.day !== undefined
        ? `<strong>${escapeHtml(day(r.day))}</strong>${has(r.liters) ? ` · ${rfNum(r.liters, 2)} l` : ''}${r.full_tank ? ` <span class="rf-tag rf-tag-full">${t('lleno')}</span>` : ''}`
        : `<strong>${t('Fila {p0}', {p0: r.line})}</strong>`;
      const cost = has(r.total_cost) ? rfMoney(r.total_cost) : '';
      const price = has(r.price_per_liter) ? `${rfNum(r.price_per_liter, 3)} €/l` : '';
      const km = has(r.odometer) ? `${rfNum(r.odometer, 0)} km` : '';
      const rest = [cost, price, km, r.station, r.problem ? t(PROBLEM[r.problem] || 'no es un repostaje válido') : ''].filter(Boolean).map(escapeHtml).join(' · ');
      return `<div class="ci-row ${r.status === 'ok' ? '' : 'is-out'}"><span>${main}</span><span class="ci-chip is-${cls}">${t(label)}</span>${rest ? `<small>${rest}</small>` : ''}</div>`;
    }).join('');
    const more = review.total > review.rows.length ? t('Se enseñan {p0} de {p1}.', {p0: review.rows.length, p1: review.total}) + ' ' : '';
    rfEl('riNote').textContent = more + (c.duplicate || c.error ? t('Los que ya estaban y los que hay que revisar no se importan.') : '');
    const btn = rfEl('riImport');
    btn.disabled = !c.ok || review.missing.length > 0;
    btn.textContent = c.ok === 1 ? t('Importar 1 repostaje') : (c.ok ? t('Importar {p0} repostajes', {p0: c.ok}) : t('Nada que importar'));
    error('riError2', review.missing.length ? t('Indica qué columna tiene la fecha y cuál los litros (o el total y el precio por litro).') : '');
  }

  async function run() {
    const btn = rfEl('riImport');
    btn.disabled = true;
    try {
      const done = await send('', mappingNow());
      lastBatch = done.batch;
      rfEl('riDoneCount').textContent = done.imported;
      const span = done.from === done.to ? day(done.from) : t('del {p0} al {p1}', {p0: day(done.from), p1: day(done.to)});
      rfEl('riDoneText').textContent = [
        t('{p0} litros y {p1} más en tu historial, {p2}.', {p0: rfNum(done.liters), p1: rfMoney(done.cost), p2: span}),
        done.unpriced ? t('Sin precio: {p0}. Puedes añadírselo tocándolos en la lista.', {p0: done.unpriced}) : '',
      ].filter(Boolean).join(' ');
      step(3);
    } catch (e) {
      error('riError2', e.message || String(e));
      btn.disabled = false;
    }
  }

  async function undo() {
    if (lastBatch === null || !await confirmDialog(t('¿Deshacer la importación? Se borran los repostajes que acaba de traer.'), { confirmText: t('Deshacer'), danger: true })) return;
    try {
      const res = await fetch(`/api/vehicles/${currentVin}/refuels/import?batch=${encodeURIComponent(lastBatch)}`, { method: 'DELETE' });
      if (!res.ok) throw await fail(res);
      showToast(t('Importación deshecha.'), 'success');
      close();
    } catch (e) {
      showToast(t('No se pudo deshacer: {p0}', {p0: e.message || e}), 'error', 6000);
    }
  }

  return { open, close, pickFile: () => rfEl('riFile').click(), fileChosen, remap: () => preview(mappingNow()), back: () => step(1), run, undo };
})();
window.RefuelImport = RefuelImport;

document.addEventListener('DOMContentLoaded', () => {
  if (!rfEl('refuelModal')) return;
  Object.entries(RF_TRIO).forEach(([key, id]) => rfEl(id).addEventListener('input', () => rfEdited(key)));
});
