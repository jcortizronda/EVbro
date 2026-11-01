/* EVBro — Consumables and part numbers: what the car takes (Más → Coches → Consumibles) and the parts of each intervention.
 * The same row editor is used in the car sheet and in the maintenance modals (a "picker" that returns the parts to send).
 * With «Ver catálogo online» the list also shows the approved entries of the shared catalogue for the car's model, under my own
 * parts (each with its state: not proposed, proposal sent, in the catalogue, rejected); tapping one copies it to the car.
 * (Classic script: shares the global scope with the other files; see the load order in templates/index.html.)
 */

let partCatalog = null;
let vehicleParts = [];
const pEl = (id) => document.getElementById(id);
const monthsES = ['ene', 'feb', 'mar', 'abr', 'may', 'jun', 'jul', 'ago', 'sep', 'oct', 'nov', 'dic'];

async function ensurePartCatalog() {
  if (!partCatalog) partCatalog = await fetch('/api/part-categories').then((r) => r.json());
  return partCatalog;
}
const partCat = (key) => (partCatalog?.categories || []).find((c) => c.key === key) || null;

// ───────────────────────── Row editor (shared) ─────────────────────────
function partRowHtml(part = {}) {
  const cats = partCatalog.categories;
  const key = part.category || cats[0].key;
  const opt = (arr, sel) => arr.map((o) => `<option value="${o.key}" ${o.key === sel ? 'selected' : ''}>${escapeHtml(o.label)}</option>`).join('');
  const a = part.attrs || {};
  return `<div class="part-row" data-part-row>
    <div class="part-grid">
      <label class="part-wide"><span>Tipo de pieza</span><select data-f="category" class="form-control" data-on-change="onPartCategoryChange(this)">${opt(cats, key)}</select></label>
      <label data-wrap="position"><span>Posición</span><select data-f="position" class="form-control"></select></label>
      <label class="part-wide"><span>Nombre o modelo</span><input data-f="name" class="form-control" maxlength="150" value="${escapeHtml(part.name || '')}" placeholder="Opcional" /></label>
      <label><span>Marca de la pieza</span><input data-f="brand" class="form-control" maxlength="80" value="${escapeHtml(part.brand || '')}" /></label>
      <label title="${escapeHtml(t('Número de pieza que pone la marca de la pieza (Mann, Bosch…)'))}"><span>Ref. marca</span><input data-f="reference" class="form-control" maxlength="120" value="${escapeHtml(part.reference || '')}" /></label>
      <label title="${escapeHtml(t('Número de pieza de la marca del coche (el de Ebro, en un Ebro)'))}"><span>Ref. fabricante</span><input data-f="oem_reference" class="form-control" maxlength="120" value="${escapeHtml(part.oem_reference || '')}" placeholder="Opcional" /></label>
      <label><span data-lbl="spec">Especificación</span><input data-f="spec" class="form-control" maxlength="120" value="${escapeHtml(part.spec || '')}" /></label>
      <label><span data-lbl="qty">Cantidad</span><input data-f="quantity" type="number" min="0" step="0.1" inputmode="decimal" class="form-control" value="${part.quantity ?? ''}" /></label>
      <label data-wrap="season"><span>Tipo de neumático</span><select data-f="season" class="form-control"><option value="">Sin indicar</option>${opt(partCatalog.tyre_seasons, a.season)}</select></label>
      <label data-wrap="dot"><span>Fabricación (DOT)</span><input data-f="dot" class="form-control" maxlength="4" inputmode="numeric" placeholder="2324" value="${escapeHtml(a.dot || '')}" /></label>
    </div>
  </div>`;
}

