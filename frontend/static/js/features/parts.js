/* EVBro — Consumibles y referencias: lo que lleva el coche (Ajustes → Coche → Consumibles) y las piezas de cada intervención.
 * The same row editor is used in the car sheet and in the maintenance modals (a "picker" that returns the parts to send).
 * (Script clásico: comparte el ámbito global con los demás ficheros; ver el orden de carga en templates/index.html.)
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

/** "2324" -> "sem. 23/2024" */
function dotLabel(dot) {
  if (!/^\d{4}$/.test(dot || '')) return dot || '';
  return `sem. ${dot.slice(0, 2)}/20${dot.slice(2)}`;
}

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
      <label><span>Marca</span><input data-f="brand" class="form-control" maxlength="80" value="${escapeHtml(part.brand || '')}" /></label>
      <label><span>Referencia</span><input data-f="reference" class="form-control" maxlength="120" value="${escapeHtml(part.reference || '')}" /></label>
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
  row.querySelector('[data-wrap=season]').hidden = !tyre;
  row.querySelector('[data-wrap=dot]').hidden = !tyre;
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
    reference: val('reference') || null, spec: val('spec') || null };
  const qty = parseFloat(val('quantity').replace(',', '.'));
  part.quantity = isNaN(qty) ? null : qty;
  const attrs = {};
  if (!row.querySelector('[data-wrap=dot]').hidden) { if (val('season')) attrs.season = val('season'); if (val('dot')) attrs.dot = val('dot'); }
  part.attrs = Object.keys(attrs).length ? attrs : null;
  part.hasData = !!(part.name || part.brand || part.reference || part.spec || part.quantity !== null || part.attrs);
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

// ───────────────────────── Catálogo compartido ─────────────────────────
let myProposals = {};          // catalog entry id → entry (my own proposals, with their state)
let currentModel = '';
const PROPOSAL_LABEL = { pending: 'Propuesta enviada', approved: 'En el catálogo', rejected: 'Propuesta rechazada' };

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
  if (!e) return p.catalog_id ? '<span class="part-tag part-tag-approved">En el catálogo</span>' : '';
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
  const row = pEl('partModalFields').firstElementChild;
  const set = (f, v) => { const el = row.querySelector(`[data-f="${f}"]`); if (el && v !== null && v !== undefined && v !== '') el.value = v; };
  set('name', e.name); set('brand', e.brand); set('reference', e.reference); set('spec', e.spec); set('quantity', e.quantity);
  showToast('Datos copiados del catálogo; revísalos y guarda.', 'info');
}

async function loadParts() {
  if (!currentVin || !pEl('partsGroups')) return;
  await loadMyProposals();
  try {
    await ensurePartCatalog();
    vehicleParts = await fetch(`/api/vehicles/${currentVin}/parts`).then((r) => r.json());
    renderParts();
  } catch (e) {
    console.warn('No se pudieron cargar las piezas:', e);
  }
}

function partMatches(p, q) {
  const hay = [p.name, p.brand, p.reference, p.spec, p.notes, p.category_label, p.position_label, p.attrs?.dot, p.attrs?.season].filter(Boolean).join(' ').toLowerCase();
  return hay.includes(q);
}

function renderParts() {
  const q = (pEl('partsSearch').value || '').trim().toLowerCase();
  const rows = vehicleParts.filter((p) => !q || partMatches(p, q));
  const present = new Set(vehicleParts.map((p) => p.category));
  pEl('partsSuggest').innerHTML = q ? '' : (partCatalog.categories.filter((c) => !present.has(c.key) && c.key !== 'other')
    .map((c) => `<button type="button" class="maint-chip" data-on-click="openPartModal(null, '${c.key}')">${escapeHtml(c.label)}</button>`).join('')) || '';
  pEl('partsSuggestWrap').hidden = !pEl('partsSuggest').innerHTML;

  if (!vehicleParts.length) { pEl('partsGroups').innerHTML = '<div class="costs-empty">Todavía no has anotado ninguna pieza. Empieza por las sugeridas de arriba, o regístralas al anotar una intervención.</div>'; return; }
  if (!rows.length) { pEl('partsGroups').innerHTML = '<div class="costs-empty">No hay nada que coincida con la búsqueda.</div>'; return; }
  pEl('partsGroups').innerHTML = partCatalog.groups.map((g) => {
    const items = rows.filter((p) => p.group === g.key);
    if (!items.length) return '';
    return `<section class="glass part-group"><h3 class="part-group-title">${escapeHtml(g.label)}</h3>${items.map(partItemHtml).join('')}</section>`;
  }).join('');
}

function partItemHtml(p) {
  const title = p.position_label ? `${p.category_label} · ${p.position_label.toLowerCase()}` : p.category_label;
  const line1 = [p.brand, p.name !== p.category_label ? p.name : null].filter(Boolean).join(' ') || 'Sin marca indicada';
  const cat = partCat(p.category);
  const line2 = [p.reference ? t('Ref. {p0}', {p0: p.reference}) : null, p.spec, p.quantity !== null && p.quantity !== undefined ? `${String(p.quantity).replace('.', ',')} ${(cat?.qty_label || '').toLowerCase()}` : null,
    p.attrs?.season ? partCatalog.tyre_seasons.find((s) => s.key === p.attrs.season)?.label : null, p.attrs?.dot ? t('DOT {p0} ({p1})', {p0: p.attrs.dot, p1: dotLabel(p.attrs.dot)}) : null].filter(Boolean).join(' · ');
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
  const entry = p && p.catalog_id ? myProposals[p.catalog_id] : null;
  pEl('partPropose').hidden = !p || (p.catalog_id && (!entry || entry.status !== 'rejected'));
  pEl('partModal').classList.add('open');
  loadCatalogSuggestions((p && p.category) || category || pEl('partModalFields').querySelector('[data-f="category"]')?.value);
}
function closePartModal() { pEl('partModal').classList.remove('open'); }

async function savePart() {
  const id = pEl('partId').value;
  const { hasData, ...body } = readPartRow(pEl('partModalFields').firstElementChild);
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
