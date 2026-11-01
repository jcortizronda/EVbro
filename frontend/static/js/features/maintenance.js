/* EVBro — Maintenance: plan switch (official / custom), custom tasks and history.
 * The official view (next service, full plan, log a step) is in maintenance-official.js.
 * (Classic script: shares the global scope with the other files; see the load order in templates/index.html.)
 */

let maintenancePlan = null;          // last response of GET /maintenance/plan
const MAINT_STATUS = { ok: 'Al día', warning: 'Próximo', due: 'Vencido' };

async function loadMaintenance() {
  if (!currentVin) return;
  try {
    const res = await fetch(`/api/vehicles/${currentVin}/maintenance/plan`);
    if (!res.ok) throw new Error(res.status);
    maintenancePlan = await res.json();
    if (maintenancePlan.vehicle && maintenancePlan.vehicle.odometer) {
      latestOdometer = maintenancePlan.vehicle.odometer;
      latestModel = maintenancePlan.vehicle.model;
    }
    renderMaintenance();
  } catch (err) {
    const grid = document.getElementById('maintenanceGrid');
    if (grid) grid.innerHTML = `<div class="u-color-var-accent-red_grid-column-1-1_text-align-center">${t('No se pudo cargar el mantenimiento ({p0}).', {p0: escapeHtml(err)})}</div>`;
  }
}

function renderMaintenance() {
  const plan = maintenancePlan;
  if (!plan) return;
  const available = plan.official_available !== false;      // the official plan is EBRO's: other brands only have their own tasks
  const official = available && plan.mode === 'official';
  const sw = document.getElementById('maintModeSwitch');
  if (sw) sw.hidden = !available;
  const intro = document.getElementById('maintIntro');
  if (intro) intro.textContent = available ? 'Sigue la pauta oficial de EBRO o define tus propias tareas.' : 'Define tus propias tareas e intervalos. La ITV se calcula igual para cualquier marca.';
  for (const [id, on] of [['maintModeOfficial', official], ['maintModeCustom', !official]]) {
    const b = document.getElementById(id);
    if (b) { b.setAttribute('aria-selected', String(on)); b.classList.toggle('is-active', on); }
  }
  document.getElementById('maintOfficialView').hidden = !official;
  document.getElementById('maintCustomView').hidden = official;
  renderItv(plan.itv);
  if (official) renderOfficialPlan(plan); else renderCustomPlan(plan);
}

async function setMaintenanceMode(mode) {
  if (!currentVin || (maintenancePlan && maintenancePlan.mode === mode)) return;
  try {
    const res = await fetch(`/api/vehicles/${currentVin}/maintenance/plan`, {
      method: 'PUT', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ mode }) });
    if (!res.ok) throw new Error((await res.json()).detail || res.status);
    maintenancePlan = await res.json();
    renderMaintenance();
    showToast(mode === 'official' ? 'Plan oficial de EBRO activado' : 'Plan personalizado activado', 'success');
  } catch (e) {
    showToast(t('No se pudo cambiar el plan: {p0}', {p0: e.message || e}), 'error');
  }
}

// ───────────────────────── Custom plan: own tasks ─────────────────────────
function remainingText(item) {
  const parts = [];
  if (item.remaining_km !== null && item.remaining_km !== undefined) {
    parts.push(item.remaining_km <= 0
      ? `<strong class="maint-over">${t('venció hace {p0} km', {p0: Math.abs(Math.round(item.remaining_km)).toLocaleString(I18n.locale)})}</strong>`
      : `<strong>${Math.round(item.remaining_km).toLocaleString(I18n.locale)} km</strong>`);
  }
  if (item.remaining_days !== null && item.remaining_days !== undefined) {
    parts.push(item.remaining_days <= 0
      ? `<strong class="maint-over">${t('venció hace {p0} días', {p0: Math.abs(item.remaining_days)})}</strong>`
      : `<strong>${item.remaining_days} días</strong>`);
  }
  return parts.length ? parts.join(' · ') : 'Sin plazo';
}