function syncPartRow(row, keepPosition) {
  const cat = partCat(row.querySelector('[data-f=category]').value);
  const posSel = row.querySelector('[data-f=position]');
  const current = keepPosition ?? posSel.value;
  posSel.innerHTML = `<option value="">Sin indicar</option>${(cat.positions || []).map((k) => `<option value="${k}">${escapeHtml(partCatalog.positions.find((p) => p.key === k)?.label || k)}</option>`).join('')}`;
  posSel.value = (cat.positions || []).includes(current) ? current : '';
  row.querySelector('[data-wrap=position]').hidden = !(cat.positions || []).length;
  row.querySelector('[data-lbl=spec]').textContent = cat.spec_label;
  row.querySelector('[data-f=spec]').placeholder = cat.spec_hint || '';
  row.querySelector('[data-lbl=qty]').textContent = cat.qty_label;
  const tyre = (cat.attrs || []).includes('dot');
  // The DOT belongs to the tyres fitted in an intervention, not to the consumable on the car sheet
  const sheet = !!row.closest('#partModalFields');
  row.querySelector('[data-wrap=season]').hidden = !tyre;
  row.querySelector('[data-wrap=dot]').hidden = !tyre || sheet;
}
function onPartCategoryChange(sel) {
  syncPartRow(sel.closest('[data-part-row]'));
  if (sel.closest('#partModalFields')) loadCatalogSuggestions(sel.value);
}

/** Fills a freshly rendered row: the position (a select that depends on the category) is set after the options exist. */
function initPartRow(row, part = {}) { syncPartRow(row, part.position || ''); }

function readPartRow(row) {
  const f = (k) => row.querySelector(`[data-f=${k}]`);
  const val = (k) => f(k).value.trim();
  const part = { category: val('category'), position: val('position') || null, name: val('name'), brand: val('brand') || null,
    reference: val('reference') || null, oem_reference: val('oem_reference') || null, spec: val('spec') || null };
  const qty = parseFloat(val('quantity').replace(',', '.'));
  part.quantity = isNaN(qty) ? null : qty;
  const attrs = {};
  if (!row.querySelector('[data-wrap=season]').hidden && val('season')) attrs.season = val('season');
  if (!row.querySelector('[data-wrap=dot]').hidden && val('dot')) attrs.dot = val('dot');
  part.attrs = Object.keys(attrs).length ? attrs : null;
  part.hasData = !!(part.name || part.brand || part.reference || part.oem_reference || part.spec || part.quantity !== null || part.attrs);
  return part;
}

// ───────────────────────── Picker inside maintenance modals ─────────────────────────
async function mountPartsPicker(boxId) {
  await ensurePartCatalog();
  pEl(boxId).innerHTML = `<div class="parts-picker">
    <div class="parts-picker-head"><strong>Piezas montadas</strong><small>Opcional: aceite, filtros, neumáticos…</small></div>
    <div class="parts-picker-rows" data-rows></div>
    <button type="button" class="btn-secondary parts-add" data-on-click="addPickerRow('${boxId}')">Añadir pieza</button>
    <label class="parts-update"><input type="checkbox" data-update checked /> Actualizar la ficha del coche con estas piezas</label>
  </div>`;
}

function addPickerRow(boxId, category) {
  const rows = pEl(boxId).querySelector('[data-rows]');
  rows.insertAdjacentHTML('beforeend', partRowHtml({ category }));
  const row = rows.lastElementChild;
  initPartRow(row);
  row.insertAdjacentHTML('afterbegin', `<button type="button" class="part-remove" aria-label="Quitar pieza" data-on-click="removeClosest(this, '[data-part-row]')">Quitar</button>`);
}

/** Returns {parts, update_parts} ready to spread into the request body (rows left empty are ignored). */
function collectParts(boxId) {
  const box = pEl(boxId);
  if (!box || !box.querySelector('[data-rows]')) return {};
  const parts = [...box.querySelectorAll('[data-part-row]')].map(readPartRow).filter((p) => p.hasData).map(({ hasData, ...p }) => p);
  if (!parts.length) return {};
  return { parts, update_parts: box.querySelector('[data-update]').checked };
}

// ───────────────────────── Car sheet: "Consumibles" pane ─────────────────────────
function setVehiclePane(which) {
  pEl('vpPaneData').hidden = which !== 'data';
  pEl('vpPaneParts').hidden = which !== 'parts';
  document.querySelectorAll('.vp-seg-btn').forEach((b) => { const on = b.dataset.pane === which; b.classList.toggle('is-active', on); b.setAttribute('aria-selected', on ? 'true' : 'false'); });
  if (which === 'parts') loadParts();
}

