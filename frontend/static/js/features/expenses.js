/* EVBro — Expenses (Más → Gastos): what is paid that is not maintenance, charging or fuel: insurance, taxes, garage, tolls, fines…
 * Refuels have their own section (features/refuels.js); the overall balance is in Costes (read only).
 * (Classic script: shares the global scope with the other files; see the load order in templates/index.html.)
 */

let expensesData = [];
let expenseCategories = [];
let expensesPicker = null;
const expensesPickerOf = () => expensesPicker || (expensesPicker = PeriodPicker.create('exPeriod', { storageKey: 'evbro_period_expenses', presets: ['all', 'year', 'last_year', 'month'], onChange: loadExpenses }));
let expensesCat = 'all';

const exEl = (id) => document.getElementById(id);
const exMoney = (n) => `${(n || 0).toLocaleString(I18n.locale, { minimumFractionDigits: 2, maximumFractionDigits: 2 })} €`;
const exDay = (iso) => serverDate(iso).toLocaleDateString(I18n.locale);
const EXPENSE_NAME_HINTS = {
  insurance: ['Seguro anual', 'Seguro trimestral'], tax: ['Impuesto de circulación (IVTM)'], parking: ['Garaje mensual', 'Parking'],
  tolls: ['Peaje'], fines: ['Multa'], wash: ['Lavado'], accessories: ['Alfombrillas', 'Cable de carga'], other: [],
};

async function loadExpenses() {
  if (!currentVin || !exEl('tabExpenses')) return;
  try {
    const [e, c] = await Promise.all([
      fetch(`/api/vehicles/${currentVin}/expenses?${expensesPickerOf().query()}`).then((x) => x.json()),
      expenseCategories.length ? Promise.resolve(expenseCategories) : fetch('/api/expense-categories').then((x) => x.json()),
    ]);
    expensesData = e; expenseCategories = c;
    renderExpenses();
  } catch (err) {
    console.warn('Could not load the expenses:', err);
  }
}

function setExpenseCat(c) { expensesCat = c; renderExpenses(); }

/** Due dates: the insurance renewal from the car sheet and the latest expense of each item with a «next time» (same rule as the server). */
function pendingDue() {
  const out = [];
  const veh = (typeof currentVehicle === 'function') ? currentVehicle() : null;
  if (veh?.insurance_renewal_date) out.push({ label: 'Renovación del seguro', due: veh.insurance_renewal_date, id: null });
  const latest = {};
  for (const e of expensesData) {
    if (!e.next_due_date) continue;
    const k = `${e.category}:${e.title.trim().toLowerCase()}`;
    if (!latest[k] || e.date > latest[k].date) latest[k] = e;
  }
  for (const e of Object.values(latest)) {
    if (e.category === 'insurance' && veh?.insurance_renewal_date) continue;
    out.push({ label: e.title, due: e.next_due_date, id: e.id });
  }
  return out.sort((a, b) => a.due.localeCompare(b.due));
}

function renderExpenses() {
  // due dates
  const due = pendingDue();
  exEl('exDueCard').hidden = !due.length;
  const today = new Date(); today.setHours(0, 0, 0, 0);
  exEl('exDueList').innerHTML = due.map((d) => {
    const days = Math.round((new Date(d.due.slice(0, 10)) - today) / 86400000);
    const tone = days < 0 ? 'overdue' : days <= 7 ? 'urgent' : days <= 30 ? 'soon' : 'ok';
    const txt = days < 0 ? t('Venció hace {p0} días', {p0: -days}) : days === 0 ? 'Vence hoy' : t('Faltan {p0} días', {p0: days});
    const tag = d.id ? 'button' : 'div';
    return `<${tag} type="button" class="ex-due-row" data-tone="${tone}" ${d.id ? `onclick="openExpenseModal(${d.id})"` : ''}>
      <div class="ex-row-main"><strong>${escapeHtml(d.label)}</strong><small>${exDay(d.due)}</small></div><span class="ex-due-chip">${txt}</span></${tag}>`;
  }).join('');

  // filters: the server applies the period (shared picker); here only the categories
  exEl('exChips').innerHTML = [{ key: 'all', label: 'Todas' }, ...expenseCategories].map((c) =>
    `<button type="button" class="ex-chip ${expensesCat === c.key ? 'is-active' : ''}" data-on-click="setExpenseCat('${c.key}')">${escapeHtml(c.label)}</button>`).join('');

  const rows = expensesData.filter((e) => expensesCat === 'all' || e.category === expensesCat);
  exEl('exTotal').textContent = exMoney(rows.reduce((a, e) => a + e.amount, 0));
  exEl('exList').innerHTML = rows.length ? rows.map((e) => `
    <button type="button" class="costs-row costs-row-btn" data-on-click="openExpenseModal(${e.id})">
      <div class="costs-row-main"><strong>${escapeHtml(e.title)}</strong><small>${exDay(e.date)} · ${escapeHtml(e.category_label)}${e.next_due_date ? ` · próxima ${exDay(e.next_due_date)}` : ''}</small></div>
      <span class="costs-row-amount">${exMoney(e.amount)}</span>
    </button>`).join('') : '<div class="costs-empty">No hay gastos con este filtro. Añade el primero con «Añadir gasto».</div>';

}

