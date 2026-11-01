/* EVBro — Maintenance: official EBRO plan (next service, full plan and logging of steps).
 * The server computes everything (services/maintenance_official.py); here it is only drawn and the actions requested.
 * (Classic script: shares the global scope with the other files; see the load order in templates/index.html.)
 */

const fmtKm = (n) => Math.round(n).toLocaleString(I18n.locale);
const fmtDate = (iso) => serverDate(iso).toLocaleDateString(I18n.locale, { day: '2-digit', month: 'long', year: 'numeric' });

function renderOfficialPlan(plan) {
  renderBaseline(plan);
  const next = plan.official.next;
  const card = document.getElementById('maintNextCard');
  const stepsCard = document.getElementById('maintSteps');

  if (plan.official.finished || !next) {
    card.dataset.status = 'ok';
    document.getElementById('maintNextPill').textContent = 'Plan completado';
    document.getElementById('maintNextTitle').textContent = 'Has hecho todas las revisiones de la pauta oficial';
    document.getElementById('maintNextLabor').textContent = '';
    card.querySelector('.maint-meters').hidden = true;
    card.querySelector('.maint-ops').hidden = true;
    card.querySelector('.maint-next-rule').textContent = 'A partir de 90.000 km o 72 meses consulta el plan de tu concesionario o pasa al plan personalizado.';
    document.getElementById('btnRegisterRevision').hidden = true;
    document.getElementById('maintNextNotes').hidden = true;
  } else {
    card.querySelector('.maint-meters').hidden = false;
    card.querySelector('.maint-ops').hidden = false;
    card.querySelector('.maint-next-rule').textContent = 'Vence lo que antes llegue: los kilómetros o la fecha.';
    document.getElementById('btnRegisterRevision').hidden = false;
    card.dataset.status = next.status;
    document.getElementById('maintNextPill').textContent = { ok: 'Próxima revisión', warning: 'Toca pronto', due: 'Vencida' }[next.status];
    document.getElementById('maintNextTitle').textContent = t('Revisión de {p0}', {p0: next.label});
    document.getElementById('maintNextLabor').textContent = t('{p0} h de mano de obra', {p0: String(next.labor_hours).replace('.', ',')});

    // Kilometres
    const kmOver = next.remaining_km <= 0;
    document.getElementById('maintMeterKmText').textContent = kmOver
      ? t('Pasada por {p0} km', {p0: fmtKm(Math.abs(next.remaining_km))}) : t('Faltan {p0} km', {p0: fmtKm(next.remaining_km)});
    document.getElementById('maintMeterKmSub').textContent = t('A los {p0} km (llevas {p1})', {p0: fmtKm(next.km), p1: fmtKm(plan.vehicle.odometer)});
    setMeter('maintMeterKmBar', next.progress_km_pct, kmOver ? 'due' : (next.remaining_km <= 1500 ? 'warning' : 'ok'));

    // Date (only if there is a delivery date)
    const dateBox = document.getElementById('maintMeterDateBox');
    if (next.due_date === null) {
      dateBox.hidden = true;
    } else {
      dateBox.hidden = false;
      const dOver = next.remaining_days <= 0;
      document.getElementById('maintMeterDateText').textContent = dOver
        ? t('Pasada por {p0} días', {p0: Math.abs(next.remaining_days)}) : t('Faltan {p0} días', {p0: next.remaining_days});
      document.getElementById('maintMeterDateSub').textContent = t('Vence el {p0} ({p1} meses)', {p0: fmtDate(next.due_date), p1: next.months});
      setMeter('maintMeterDateBar', next.progress_time_pct, dOver ? 'due' : (next.remaining_days <= 30 ? 'warning' : 'ok'));
    }

    const notes = document.getElementById('maintNextNotes');
    notes.hidden = !next.extra_notes.length;
    notes.textContent = next.extra_notes.join(' · ');
    document.getElementById('checklistReplaceList').innerHTML = opsList(next.replace_items, 'R', 'Nada que reemplazar en esta revisión.');
    document.getElementById('checklistInspectList').innerHTML = opsList(next.inspect_items, 'I', 'Nada que inspeccionar en esta revisión.');
  }

  // Steps: done / next / pending
  stepsCard.innerHTML = plan.official.matrix.columns.map(c => {
    const done = plan.official.history.find(h => h.step_km === c.km);
    const state = c.is_done ? 'done' : (c.is_next ? 'next' : 'todo');
    const label = { done: done ? `Hecha ${serverDate(done.date).toLocaleDateString(I18n.locale)}` : 'Hecha', next: 'Siguiente', todo: 'Pendiente' }[state];
    return `<div class="maint-step ${state}"><strong>${c.km_label} km</strong><span>${c.months} meses</span><small>${label}</small></div>`;
  }).join('');
  renderEbroMatrix(plan);
}

function setMeter(id, pct, status) {
  const el = document.getElementById(id);
  if (!el) return;
  el.style.width = `${Math.max(2, Math.min(100, pct ?? 0))}%`;
  el.className = `maint-bar-fill ${status}`;
}

function opsList(items, badge, empty) {
  if (!items.length) return `<div class="maint-ops-empty">${empty}</div>`;
  return items.map(i => `
    <div class="check-item-row">
      <div class="check-item-info"><span class="check-item-name">${escapeHtml(i.name)}</span><span class="check-item-spec">${escapeHtml(i.spec)}</span></div>
      <span class="${badge === 'R' ? 'badge-r' : 'badge-i'}">${badge}</span>
    </div>`).join('');
}