// ───────────────────────── Shared catalogue ─────────────────────────
let myProposals = {};          // catalog entry id → entry (my own proposals, with their state)
let currentModel = '';
let catalogEntries = [];       // approved entries for the car's model, shown with «Ver catálogo online»
const PROPOSAL_LABEL = { pending: 'Propuesta enviada', approved: 'En el catálogo', rejected: 'Propuesta rechazada' };
const PARTS_CATALOG_KEY = 'evbro_parts_catalog';
const lc = (s) => String(s || '').trim().toLowerCase();

function showingCatalog() {
  try { return localStorage.getItem(PARTS_CATALOG_KEY) === '1'; } catch (e) { return false; }
}
function togglePartsCatalog(on) {
  try { localStorage.setItem(PARTS_CATALOG_KEY, on ? '1' : '0'); } catch (e) { /* the switch still works for this visit */ }
  loadParts(on);
}

/** The maker's part number of an entry for this car's model: it belongs to the fitment (Ebro's number is not another make's). */
function entryOem(e, model = currentModel) {
  const fits = e.fitments || [];
  const f = fits.find((x) => lc(x.model) === lc(model));
  return f ? f.oem_reference || null : (fits.length ? null : e.oem_reference || null);
}

/** Is a catalogue entry already one of my parts? Same link, same brand + reference, or (without a reference) the same maker's number. */
function entryCovered(e) {
  return vehicleParts.some((p) => p.catalog_id === e.id || (p.category === e.category && (
    (e.reference && lc(p.reference) === lc(e.reference) && lc(p.brand) === lc(e.brand)) ||
    (!e.reference && entryOem(e) && lc(p.oem_reference) === lc(entryOem(e))))));
}

async function loadCatalogEntries() {
  catalogEntries = [];
  const model = await currentVehicleModel();
  const note = pEl('partsCatalogNote');
  if (!model) { note.textContent = t('Indica el modelo del coche en la ficha para ver su catálogo.'); note.hidden = false; return; }
  try {
    const res = await fetch(`/api/catalog?model=${encodeURIComponent(model)}&limit=100`);
    catalogEntries = res.ok ? await res.json() : [];
    note.textContent = catalogEntries.length ? '' : t('El catálogo todavía no tiene piezas aprobadas para el {p0}.', {p0: model});
    note.hidden = !!catalogEntries.length;
  } catch (e) {
    note.textContent = t('No se pudo cargar el catálogo.'); note.hidden = false;
  }
}

async function loadMyProposals() {
  try {
    const res = await fetch('/api/catalog/mine');
    const list = res.ok ? await res.json() : [];
    myProposals = Object.fromEntries(list.map((e) => [e.id, e]));
  } catch (e) { myProposals = {}; }
}

async function currentVehicleModel() {
  try {
    const res = await fetch('/api/vehicles');
    const list = res.ok ? await res.json() : [];
    const v = list.find((x) => x.vin === currentVin);
    currentModel = (v && v.model) || '';
  } catch (e) { currentModel = ''; }
  return currentModel;
}

function proposalTag(p) {
  const e = p.catalog_id ? myProposals[p.catalog_id] : null;
  if (!e && !p.catalog_id) return showingCatalog() ? `<span class="part-tag part-tag-local" title="${escapeHtml(t('Solo está en tu coche'))}">${t('Sin proponer')}</span>` : '';
  if (!e) return '<span class="part-tag part-tag-approved">En el catálogo</span>';
  return `<span class="part-tag part-tag-${e.status}" title="${escapeHtml(e.reject_reason || '')}">${PROPOSAL_LABEL[e.status] || e.status}</span>`;
}

async function proposePart() {
  const id = pEl('partId').value;
  if (!id) return;
  try {
    const res = await fetch(`/api/parts/${id}/propose`, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: '{}' });
    const data = await res.json().catch(() => ({}));
    if (!res.ok) throw new Error(data.detail || res.status);
    showToast('Propuesta enviada. Se publicará cuando la revise el administrador.', 'success', 6000);
    closePartModal();
    loadParts();
  } catch (e) {
    showToast(t('No se pudo proponer: {p0}', {p0: e.message || e}), 'error', 6000);
  }
}