// ───────────────────────── Expenses ─────────────────────────
function fillNameHints(cat) {
  exEl('exNameList').innerHTML = (EXPENSE_NAME_HINTS[cat] || []).map((n) => `<option value="${escapeHtml(n)}"></option>`).join('');
}

function openExpenseModal(id) {
  const e = id ? expensesData.find((x) => x.id === id) : null;
  exEl('exId').value = e ? e.id : '';
  exEl('expenseTitle').textContent = e ? 'Editar gasto' : 'Añadir gasto';
  exEl('exCategory').innerHTML = expenseCategories.map((c) => `<option value="${c.key}">${escapeHtml(c.label)}</option>`).join('');
  exEl('exCategory').value = e ? e.category : 'tax';
  exEl('exDate').value = (e ? e.date : new Date().toISOString()).slice(0, 10);
  exEl('exName').value = e ? e.title : '';
  exEl('exAmount').value = e ? e.amount : '';
  exEl('exDue').value = e && e.next_due_date ? e.next_due_date.slice(0, 10) : '';
  exEl('exNotes').value = e ? e.notes || '' : '';
  exEl('exDelete').hidden = !e;
  fillNameHints(exEl('exCategory').value);
  exEl('expenseModal').classList.add('open');
}
function closeExpenseModal() { exEl('expenseModal').classList.remove('open'); }

async function saveExpense() {
  const id = exEl('exId').value;
  const amount = parseFloat(exEl('exAmount').value.replace(',', '.'));
  const title = exEl('exName').value.trim();
  if (!title) { showToast('Indica el concepto.', 'warning'); return; }
  if (isNaN(amount) || amount < 0) { showToast('Indica el importe.', 'warning'); return; }
  const date = exEl('exDate').value;
  const due = exEl('exDue').value;
  if (due && date && due <= date) { showToast('La próxima fecha debe ser posterior a la del gasto.', 'warning', 5000); return; }
  const body = { category: exEl('exCategory').value, title, amount, notes: exEl('exNotes').value.trim() || null,
    date: date ? `${date}T12:00:00` : undefined, next_due_date: due ? `${due}T12:00:00` : null };
  try {
    const res = await fetch(id ? `/api/expenses/${id}` : `/api/vehicles/${currentVin}/expenses`, {
      method: id ? 'PUT' : 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) });
    const data = await res.json().catch(() => ({}));
    if (!res.ok) throw new Error(Array.isArray(data.detail) ? data.detail.map((d) => d.msg).join(' · ') : (data.detail || res.status));
    closeExpenseModal();
    showToast('Gasto guardado', 'success');
    loadExpenses();
  } catch (e) {
    showToast(t('No se pudo guardar: {p0}', {p0: e.message || e}), 'error', 6000);
  }
}

async function deleteExpense() {
  const id = exEl('exId').value;
  if (!id || !await confirmDialog('¿Borrar este gasto?', { confirmText: 'Borrar', danger: true })) return;
  const res = await fetch(`/api/expenses/${id}`, { method: 'DELETE' });
  if (res.ok) { closeExpenseModal(); loadExpenses(); }
}

document.addEventListener('DOMContentLoaded', () => {
  exEl('exCategory')?.addEventListener('change', (e) => fillNameHints(e.target.value));
});