// ───────────────────────── Delivery date (set in the car sheet) ─────────────────────────
function renderBaseline(plan) {
  const configured = plan.baseline.configured;
  document.getElementById('maintBaselineTitle').textContent = configured
    ? t('Fecha de entrega: {p0}', {p0: fmtDate(plan.baseline.date)}) : 'Falta la fecha de entrega del coche';
  document.getElementById('maintBaselineHint').textContent = configured
    ? 'Los plazos en meses de la pauta (12, 24, 36…) se cuentan desde ella.'
    : 'Los plazos en meses de la pauta (12, 24, 36…) se cuentan desde ella. Sin la fecha solo se vigilan los kilómetros.';
  document.getElementById('maintBaselineCard').classList.toggle('needs-setup', !configured);
}

// ───────────────────────── Brand's full table ─────────────────────────
function renderEbroMatrix(plan) {
  const table = document.getElementById('ebroMatrixTable');
  if (!table || !plan) return;
  const { columns: cols, rows } = plan.official.matrix;
  const cls = (c) => (c.is_next ? 'col-active' : (c.is_done ? 'col-done' : ''));
  let html = '<thead><tr><th class="u-text-align-left">Meses</th>' + cols.map(c => `<th class="${cls(c)}">${c.months}</th>`).join('') + '</tr>';
  html += '<tr><th class="u-text-align-left">x 1000 km</th>' + cols.map(c => `<th class="${cls(c)}">${c.km / 1000}</th>`).join('') + '</tr></thead><tbody>';
  html += '<tr class="matrix-labor-row"><td class="col-op-name">Mano de obra (tu modelo)</td>' + cols.map(c => `<td class="${cls(c)}">${String(c.labor_hours).replace(".", ",")} h</td>`).join('') + '</tr>';
  rows.forEach(r => {
    html += `<tr><td class="col-op-name">${escapeHtml(r.name)}</td>` + cols.map(c => {
      const op = r.operations[c.km] || '-';
      const cell = op === 'R' ? '<span class="badge-r">R</span>' : (op === 'I' ? '<span class="badge-i">I</span>' : '-');
      return `<td class="${cls(c)}">${cell}</td>`;
    }).join('') + '</tr>';
  });
  table.innerHTML = html + '</tbody>';
}

// ───────────────────────── Log an official service ─────────────────────────
function showRegisterOfficialRevisionModal() {
  const plan = maintenancePlan;
  if (!plan) return;
  const pending = plan.official.matrix.columns.filter(c => !c.is_done);
  if (!pending.length) { showToast('Ya están registradas todas las revisiones del plan.', 'info'); return; }
  const sel = document.getElementById('regRevInterval');
  sel.innerHTML = pending.map(c => `<option value="${c.km}">${escapeHtml(c.label)}</option>`).join('');
  sel.value = plan.official.next ? plan.official.next.km : pending[0].km;
  onRevisionIntervalSelectChange(sel.value);
  document.getElementById('regRevOdo').value = plan.vehicle.odometer ? plan.vehicle.odometer.toFixed(1) : '';
  document.getElementById('regRevDate').value = new Date().toISOString().split('T')[0];
  document.getElementById('regRevCost').value = '';
  document.getElementById('regRevNotes').value = '';
  const inv = document.getElementById('regRevInvoice');
  if (inv) inv.value = '';
  mountPartsPicker('regRevPartsBox');
  document.getElementById('registerOfficialRevisionModal').classList.add('open');
}

function closeRegisterOfficialRevisionModal() {
  document.getElementById('registerOfficialRevisionModal').classList.remove('open');
}

function onRevisionIntervalSelectChange(kmVal) {
  const km = parseInt(kmVal, 10);
  const notes = document.getElementById('regRevNotes');
  if (!notes) return;
  const hints = {
    15000: 'Aceite 0W-20 y filtro de aceite sustituidos.',
    30000: 'Incluye líquido de frenos y filtro de habitáculo.',
    45000: 'Incluye bujías de encendido y filtro de aire del motor.',
    60000: 'Incluye anticongelante y aceite de transmisión DCT/HCT con su filtro.',
    75000: 'Aceite y filtro de aceite sustituidos.',
    90000: 'Incluye frenos, filtros de habitáculo y de aire y bujías.',
  };
  notes.placeholder = hints[km] || '';
}

async function submitOfficialRevision() {
  if (!currentVin) return;
  const stepKm = parseInt(document.getElementById('regRevInterval').value, 10);
  const odo = parseFloat(document.getElementById('regRevOdo').value);
  const serviceDate = document.getElementById('regRevDate').value;
  const invoice = document.getElementById('regRevInvoice')?.files?.[0];
  try {
    const res = await fetch(`/api/vehicles/${currentVin}/maintenance/official/complete`, {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        step_km: stepKm,
        odometer: isNaN(odo) ? null : odo,
        cost: parseFloat(document.getElementById('regRevCost').value) || 0.0,
        workshop: document.getElementById('regRevWorkshop').value.trim() || 'Taller Oficial Ebro',
        notes: document.getElementById('regRevNotes').value.trim(),
        service_date: serviceDate ? serverDate(serviceDate).toISOString() : null,
        ...collectParts('regRevPartsBox'),
      }),
    });
    const data = await res.json().catch(() => ({}));
    if (!res.ok) throw new Error(data.detail || res.status);
    if (invoice && data.log_id) {
      try { await uploadMaintenanceInvoiceFile(data.log_id, invoice); }
      catch (invErr) { showToast(t('Revisión guardada, pero no se pudo adjuntar la factura: {p0}', {p0: invErr.message}), 'warning', 6000); }
    }
    closeRegisterOfficialRevisionModal();
    showToast('Revisión registrada: el plan pasa a la siguiente', 'success');
    loadMaintenance();
    loadMaintenanceLogs();
  } catch (e) {
    showToast(t('No se pudo registrar: {p0}', {p0: e.message || e}), 'error');
  }
}