function renderCustomPlan(plan) {
  const grid = document.getElementById('maintenanceGrid');
  const items = plan.custom.items;
  if (!items.length) {
    grid.innerHTML = '<div class="maint-empty">Aún no tienes tareas. Añade una con «Nueva tarea» o elige una de las sugeridas.</div>';
  } else {
    grid.innerHTML = items.map(item => {
      const interval = [
        item.interval_km ? t('cada {p0} km', {p0: Math.round(item.interval_km).toLocaleString(I18n.locale)}) : null,
        item.interval_months ? t('cada {p0} meses', {p0: item.interval_months}) : null,
      ].filter(Boolean).join(' o ') || 'Sin intervalo';
      return `
        <div class="maintenance-card">
          <div class="maint-header">
            <div class="maint-title-group"><h3>${escapeHtml(item.name)}</h3><span class="maint-category-badge">${escapeHtml(item.category)}</span></div>
            <span class="maint-status-pill ${item.status}">${MAINT_STATUS[item.status] || item.status}</span>
          </div>
          <div class="maint-progress-wrap">
            <div class="maint-progress-labels"><span>Falta (lo que antes llegue)</span><span>${remainingText(item)}</span></div>
            <div class="maint-progress-track"><div class="maint-progress-fill ${item.status}" data-css="width: ${item.progress_pct}%"></div></div>
          </div>
          <div class="maint-card-meta">
            <div>Intervalo: <strong>${interval}</strong></div>
            <div>Último servicio: ${item.last_service_odometer ? Math.round(item.last_service_odometer).toLocaleString(I18n.locale) + ' km' : '--'}${item.last_service_date ? ' · ' + serverDate(item.last_service_date).toLocaleDateString(I18n.locale) : ''}</div>
            ${item.notes ? `<div class="maint-card-notes">${escapeHtml(item.notes)}</div>` : ''}
          </div>
          <div class="maint-footer">
            <button class="btn-primary" data-on-click="openCompleteMaintModal(${item.id}, ${JSON.stringify(item.name).replace(/"/g, '&quot;')})">Hecho</button>
            <div class="maint-actions">
              <button class="nav-btn" title="Editar" aria-label="Editar" data-on-click="showEditMaintenanceModal(${item.id})">${icon('edit', 16)}</button>
              <button class="nav-btn" title="Eliminar" aria-label="Eliminar" data-on-click="deleteMaintItem(${item.id})">${icon('trash', 16)}</button>
            </div>
          </div>
        </div>`;
    }).join('');
    applyDataStyles(grid);
  }
  const box = document.getElementById('maintSuggestBox');
  const sug = plan.custom.suggestions;
  box.hidden = !sug.length;
  document.getElementById('maintSuggestions').innerHTML = sug.map(s =>
    `<button type="button" class="maint-chip" data-on-click="addSuggestedTask('${s.key}')">${escapeHtml(s.name)}</button>`).join('');
}

function addSuggestedTask(key) {
  const s = (maintenancePlan?.custom.suggestions || []).find(x => x.key === key);
  if (!s) return;
  showAddMaintenanceModal();
  document.getElementById('itemMaintName').value = s.name;
  document.getElementById('itemMaintCat').value = s.category;
  document.getElementById('itemMaintKm').value = s.interval_km ?? '';
  document.getElementById('itemMaintMonths').value = s.interval_months ?? '';
}

/** One line per intervention listing the parts that were fitted ("Michelin 235/55 R19 DOT 1224"). */
function partsUsedHtml(parts) {
  if (!parts || !parts.length) return '';
  const label = (p) => [p.brand, p.name && p.name !== p.category ? p.name : null, p.reference ? t('ref. marca {p0}', {p0: p.reference}) : null, p.oem_reference ? t('ref. fab. {p0}', {p0: p.oem_reference}) : null, p.spec, p.attrs?.dot ? `DOT ${p.attrs.dot}` : null].filter(Boolean).join(' ');
  return `<span class="maint-parts">Piezas: ${parts.map((p) => escapeHtml(label(p) || (partCat(p.category)?.label ?? p.category))).join(' · ')}</span>`;
}