async function loadCatalogSuggestions(category) {
  const box = pEl('partCatalogSuggest');
  if (!box) return;
  box.hidden = true;
  box.innerHTML = '';
  const model = currentModel || await currentVehicleModel();
  if (!model || !category) return;
  try {
    const res = await fetch(`/api/catalog?model=${encodeURIComponent(model)}&category=${encodeURIComponent(category)}&limit=8`);
    const list = res.ok ? await res.json() : [];
    if (!list.length) return;
    box.innerHTML = `<span class="part-suggest-title">${t('Lo que usan otros {p0}', {p0: escapeHtml(model)})}</span>` + list.map((e) => `
      <button type="button" class="maint-chip" data-on-click="applyCatalogEntry(${e.id})" title="${escapeHtml(e.spec || '')}">${escapeHtml([e.brand, e.reference || e.name].filter(Boolean).join(' · '))}${e.confirmations ? ` <small>×${e.confirmations + 1}</small>` : ''}</button>`).join('');
    box.dataset.entries = JSON.stringify(list);
    box.hidden = false;
  } catch (e) { /* the catalogue is a help, never an error */ }
}

function applyCatalogEntry(id) {
  const box = pEl('partCatalogSuggest');
  const e = (JSON.parse(box.dataset.entries || '[]')).find((x) => x.id === id);
  if (!e) return;
  fillRowFromEntry(pEl('partModalFields').firstElementChild, e);
  showToast('Datos copiados del catálogo; revísalos y guarda.', 'info');
}

/** Copies a catalogue entry into the editor row and remembers the link (dropped on save if the references are changed by hand). */
function fillRowFromEntry(row, e) {
  const set = (f, v) => { const el = row.querySelector(`[data-f="${f}"]`); if (el && v !== null && v !== undefined && v !== '') el.value = v; };
  set('name', e.name); set('brand', e.brand); set('reference', e.reference); set('oem_reference', entryOem(e)); set('spec', e.spec); set('quantity', e.quantity);
  if (e.position && row.querySelector('[data-f="position"]')) row.querySelector('[data-f="position"]').value = e.position;
  row.dataset.catalog = JSON.stringify({ id: e.id, brand: lc(e.brand), reference: lc(e.reference), oem: lc(entryOem(e)) });
}

/** «Ver catálogo online»: tapping an entry opens the editor with it filled in, ready to save on this car. */
async function useCatalogEntry(id) {
  const e = catalogEntries.find((x) => x.id === id);
  if (!e) return;
  await openPartModal(null, e.category);
  const row = pEl('partModalFields').firstElementChild;
  fillRowFromEntry(row, e);
  pEl('partModalTitle').textContent = t('Añadir del catálogo');
  const cat = partCat(e.category);
  const mine = !cat?.multiple && vehicleParts.find((p) => p.category === e.category && (p.position || null) === (e.position || null));
  pEl('partReplaceNote').textContent = mine ? t('Sustituirá a la que tienes anotada: {p0}.', {p0: [mine.brand, mine.reference || mine.name].filter(Boolean).join(' ')}) : '';
  pEl('partReplaceNote').hidden = !mine;
}

async function loadParts(withCatalog = showingCatalog()) {
  if (!currentVin || !pEl('partsGroups')) return;
  pEl('partsShowCatalog').checked = withCatalog;
  pEl('partsCatalogNote').hidden = true;
  await loadMyProposals();
  try {
    await ensurePartCatalog();
    vehicleParts = await fetch(`/api/vehicles/${currentVin}/parts`).then((r) => r.json());
    if (withCatalog) await loadCatalogEntries(); else catalogEntries = [];
    renderParts();
  } catch (e) {
    console.warn('Could not load the parts:', e);
  }
}

function partMatches(p, q) {
  const hay = [p.name, p.brand, p.reference, p.oem_reference, p.spec, p.notes, p.category_label, p.position_label, p.attrs?.season].filter(Boolean).join(' ').toLowerCase();
  return hay.includes(q);
}

