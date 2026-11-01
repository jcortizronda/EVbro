/* EVBro — Costs (Más → Costes): the balance of everything the car costs. Read only.
 * Adds up purchase, charges, refuels, maintenance and expenses (api/routes_costs.py computes the totals; here they are only drawn).
 * To add something: expenses in Gastos, refuels in Repostajes, charges in Energía, interventions in Mantenimiento.
 * (Classic script: shares the global scope with the other files; see the load order in templates/index.html.)
 */

let costsPicker = null;
const costsPickerOf = () => costsPicker || (costsPicker = PeriodPicker.create('costsPeriod', { storageKey: 'evbro_period_costs', presets: ['all', 'year', 'last_year', '12m', 'month'], onChange: loadCosts }));
let costsData = null;

const cEl = (id) => document.getElementById(id);
const money = (n) => `${(n || 0).toLocaleString(I18n.locale, { minimumFractionDigits: 2, maximumFractionDigits: 2 })} €`;
const moneyShort = (n) => `${Math.round(n || 0).toLocaleString(I18n.locale)} €`;
const monthLabel = (ym) => { const [y, m] = ym.split('-'); return new Date(+y, +m - 1, 1).toLocaleDateString(I18n.locale, { month: 'short' }).replace('.', ''); };

function setCostsPeriod(period) {
  costsPickerOf().set({ period });                                    // the picker calls loadCosts()
}

async function loadCosts() {
  if (!currentVin || !cEl('tabCosts')) return;
  try {
    costsData = await fetch(`/api/vehicles/${currentVin}/costs?${costsPickerOf().query()}`).then((x) => x.json());
    renderCosts();
  } catch (e) {
    console.warn('Could not load the costs:', e);
  }
}

function renderCosts() {
  const d = costsData;
  if (!d) return;
  const tot = d.totals;   // not `t`: that name is the translation function
  const label = costsPickerOf().isEverything() ? 'Total desde la compra' : t('Total · {p0}', {p0: costsPickerOf().label().toLowerCase()});
  cEl('costsHeroLabel').textContent = label;
  cEl('costsTotal').textContent = money(tot.all);
  cEl('costsHeroSub').textContent = tot.purchase ? t('Compra {p0} + uso {p1}', {p0: moneyShort(tot.purchase), p1: moneyShort(tot.running)}) : 'Cargas, repostajes, mantenimiento y gastos';
  cEl('costsRunning').textContent = money(tot.running);
  cEl('costsPer100').textContent = d.per_100km !== null ? money(d.per_100km) : '--';
  cEl('costsKm').textContent = d.km ? `${Math.round(d.km).toLocaleString(I18n.locale)} km` : '--';

  const v = d.vehicle;
  cEl('costsPurchase').textContent = tot.purchase ? money(tot.purchase) : '--';
  cEl('costsPurchaseSub').textContent = v.purchase_price ? (v.purchase_date ? t('Entrega: {p0}', {p0: serverDate(v.purchase_date).toLocaleDateString(I18n.locale)}) : '')
    : 'Indícala en la ficha del coche';
  cEl('costsCharges').textContent = money(tot.charges);
  cEl('costsChargesSub').textContent = t('{p0} kWh a la batería', {p0: String(d.energy.kwh).replace('.', ',')});
  cEl('costsRefuels').textContent = money(tot.refuels);
  cEl('costsRefuelsSub').textContent = `${String(d.energy.liters).replace('.', ',')} L`;
  cEl('costsMaint').textContent = money(tot.maintenance);
  cEl('costsMaintSub').textContent = 'Revisiones, ITV y arreglos';
  cEl('costsExpenses').textContent = money(tot.expenses);
  cEl('costsExpensesSub').textContent = 'Seguro, impuestos, garaje y más';

  const pend = cEl('costsPending');
  const parts = [];
  if (d.pending.charges) parts.push(`${d.pending.charges} carga${d.pending.charges > 1 ? 's' : ''}`);
  if (d.pending.refuels) parts.push(`${d.pending.refuels} repostaje${d.pending.refuels > 1 ? 's' : ''}`);
  pend.hidden = !parts.length;
  if (parts.length) pend.innerHTML = `<strong>${t('{p0} sin precio.', {p0: parts.join(' y ')})}</strong> No cuentan en los totales hasta que los valores (las cargas en Carga, «Editar coste»; los repostajes en Repostajes).`;

  renderCostBars(d.monthly);
  renderCostCategories(d.by_category, tot.expenses);
  cEl('costsRecent').innerHTML = d.recent.length ? d.recent.map((x) => `
    <div class="costs-row">
      <div class="costs-row-main"><strong>${escapeHtml(x.title)}</strong><small>${serverDate(x.date).toLocaleDateString(I18n.locale)} · ${escapeHtml(x.detail)}</small></div>
      <span class="costs-row-amount ${x.pending ? 'is-pending' : ''}">${x.pending ? 'Sin precio' : money(x.amount)}</span>
    </div>`).join('') : '<div class="costs-empty">Todavía no hay movimientos en este periodo.</div>';
}

function renderCostCategories(cats, total) {
  const box = cEl('costsByCat');
  if (!cats || !cats.length) { box.innerHTML = '<div class="costs-empty">Sin gastos registrados en este periodo.</div>'; return; }
  box.innerHTML = cats.map((c) => `
    <div class="costs-cat">
      <div class="costs-cat-head"><span>${escapeHtml(c.label)}</span><strong>${money(c.amount)}</strong></div>
      <div class="costs-cat-track"><i data-css="width:${total ? Math.max(3, (c.amount / total) * 100) : 0}%"></i></div>
    </div>`).join('');
  applyDataStyles(box);
}

function renderCostBars(months) {
  const box = cEl('costsBars');
  if (!months.length) { box.innerHTML = '<div class="costs-empty">Sin gastos registrados en este periodo.</div>'; return; }
  const sum = (m) => m.charges + m.refuels + m.maintenance + m.expenses;
  const max = Math.max(...months.map(sum), 1);
  box.innerHTML = months.map((m) => {
    const h = (v) => `${(v / max) * 100}%`;
    return `<div class="costs-bar" title="${monthLabel(m.month)} ${m.month.slice(0, 4)}: ${money(sum(m))}">
      <span class="costs-bar-total">${moneyShort(sum(m))}</span>
      <div class="costs-bar-stack">
        <i class="c-exp" data-css="height:${h(m.expenses)}"></i><i class="c-maint" data-css="height:${h(m.maintenance)}"></i><i class="c-refuels" data-css="height:${h(m.refuels)}"></i><i class="c-charges" data-css="height:${h(m.charges)}"></i>
      </div>
      <span class="costs-bar-label">${monthLabel(m.month)}</span></div>`;
  }).join('');
  applyDataStyles(box);
}