async function loadMaintenanceLogs() {
  if (!currentVin) return;
  const tbody = document.getElementById('maintLogsTableBody');
  tbody.innerHTML = '<tr><td class="u-text-align-center" colspan="8">Cargando historial...</td></tr>';

  try {
    const res = await fetch(`/api/vehicles/${currentVin}/maintenance/logs`);
    const logs = await res.json();

    if (!logs || logs.length === 0) {
      tbody.innerHTML = '<tr><td class="u-text-align-center_color-var-text-muted" colspan="8">No hay intervenciones registradas en el historial.</td></tr>';
      return;
    }

    tbody.innerHTML = '';
    logs.forEach(l => {
      const tr = document.createElement('tr');
      const dt = serverDate(l.service_date).toLocaleDateString(I18n.locale, { day: '2-digit', month: '2-digit', year: 'numeric' });
      
      let invHtml = '';
      if (l.has_invoice) {
        const isPdf = (l.invoice_content_type || '').includes('pdf') || (l.invoice_original_name || '').toLowerCase().endsWith('.pdf');
        const fileIcon = icon(isPdf ? 'file' : 'image', 16);
        const rawName = l.invoice_original_name || 'Factura';
        // Text that goes inside an inline `onclick="f(…)"`: first a JS string literal, then escaped for the HTML attribute.
        const jsArg = (v) => escapeHtml(JSON.stringify(String(v ?? '')));
        const cType = l.invoice_content_type || '';

        invHtml = `
          <div class="u-display-flex_gap-0p35rem_justify-content-center_align-items-center">
            <button class="btn-sm btn-info u-display-inline-flex_align-items-center_gap-0p3rem_padding-0p25rem-0p55rem_font-size-0p78rem_font-weight-500" data-on-click="openInvoiceViewer(${l.id}, ${jsArg(rawName)}, ${jsArg(cType)}, ${jsArg(l.item_name)})" title="Ver / Descargar factura">
              <span>${fileIcon}</span>
              <span class="u-max-width-110px_overflow-hidden_text-overflow-ellipsis_white-space-nowrap">${escapeHtml(rawName)}</span>
            </button>
            <button class="btn-sm u-padding-0p2rem-0p45rem_background-rgba-255-61-0-0p15_color-var-accent-red_border-1px-solid-rgba-255-61-0-0p3_border-radius-4px_font-size-0p75rem" data-on-click="deleteMaintenanceInvoice(${l.id})" title="Eliminar factura">✕</button>
          </div>
        `;
      } else {
        invHtml = `
          <div class="u-text-align-center">
            <button class="btn-sm btn-secondary u-font-size-0p75rem_padding-0p25rem-0p6rem_opacity-0p85_display-inline-flex_align-items-center_gap-0p3rem_margin-0-auto" data-on-click="triggerAttachInvoice(${l.id})" title="Adjuntar factura en PDF o Imagen a este registro">
              <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M21.44 11.05l-9.19 9.19a6 6 0 0 1-8.49-8.49l9.19-9.19a4 4 0 0 1 5.66 5.66l-9.2 9.19a2 2 0 0 1-2.83-2.83l8.49-8.48"></path></svg>
              <span>+ Adjuntar</span>
            </button>
          </div>
        `;
      }

      tr.innerHTML = `
        <td><strong>${dt}</strong></td>
        <td><strong>${escapeHtml(l.item_name)}</strong></td>
        <td><span class="maint-category-badge">${escapeHtml(l.category)}</span></td>
        <td>${Math.round(l.odometer).toLocaleString()} km</td>
        <td><strong>${l.cost ? l.cost.toFixed(2) + ' €' : '--'}</strong></td>
        <td>${escapeHtml(l.workshop)}</td>
        <td>${escapeHtml(l.notes)}${partsUsedHtml(l.parts_used)}</td>
        <td>${invHtml}</td>
      `;
      tbody.appendChild(tr);
    });
  } catch (e) {
    tbody.innerHTML = `<tr><td class="u-color-var-accent-red_text-align-center" colspan="8">Error al cargar registro: ${escapeHtml(e)}</td></tr>`;
  }
}