function renderParts() {
  const q = (pEl('partsSearch').value || '').trim().toLowerCase();
  const rows = vehicleParts.filter((p) => !q || partMatches(p, q));
  const present = new Set(vehicleParts.map((p) => p.category));
  pEl('partsSuggest').innerHTML = q ? '' : (partCatalog.categories.filter((c) => !present.has(c.key) && c.key !== 'other')
    .map((c) => `<button type="button" class="maint-chip" data-on-click="openPartModal(null, '${c.key}')">${escapeHtml(c.label)}</button>`).join('')) || '';
  pEl('partsSuggestWrap').hidden = !pEl('partsSuggest').innerHTML;

  // With the catalogue on: approved entries not already on the car, and my proposals still waiting whose part is no longer on the sheet
  const groupOf = (e) => partCat(e.category)?.group || 'other';
  const extra = catalogEntries.filter((e) => !entryCovered(e)).map((e) => ({ ...e, _kind: 'catalog' }));
  if (showingCatalog()) {
    const linked = new Set(vehicleParts.map((p) => p.catalog_id).filter(Boolean));
    Object.values(myProposals).filter((e) => e.status === 'pending' && !linked.has(e.id) && !entryCovered(e) && (e.fitments || []).some((f) => lc(f.model) === lc(currentModel)))
      .forEach((e) => extra.push({ ...e, _kind: 'proposal' }));
  }
  const extraRows = extra.filter((e) => !q || partMatches(e, q));
  if (!vehicleParts.length && !extra.length) { pEl('partsGroups').innerHTML = '<div class="costs-empty">Todavía no has anotado ninguna pieza. Empieza por las sugeridas de arriba, o regístralas al anotar una intervención.</div>'; return; }
  if (!rows.length && !extraRows.length) { pEl('partsGroups').innerHTML = '<div class="costs-empty">No hay nada que coincida con la búsqueda.</div>'; return; }
  pEl('partsGroups').innerHTML = partCatalog.groups.map((g) => {
    const items = rows.filter((p) => p.group === g.key);
    const others = extraRows.filter((e) => groupOf(e) === g.key);
    if (!items.length && !others.length) return '';
    return `<section class="glass part-group"><h3 class="part-group-title">${escapeHtml(g.label)}</h3>${items.map(partItemHtml).join('')}${others.map(catalogItemHtml).join('')}</section>`;
  }).join('');
}

/** A catalogue entry (or a proposal of mine still waiting) listed under my parts. */
function catalogItemHtml(e) {
  const title = e.position_label ? `${e.category_label} · ${e.position_label.toLowerCase()}` : e.category_label;
  const line1 = [e.brand, e.name !== e.category_label ? e.name : null].filter(Boolean).join(' ') || 'Sin marca indicada';
  const oem = entryOem(e);
  const line2 = [e.reference ? t('Ref. marca {p0}', {p0: e.reference}) : null, oem ? t('Ref. fabricante {p0}', {p0: oem}) : null, e.spec,
    e.quantity !== null && e.quantity !== undefined ? `${String(e.quantity).replace('.', ',')} ${(e.qty_label || '').toLowerCase()}` : null].filter(Boolean).join(' · ');
  if (e._kind === 'proposal') {
    return `<button type="button" class="part-item part-item-catalog" disabled>
      <div class="part-item-main"><strong>${escapeHtml(title)}<span class="part-tag part-tag-pending">${PROPOSAL_LABEL.pending}</span></strong><span>${escapeHtml(line1)}</span>${line2 ? `<small>${escapeHtml(line2)}</small>` : ''}</div>
    </button>`;
  }
  const votes = e.confirmations ? ` ×${e.confirmations + 1}` : '';
  return `<button type="button" class="part-item part-item-catalog" data-on-click="useCatalogEntry(${e.id})">
    <div class="part-item-main"><strong>${escapeHtml(title)}<span class="part-tag part-tag-catalog">${t('Catálogo')}${votes}</span></strong><span>${escapeHtml(line1)}</span>${line2 ? `<small>${escapeHtml(line2)}</small>` : ''}<small class="part-use">${t('Usar en mi coche')}</small></div>
  </button>`;
}

