/* EVBro — Charges written down by the user (Carga → Historial de cargas): «Añadir carga» for one, «Importar» for a spreadsheet
 * (Excel or CSV). They are the charges from before EVBro, or one it missed: only the kWh are required, and they show in the
 * history with the «Manual» tag. Server side: services/manual_charges.py and services/charge_import.py.
 * (Classic script: shares the global scope with the other files; see the load order in templates/index.html.)
 */
(function () {
  const el = (id) => document.getElementById(id);
  const OTHER = '__other__';                      // «Otro sitio…» in the list of places
  let places = { geofences: [], places: [] };
  let editing = null;                             // id of the manual charge being corrected, or null for a new one

  const num = (id) => { const v = parseFloat(String(el(id).value).replace(',', '.')); return Number.isFinite(v) ? v : null; };
  const fail = async (res) => { const d = await res.json().catch(() => ({})); return new Error(typeof d.detail === 'string' ? d.detail : `HTTP ${res.status}`); };
  const error = (id, text) => { const box = el(id); box.textContent = text || ''; box.hidden = !text; };

  // ───────────────────────── one charge ─────────────────────────
  // kWh of the charger, price per kWh and total paid: any two give the third. `typed` keeps the ones the person wrote, the most
  // recent first; the third one (when two are typed) is worked out and shown as such. Only the typed ones are sent.
  const TRIO = { kwh: 'mcKwh', price: 'mcPrice', total: 'mcTotal' };
  let typed = [];
  const capacity = () => { const v = typeof currentVehicle === 'function' ? currentVehicle() : null; return v && v.battery_capacity_kwh ? Number(v.battery_capacity_kwh) : null; };
  const fmt = (v, d) => String(Math.round(v * 10 ** d) / 10 ** d);

  function fillPlaces(selected) {
    const select = el('mcPlace');
    const option = (value, text) => Object.assign(document.createElement('option'), { value, textContent: text });
    const group = (label, items) => { const g = document.createElement('optgroup'); g.label = label; g.append(...items); return g; };
    const tariff = (g) => (g.free ? t('gratis') : (g.price_per_kwh !== null ? `${localNum(g.price_per_kwh)} €/kWh` : ''));
    select.replaceChildren(option('', t('Sin indicar')));
    if (places.geofences.length) {
      select.append(group(t('Tus geocercas'), places.geofences.map((g) => option(`g:${g.id}`, tariff(g) ? `${g.name} · ${tariff(g)}` : g.name))));
    }
    if (places.places.length) select.append(group(t('Sitios que ya has escrito'), places.places.map((p) => option(`p:${p.name}`, p.name))));
    select.append(option(OTHER, t('Otro sitio…')));
    select.value = [...select.options].some((o) => o.value === selected) ? selected : '';
    placeChanged(false);
  }

  /** A geofence with a fixed or a free tariff fills in the price, if none was typed; «Otro sitio…» opens the text field. The
   *  geofence's price is a suggestion: it goes last, so a total typed afterwards reworks the price, not the kWh. */
  function placeChanged(fillPrice = true) {
    const value = el('mcPlace').value;
    el('mcPlaceText').hidden = value !== OTHER;
    if (!fillPrice || !value.startsWith('g:') || typed.includes('price')) return;
    const g = places.geofences.find((x) => `g:${x.id}` === value);
    const price = g ? (g.free ? 0 : g.price_per_kwh) : null;
    if (price === null || price === undefined) return;
    el('mcPrice').value = price;
    typed = typed.filter((k) => k !== 'price').concat('price');
    recompute();
  }

  /** The person wrote in one of the three: it becomes the most recent, and the third one is worked out again. */
  function edited(key) {
    typed = typed.filter((k) => k !== key);
    if (el(TRIO[key]).value.trim() !== '') typed.unshift(key);
    recompute();
  }

  function recompute() {
    const [a, b] = typed;
    const third = a && b ? Object.keys(TRIO).find((k) => k !== a && k !== b) : null;
    Object.entries(TRIO).forEach(([k, id]) => {
      const input = el(id);
      const auto = k === third;
      if (!auto && !typed.includes(k) && input.classList.contains('is-auto')) input.value = '';
      input.classList.toggle('is-auto', auto);
    });
    if (third) {
      const kwh = num('mcKwh'), price = num('mcPrice'), total = num('mcTotal');
      let value = null;
      if (third === 'total' && kwh !== null && price !== null) value = fmt(kwh * price, 2);
      if (third === 'price' && kwh && total !== null) value = fmt(total / kwh, 4);
      if (third === 'kwh' && price && total !== null) value = fmt(total / price, 2);
      el(TRIO[third]).value = value === null ? '' : value;
    }
    explain();
  }

  /** How the charge will be counted, from what is filled in: the charger's side or the battery's (services/manual_charges.py). */
  function explain() {
    const kwh = num('mcKwh'), price = num('mcPrice'), total = num('mcTotal'), gain = num('mcBattery');
    const cap = capacity();
    let text = '';
    if (kwh) {
      text = t('Lado del cargador: el coste se calcula con estos kWh.');
      if (gain && cap) {
        const eff = (gain / 100 * cap) / kwh;
        text += ' ' + (eff >= 0.5 && eff <= 1
          ? t('Con el % de batería, la eficiencia sale ≈ {p0} %.', {p0: Math.round(eff * 100)})
          : t('El % de batería no cuadra con esos kWh: no se calcula la eficiencia.'));
      } else {
        text += ' ' + t('Si pones el % de batería, se estima la eficiencia.');
      }
    } else if (gain && cap) {
      const battery = gain / 100 * cap;
      text = t('Lado de la batería: ≈ {p0} kWh, estimados con el % y la capacidad del coche.', {p0: historyNum(battery)});
      if (price !== null) text += ' ' + t('Coste ≈ {p0} €. Sin los kWh del cargador no hay eficiencia.', {p0: historyNum(price * battery, 2)});
      else text += ' ' + t('Sin los kWh del cargador no hay eficiencia.');
    } else if (gain && !cap) {
      text = t('Sin los kWh del cargador hace falta la capacidad de la batería en la ficha del coche.');
    } else if (total !== null && !kwh) {
      text = t('Con el total hace falta también el precio por kWh, los kWh o el % de batería.');
    }
    el('mcHow').textContent = text;
    el('mcHow').hidden = !text;
  }

  async function loadPlaces() {
    try {
      const res = await fetch(`/api/vehicles/${currentVin}/charges/places`);
      if (res.ok) places = await res.json();
    } catch (e) { /* offline: the list keeps «Sin indicar» and «Otro sitio…» */ }
  }

  async function open(id = null) {
    if (typeof currentVin === 'undefined' || !currentVin) return;
    editing = id;
    let c = null;
    await loadPlaces();
    if (id !== null) {
      try {
        const res = await fetch(`/api/charges/${id}/manual`);
        if (!res.ok) throw await fail(res);
        c = await res.json();
      } catch (e) { showToast(t('No se pudo abrir la carga: {p0}', {p0: e.message || e}), 'error', 6000); return; }
    }
    el('mcTitle').textContent = c ? t('Editar la carga') : t('Añadir una carga');
    el('mcDay').value = (c && c.day) || '';
    el('mcDay').max = new Date().toISOString().slice(0, 10);
    el('mcHour').value = (c && c.hour) || '';
    el('mcType').value = (c && c.charge_type) || 'ac';
    el('mcBattery').value = c && c.battery_gain_pct !== null ? c.battery_gain_pct : '';
    // what the charge was saved with comes back as typed: the charger's kWh and the total, or the price (battery side)
    el('mcKwh').value = c && c.kwh !== null ? c.kwh : '';
    el('mcTotal').value = c && c.total_cost !== null && c.kwh !== null ? c.total_cost : (c && c.free ? 0 : '');
    el('mcPrice').value = c && c.price_per_kwh !== null && c.kwh === null ? c.price_per_kwh : '';
    typed = c ? ['total', 'kwh', 'price'].filter((k) => el(TRIO[k]).value !== '') : [];
    el('mcPlaceText').value = (c && !c.geofence_id && c.place) || '';
    const known = c && c.place && places.places.some((p) => p.name === c.place);
    fillPlaces(c ? (c.geofence_id ? `g:${c.geofence_id}` : (c.place ? (known ? `p:${c.place}` : OTHER) : '')) : '');
    recompute();
    el('mcDelete').hidden = !c;
    error('mcError', '');
    el('chargeManualModal').classList.add('open');
    el('chargeManualModal').querySelector('.modal-card').scrollTop = 0;
  }

  function close() {
    el('chargeManualModal').classList.remove('open');
  }

  function body() {
    const place = el('mcPlace').value;
    const sent = (k) => (typed.includes(k) ? num(TRIO[k]) : null);       // the worked-out one is the server's to work out
    return {
      kwh: sent('kwh'), price_per_kwh: sent('price'), total_cost: sent('total'),
      day: el('mcDay').value || null, hour: el('mcDay').value && el('mcHour').value ? el('mcHour').value : null,
      battery_gain_pct: num('mcBattery'), charge_type: el('mcType').value,
      geofence_id: place.startsWith('g:') ? Number(place.slice(2)) : null,
      place: place === OTHER ? el('mcPlaceText').value.trim() || null : (place.startsWith('p:') ? place.slice(2) : null),
    };
  }

  async function save() {
    const data = body();
    const energy = data.kwh || (data.total_cost && data.price_per_kwh) || data.battery_gain_pct;
    if (!energy) {
      error('mcError', t('Indica los kWh del cargador, el total y el precio por kWh, o la batería cargada (%).'));
      el('mcKwh').focus();
      return;
    }
    const btn = el('mcSave');
    btn.disabled = true;
    try {
      const res = await fetch(editing === null ? `/api/vehicles/${currentVin}/charges` : `/api/charges/${editing}`, {
        method: editing === null ? 'POST' : 'PUT', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(data),
      });
      if (!res.ok) throw await fail(res);
      close();
      showToast(editing === null ? t('Carga añadida.') : t('Carga corregida.'), 'success');
      if (typeof loadCharges === 'function') loadCharges();
    } catch (e) {
      error('mcError', e.message || String(e));
    } finally {
      btn.disabled = false;
    }
  }

  async function remove(id = editing) {
    if (id === null || !await confirmDialog(t('¿Borrar esta carga? No se puede deshacer.'), { confirmText: t('Borrar'), danger: true })) return;
    try {
      const res = await fetch(`/api/charges/${id}`, { method: 'DELETE' });
      if (!res.ok) throw await fail(res);
      close();
      showToast(t('Carga borrada.'), 'success');
      if (typeof loadCharges === 'function') loadCharges();
    } catch (e) {
      showToast(t('No se pudo borrar la carga: {p0}', {p0: e.message || e}), 'error', 6000);
    }
  }

  // ───────────────────────── a spreadsheet ─────────────────────────
  const FIELD_LABEL = { date: 'Fecha', time: 'Hora', kwh: 'kWh del cargador', price: 'Precio por kWh', total: 'Total pagado', place: 'Lugar', battery: 'Batería cargada (%)', type: 'Tipo (AC o DC)' };
  const FIELD_ORDER = ['kwh', 'date', 'time', 'total', 'price', 'place', 'battery', 'type'];
  const STATUS = { ok: ['ok', 'Lista'], duplicate: ['dup', 'Ya estaba'], error: ['bad', 'Revisar'] };
  // why a row is not brought in (the codes of services/charge_import.py)
  const PROBLEM = {
    no_energy: 'faltan los kWh (o el total y el precio, o el % de batería)', energy: 'faltan los kWh (o el total y el precio, o el % de batería)', bad_date: 'la fecha no se entiende', kwh: 'los kWh no son válidos', battery: 'la batería cargada no es válida',
    cost: 'el coste no es válido', future: 'la fecha es futura', already: 'ya hay una carga igual ese día', repeated: 'está repetida en el fichero',
  };
  let file = null;
  let review = null;
  let lastBatch = null;

  function step(n) {
    [1, 2, 3].forEach((i) => {
      el(`ciStep${i}`).hidden = i !== n;
      const tab = el(`ciTab${i}`);
      tab.classList.toggle('is-on', i === n);
      tab.classList.toggle('is-done', i < n || n === 3);
    });
  }

  function openImport() {
    if (typeof currentVin === 'undefined' || !currentVin) return;
    file = null; review = null; lastBatch = null;
    el('ciFile').value = '';
    el('ciTemplate').href = `/api/vehicles/${currentVin}/charges/import/template`;
    error('ciError1', ''); error('ciError2', '');
    step(1);
    el('chargeImportModal').classList.add('open');
  }

  function closeImport() {
    el('chargeImportModal').classList.remove('open');
    if (lastBatch !== null && typeof loadCharges === 'function') loadCharges();
  }

  async function send(path, mapping) {
    const form = new FormData();
    form.append('file', file);
    if (mapping) form.append('mapping', JSON.stringify(mapping));
    const res = await fetch(`/api/vehicles/${currentVin}/charges/import${path}`, { method: 'POST', body: form });
    if (!res.ok) throw await fail(res);
    return res.json();
  }

  /** A file was chosen (or a column was reassigned): ask the server how it understands it. Nothing is stored. */
  async function preview(mapping = null) {
    error('ciError1', ''); error('ciError2', '');
    try {
      review = await send('/preview', mapping);
    } catch (e) {
      error(review ? 'ciError2' : 'ciError1', e.message || String(e));
      return;
    }
    drawReview();
    step(2);
  }

  function fileChosen(input) {
    file = input.files && input.files[0] ? input.files[0] : null;
    review = null;
    if (file) preview();
  }

  function mappingNow() {
    const mapping = {};
    FIELD_ORDER.forEach((f) => { const v = el(`ciMap-${f}`).value; mapping[f] = v === '' ? null : Number(v); });
    return mapping;
  }

  function day(iso) {
    return iso ? new Date(`${iso}T12:00:00`).toLocaleDateString(I18n.locale, { day: 'numeric', month: 'short', year: 'numeric' }) : t('Sin fecha');
  }

  function drawReview() {
    el('ciFileName').textContent = t('{p0} · {p1} filas. Así ha entendido EVBro las columnas:', {p0: review.filename || file.name, p1: review.total});
    el('ciMap').innerHTML = FIELD_ORDER.map((f) => {
      const options = [`<option value="">${t('No está en el fichero')}</option>`]
        .concat(review.columns.map((title, i) => `<option value="${i}" ${review.mapping[f] === i ? 'selected' : ''}>${escapeHtml(t('Columna «{p0}»', {p0: title}))}</option>`));
      return `<div class="${f === 'kwh' ? 'is-wide' : ''}"><label for="ciMap-${f}">${t(FIELD_LABEL[f])}</label><select id="ciMap-${f}" class="form-control" data-on-change="ManualCharges.remap()">${options.join('')}</select></div>`;
    }).join('');
    const c = review.counts;
    el('ciCounts').innerHTML = [
      `<span class="ci-chip is-ok">${t('Listas: {p0}', {p0: c.ok})}</span>`,
      c.duplicate ? `<span class="ci-chip is-dup">${t('Ya estaban: {p0}', {p0: c.duplicate})}</span>` : '',
      c.error ? `<span class="ci-chip is-bad">${t('Para revisar: {p0}', {p0: c.error})}</span>` : '',
    ].join('');
    el('ciRows').innerHTML = review.rows.map((r) => {
      const [cls, label] = STATUS[r.status];
      const cost = r.free ? t('Gratis') : (r.total_cost !== null && r.total_cost !== undefined ? `${historyNum(r.total_cost, 2)} €`
        : (r.price_per_kwh !== null && r.price_per_kwh !== undefined ? `${historyNum(r.price_per_kwh, 3)} €/kWh` : ''));
      const energy = r.kwh !== null && r.kwh !== undefined ? `${historyNum(r.kwh)} kWh` : (r.battery_gain_pct ? `+${Math.round(r.battery_gain_pct)} %` : '');
      const main = r.status !== 'error' || r.day !== undefined ? `<strong>${escapeHtml(day(r.day))}</strong>${energy ? ` · ${energy}` : ''}` : `<strong>${t('Fila {p0}', {p0: r.line})}</strong>`;
      const rest = [cost, r.place, r.problem ? t(PROBLEM[r.problem] || 'no es una carga válida') : ''].filter(Boolean).map(escapeHtml).join(' · ');
      return `<div class="ci-row ${r.status === 'ok' ? '' : 'is-out'}"><span>${main}</span><span class="ci-chip is-${cls}">${t(label)}</span>${rest ? `<small>${rest}</small>` : ''}</div>`;
    }).join('');
    const more = review.total > review.rows.length ? t('Se enseñan {p0} de {p1}.', {p0: review.rows.length, p1: review.total}) + ' ' : '';
    el('ciNote').textContent = more + (c.duplicate || c.error ? t('Las que ya estaban y las que hay que revisar no se importan.') : '');
    const btn = el('ciImport');
    btn.disabled = !c.ok || review.missing.length > 0;
    btn.textContent = c.ok === 1 ? t('Importar 1 carga') : (c.ok ? t('Importar {p0} cargas', {p0: c.ok}) : t('Nada que importar'));
    error('ciError2', review.missing.length ? t('Indica qué columna tiene los kWh, o las del total y el precio por kWh, o la de la batería cargada.') : '');
  }

  async function runImport() {
    const btn = el('ciImport');
    btn.disabled = true;
    try {
      const done = await send('', mappingNow());
      lastBatch = done.batch;
      el('ciDoneCount').textContent = done.imported;
      const span = done.from ? (done.from === done.to ? day(done.from) : t('del {p0} al {p1}', {p0: day(done.from), p1: day(done.to)})) : '';
      el('ciDoneText').textContent = [
        t('{p0} kWh y {p1} € más en tu historial', {p0: historyNum(done.kwh), p1: historyNum(done.cost, 2)}) + (span ? `, ${span}` : '') + '.',
        done.undated ? t('Sin fecha, apuntadas como anteriores a EVBro: {p0}.', {p0: done.undated}) : '',
      ].filter(Boolean).join(' ');
      step(3);
    } catch (e) {
      error('ciError2', e.message || String(e));
      btn.disabled = false;
    }
  }

  async function undoImport() {
    if (lastBatch === null || !await confirmDialog(t('¿Deshacer la importación? Se borran las cargas que acaba de traer.'), { confirmText: t('Deshacer'), danger: true })) return;
    try {
      const res = await fetch(`/api/vehicles/${currentVin}/charges/import?batch=${encodeURIComponent(lastBatch)}`, { method: 'DELETE' });
      if (!res.ok) throw await fail(res);
      showToast(t('Importación deshecha.'), 'success');
      closeImport();
    } catch (e) {
      showToast(t('No se pudo deshacer: {p0}', {p0: e.message || e}), 'error', 6000);
    }
  }

  document.addEventListener('DOMContentLoaded', () => {
    if (!el('chargeManualModal')) return;
    Object.entries(TRIO).forEach(([key, id]) => el(id).addEventListener('input', () => edited(key)));
    el('mcBattery').addEventListener('input', explain);
  });

  window.ManualCharges = {
    open, close, save, remove, placeChanged,
    openImport, closeImport, pickFile: () => el('ciFile').click(), fileChosen, remap: () => preview(mappingNow()), back: () => step(1), runImport, undoImport,
  };
})();