function openCompleteMaintModal(itemId, itemName) {
  document.getElementById('completeMaintItemId').value = itemId;
  document.getElementById('completeMaintTitle').innerText = `Registrar: ${itemName}`;
  document.getElementById('completeMaintOdo').value = '';
  document.getElementById('completeMaintCost').value = '';
  document.getElementById('completeMaintWorkshop').value = '';
  document.getElementById('completeMaintNotes').value = '';
  const invInput = document.getElementById('completeMaintInvoice');
  if (invInput) invInput.value = '';
  mountPartsPicker('completeMaintPartsBox');
  document.getElementById('completeMaintModal').classList.add('open');
}

function closeCompleteMaintModal() {
  document.getElementById('completeMaintModal').classList.remove('open');
}

async function submitCompleteMaint() {
  const itemId = document.getElementById('completeMaintItemId').value;
  if (!itemId) return;

  const odo = parseFloat(document.getElementById('completeMaintOdo').value);
  const cost = parseFloat(document.getElementById('completeMaintCost').value) || 0.0;
  const workshop = document.getElementById('completeMaintWorkshop').value.trim();
  const notes = document.getElementById('completeMaintNotes').value.trim();
  const fileInput = document.getElementById('completeMaintInvoice');
  const selectedInvoiceFile = fileInput && fileInput.files && fileInput.files[0];

  try {
    const res = await fetch(`/api/maintenance/${itemId}/complete`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        odometer: isNaN(odo) ? null : odo,
        cost: cost,
        workshop: workshop,
        notes: notes,
        ...collectParts('completeMaintPartsBox'),
      })
    });
    if (res.ok) {
      const data = await res.json();
      if (selectedInvoiceFile && data.log_id) {
        try {
          await uploadMaintenanceInvoiceFile(data.log_id, selectedInvoiceFile);
        } catch (invErr) {
          showToast(`Intervención guardada, pero la factura no se pudo adjuntar: ${invErr.message}`, 'warning', 7000);
        }
      }
      if (fileInput) fileInput.value = '';
      closeCompleteMaintModal();
      loadMaintenance();
      loadMaintenanceLogs();
    } else {
      showToast('No se pudo guardar la intervención.', 'error');
    }
  } catch (e) {
    showToast(`Error de red: ${e}`, 'error');
  }
}

function showAddMaintenanceModal() {
  document.getElementById('itemMaintTitle').innerText = 'Nueva tarea de mantenimiento';
  document.getElementById('itemMaintId').value = '';
  document.getElementById('itemMaintName').value = '';
  document.getElementById('itemMaintCat').value = 'General';
  document.getElementById('itemMaintKm').value = '15000';
  document.getElementById('itemMaintMonths').value = '12';
  document.getElementById('itemMaintLastOdo').value = '';
  document.getElementById('itemMaintLastDate').value = '';
  document.getElementById('itemMaintNotes').value = '';
  document.getElementById('itemMaintModal').classList.add('open');
}

function showEditMaintenanceModal(itemId) {
  const item = (maintenancePlan?.custom.items || []).find(i => i.id === itemId);
  if (!item) return;
  showAddMaintenanceModal();
  document.getElementById('itemMaintTitle').innerText = 'Editar tarea';
  document.getElementById('itemMaintId').value = item.id;
  document.getElementById('itemMaintName').value = item.name;
  document.getElementById('itemMaintCat').value = item.category;
  document.getElementById('itemMaintKm').value = item.interval_km ?? '';
  document.getElementById('itemMaintMonths').value = item.interval_months ?? '';
  document.getElementById('itemMaintNotes').value = item.notes || '';
}

function closeItemMaintModal() {
  document.getElementById('itemMaintModal').classList.remove('open');
}