function partItemHtml(p) {
  const title = p.position_label ? `${p.category_label} · ${p.position_label.toLowerCase()}` : p.category_label;
  const line1 = [p.brand, p.name !== p.category_label ? p.name : null].filter(Boolean).join(' ') || 'Sin marca indicada';
  const cat = partCat(p.category);
  const line2 = [p.reference ? t('Ref. marca {p0}', {p0: p.reference}) : null, p.oem_reference ? t('Ref. fabricante {p0}', {p0: p.oem_reference}) : null, p.spec, p.quantity !== null && p.quantity !== undefined ? `${String(p.quantity).replace('.', ',')} ${(cat?.qty_label || '').toLowerCase()}` : null,
    p.attrs?.season ? partCatalog.tyre_seasons.find((s) => s.key === p.attrs.season)?.label : null].filter(Boolean).join(' · ');
  let when = '';
  if (p.installed_at) { const d = serverDate(p.installed_at); when = `Montado ${monthsES[d.getMonth()]} ${d.getFullYear()}${p.installed_odometer ? ` · ${Math.round(p.installed_odometer).toLocaleString(I18n.locale)} km` : ''}`; }
  return `<button type="button" class="part-item" data-on-click="openPartModal(${p.id})">
    <div class="part-item-main"><strong>${escapeHtml(title)}${proposalTag(p)}</strong><span>${escapeHtml(line1)}</span>${line2 ? `<small>${escapeHtml(line2)}</small>` : ''}${when ? `<small class="part-when">${escapeHtml(when)}</small>` : ''}</div>
  </button>`;
}

async function openPartModal(id, category) {
  await ensurePartCatalog();
  const p = id ? vehicleParts.find((x) => x.id === id) : null;
  pEl('partId').value = p ? p.id : '';
  pEl('partModalTitle').textContent = p ? 'Editar pieza' : 'Añadir pieza';
  pEl('partModalFields').innerHTML = partRowHtml(p || { category });
  initPartRow(pEl('partModalFields').firstElementChild, p || {});
  pEl('partNotes').value = p ? p.notes || '' : '';
  pEl('partDelete').hidden = !p;
  pEl('partReplaceNote').hidden = true;
  const entry = p && p.catalog_id ? myProposals[p.catalog_id] : null;
  pEl('partPropose').hidden = !p || (p.catalog_id && (!entry || entry.status !== 'rejected'));
  pEl('partModal').classList.add('open');
  loadCatalogSuggestions((p && p.category) || category || pEl('partModalFields').querySelector('[data-f="category"]')?.value);
}
function closePartModal() { pEl('partModal').classList.remove('open'); }

async function savePart() {
  const id = pEl('partId').value;
  const row = pEl('partModalFields').firstElementChild;
  const { hasData, ...body } = readPartRow(row);
  // Copied from the catalogue: keep the link only while the part numbers are still the catalogue's
  const link = row.dataset.catalog ? JSON.parse(row.dataset.catalog) : null;
  if (link && lc(body.brand) === link.brand && lc(body.reference) === link.reference && lc(body.oem_reference) === link.oem) body.catalog_id = link.id;
  body.notes = pEl('partNotes').value.trim() || null;
  try {
    const res = await fetch(id ? `/api/parts/${id}` : `/api/vehicles/${currentVin}/parts`, { method: id ? 'PUT' : 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) });
    const data = await res.json().catch(() => ({}));
    if (!res.ok) throw new Error(Array.isArray(data.detail) ? data.detail.map((d) => d.msg).join(' · ') : (data.detail || res.status));
    closePartModal();
    showToast('Pieza guardada', 'success');
    loadParts();
  } catch (e) {
    showToast(t('No se pudo guardar: {p0}', {p0: e.message || e}), 'error', 6000);
  }
}

async function deletePart() {
  const id = pEl('partId').value;
  if (!id || !await confirmDialog('¿Borrar esta pieza de la ficha?', { confirmText: 'Borrar', danger: true })) return;
  const res = await fetch(`/api/parts/${id}`, { method: 'DELETE' });
  if (res.ok) { closePartModal(); loadParts(); }
}