/** Creates a custom task or, if the form carries an id, saves the changes to an existing one. */
async function saveMaintItem() {
  if (!currentVin) return;
  const name = document.getElementById('itemMaintName').value.trim();
  if (!name) { showToast('Escribe un nombre para la tarea.', 'warning'); return; }
  const id = document.getElementById('itemMaintId').value;
  const num = (x) => { const v = parseFloat(document.getElementById(x).value); return isNaN(v) ? null : v; };
  const km = num('itemMaintKm');
  const months = num('itemMaintMonths');
  if (km === null && months === null) { showToast('Indica un intervalo en kilómetros, en meses o en ambos.', 'warning'); return; }
  const base = {
    name, category: document.getElementById('itemMaintCat').value,
    interval_km: km, interval_months: months === null ? null : Math.round(months),
    notes: document.getElementById('itemMaintNotes').value.trim(),
  };
  const lastDate = document.getElementById('itemMaintLastDate').value;
  try {
    const res = id
      ? await fetch(`/api/maintenance/${id}`, { method: 'PUT', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(base) })
      : await fetch(`/api/vehicles/${currentVin}/maintenance`, { method: 'POST', headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ ...base, last_service_odometer: num('itemMaintLastOdo'),
                                 last_service_date: lastDate ? serverDate(lastDate).toISOString() : null }) });
    if (!res.ok) throw new Error((await res.json()).detail || res.status);
    closeItemMaintModal();
    showToast(id ? 'Tarea actualizada' : 'Tarea añadida', 'success');
    loadMaintenance();
  } catch (e) {
    showToast(`No se pudo guardar la tarea: ${e.message || e}`, 'error');
  }
}

async function deleteMaintItem(itemId) {
  if (!await confirmDialog('¿Eliminar esta tarea? El historial de lo ya registrado se conserva.', { confirmText: 'Borrar', danger: true })) return;
  try {
    const res = await fetch(`/api/maintenance/${itemId}`, { method: 'DELETE' });
    if (!res.ok) throw new Error(res.status);
    loadMaintenance();
  } catch (e) {
    showToast(`No se pudo eliminar: ${e.message || e}`, 'error');
  }
}


// ───────────────────────── ITV (counted from the registration date) ─────────────────────────
function renderItv(itv) {
  const card = document.getElementById('maintItvCard');
  if (!card || !itv) return;
  card.dataset.status = itv.status;
  const pill = { ok: 'Al día', warning: 'Toca pronto', due: 'Vencida', unknown: 'Sin fecha' }[itv.status];
  document.getElementById('maintItvPill').textContent = pill;
  const day = (iso) => serverDate(iso).toLocaleDateString(I18n.locale, { day: '2-digit', month: 'long', year: 'numeric' });
  const title = document.getElementById('maintItvTitle');
  const sub = document.getElementById('maintItvSub');
  if (itv.status === 'unknown') {
    title.textContent = 'ITV: falta la fecha de matriculación';
    sub.innerHTML = 'La ITV cuenta desde la matriculación. <button type="button" class="maint-link" data-on-click="openVehicleSettings()">Indícala en la ficha del coche</button>.';
  } else {
    title.textContent = `Próxima ITV: ${day(itv.next_due_date)}`;
    const left = itv.remaining_days;
    sub.textContent = (left <= 0 ? `Vencida desde hace ${Math.abs(left)} días. ` : `Faltan ${left} días. `) + ({
      valid_until: 'Según la pegatina de tu última ITV.',
      calculated: `Calculada desde tu última ITV (${day(itv.last_date)}).`,
      registration: `Calculada desde la matriculación (${day(itv.registration_date)}).`,
    }[itv.basis] || '');
  }
  document.getElementById('maintItvRule').textContent = itv.rule + ' Es una estimación: manda la fecha de tu pegatina.';
}

function showItvModal() {
  document.getElementById('itvDate').value = new Date().toISOString().split('T')[0];
  for (const id of ['itvValidUntil', 'itvCost', 'itvKm', 'itvWorkshop', 'itvInvoice']) document.getElementById(id).value = '';
  document.getElementById('maintItvModal').classList.add('open');
}
function closeItvModal() { document.getElementById('maintItvModal').classList.remove('open'); }

async function postWithInvoice(url, body, fileInputId, doneMessage) {
  const res = await fetch(url, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) });
  const data = await res.json().catch(() => ({}));
  if (!res.ok) throw new Error(Array.isArray(data.detail) ? data.detail.map((d) => d.msg).join(' · ') : (data.detail || res.status));
  const file = document.getElementById(fileInputId)?.files?.[0];
  if (file && data.log_id) {
    try { await uploadMaintenanceInvoiceFile(data.log_id, file); }
    catch (invErr) { showToast(`Guardado, pero no se pudo adjuntar el justificante: ${invErr.message}`, 'warning', 6000); }
  }
  showToast(doneMessage, 'success');
  loadMaintenance();
  loadMaintenanceLogs();
  if (typeof loadCosts === 'function') loadCosts();
  return data;
}

async function submitItv() {
  const day = (id) => { const v = document.getElementById(id).value; return v ? `${v}T00:00:00` : null; };
  const km = parseFloat(document.getElementById('itvKm').value);
  try {
    await postWithInvoice(`/api/vehicles/${currentVin}/maintenance/itv/complete`, {
      service_date: day('itvDate'), valid_until: day('itvValidUntil'), odometer: isNaN(km) ? null : km,
      cost: parseFloat(document.getElementById('itvCost').value) || 0, workshop: document.getElementById('itvWorkshop').value.trim() || null,
    }, 'itvInvoice', 'ITV registrada');
    closeItvModal();
  } catch (e) {
    showToast(`No se pudo registrar la ITV: ${e.message || e}`, 'error', 6000);
  }
}

// ───────────────────────── One-off interventions ─────────────────────────
// [what was done, part category it usually involves]
const EVENT_IDEAS = [['Pastillas de freno', 'brake_pads'], ['Discos de freno', 'brake_discs'], ['Reparación de rueda', null], ['Neumáticos nuevos', 'tyres'],
  ['Batería de 12 V', 'battery_12v'], ['Escobillas', 'wiper_blades'], ['Amortiguadores', null], ['Alineación', null]];

function showMaintEventModal() {
  for (const id of ['evTitle', 'evKm', 'evCost', 'evWorkshop', 'evNotes', 'evInvoice']) document.getElementById(id).value = '';
  document.getElementById('evDate').value = new Date().toISOString().split('T')[0];
  document.getElementById('evCat').value = 'General';
  document.getElementById('evChips').innerHTML = EVENT_IDEAS.map(([t, c]) => `<button type="button" class="maint-chip" data-on-click="pickEventIdea('${t}', ${c ? `'${c}'` : 'null'})">${t}</button>`).join('');
  mountPartsPicker('evPartsBox');
  document.getElementById('maintEventModal').classList.add('open');
}
/** A suggested intervention fills the title and, if it involves a known part, offers a row for it (once). */
function pickEventIdea(title, category) {
  document.getElementById('evTitle').value = title;
  const rows = document.querySelector('#evPartsBox [data-rows]');
  if (category && rows && ![...rows.querySelectorAll('[data-f=category]')].some((s) => s.value === category)) addPickerRow('evPartsBox', category);
}
function closeMaintEventModal() { document.getElementById('maintEventModal').classList.remove('open'); }

async function submitMaintEvent() {
  const title = document.getElementById('evTitle').value.trim();
  if (title.length < 2) { showToast('Escribe qué se hizo.', 'warning'); return; }
  const date = document.getElementById('evDate').value;
  const km = parseFloat(document.getElementById('evKm').value);
  try {
    await postWithInvoice(`/api/vehicles/${currentVin}/maintenance/events`, {
      title, category: document.getElementById('evCat').value, service_date: date ? `${date}T00:00:00` : null,
      odometer: isNaN(km) ? null : km, cost: parseFloat(document.getElementById('evCost').value) || 0,
      workshop: document.getElementById('evWorkshop').value.trim() || null, notes: document.getElementById('evNotes').value.trim() || null,
      ...collectParts('evPartsBox'),
    }, 'evInvoice', 'Intervención registrada');
    closeMaintEventModal();
  } catch (e) {
    showToast(`No se pudo registrar: ${e.message || e}`, 'error', 6000);
  }
}
