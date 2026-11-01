/* EVBro — Timeline (Más → Cronología): every dated event of the car in one list (drives, charges, refuels, workshop, expenses,
 * documents, geofence entries and exits), newest first, with month separators and «Ver más»; and the global search box, which looks
 * through parts, interventions, expenses, documents, refuels and drives of every car the account can see.
 * Tapping a row jumps to the tab that shows the item in full.
 * (Classic script: shares the global scope; history-list.js must load before it.)
 */

const TIMELINE_KINDS = [
  ['', 'Todo'], ['drive', 'Viajes'], ['charge', 'Cargas'], ['refuel', 'Repostajes'], ['maintenance', 'Taller'],
  ['expense', 'Gastos'], ['document', 'Documentos'], ['geofence', 'Geocercas'],
];
const TIMELINE_ICON = { drive: 'car', charge: 'bolt', refuel: 'fuel', maintenance: 'wrench', expense: 'doc', document: 'file', geofence: 'pin', part: 'list' };
const TIMELINE_KIND_LABEL = { drive: 'Viaje', charge: 'Carga', refuel: 'Repostaje', maintenance: 'Taller', expense: 'Gasto', document: 'Documento', geofence: 'Geocerca', part: 'Pieza' };

const timelineState = { kind: '', before: null, lastMonth: null, loading: false, searchTimer: null, query: '' };
let timelinePicker = null;
const timelinePickerOf = () => timelinePicker || (timelinePicker = PeriodPicker.create('tlPeriod', { storageKey: 'evbro_period_timeline', presets: ['all', 'month', 'last_month', 'year', 'last_year'], onChange: () => loadTimeline(true) }));
const tlEl = (id) => document.getElementById(id);

function tlAmount(e) {
  if (e.amount === null || e.amount === undefined) return '';
  const digits = e.amount_unit === '€' ? 2 : 1;
  return `${historyNum(e.amount, digits)} ${e.amount_unit}`;
}

/** One row of the list (timeline event or search hit): kind icon, title, subtitle, date and amount. */
function tlRow(e, withCar) {
  const row = document.createElement('button');
  row.type = 'button';
  row.className = `tl-row tl-kind-${e.kind}`;
  row.setAttribute('data-on-click', `openTimelineItem('${e.kind}', ${Number(e.id)}, '${escapeHtml(e.vin || '')}')`);
  const when = e.timestamp ? historyWhen(e.timestamp) : '';
  const sub = [withCar && e.car ? e.car : null, e.subtitle || null].filter(Boolean).join(' · ');
  row.innerHTML = `<span class="tl-ico" aria-hidden="true">${icon(TIMELINE_ICON[e.kind] || 'list', 16)}</span>
    <span class="tl-main"><strong>${escapeHtml(e.title)}</strong><small>${escapeHtml(sub)}</small></span>
    <span class="tl-side"><b>${escapeHtml(tlAmount(e))}</b><small>${escapeHtml(when)}</small></span>`;
  return row;
}

function renderTimelineChips() {
  const box = tlEl('tlChips');
  if (!box || box.children.length) return;
  box.innerHTML = TIMELINE_KINDS.map(([k, label]) =>
    `<button type="button" class="ex-chip${k === timelineState.kind ? ' is-active' : ''}" data-kind="${k}" data-on-click="setTimelineKind('${k}')">${label}</button>`).join('');
}

function setTimelineKind(kind) {
  timelineState.kind = kind;
  document.querySelectorAll('#tlChips .ex-chip').forEach((b) => b.classList.toggle('is-active', b.dataset.kind === kind));
  loadTimeline(true);
}

async function loadTimeline(reset = true) {
  if (!currentVin || !tlEl('tabTimeline')) return;
  renderTimelineChips();
  if (timelineState.query) { searchTimeline(true); return; }
  const list = tlEl('tlList');
  const more = tlEl('tlMore');
  if (reset) { timelineState.before = null; timelineState.lastMonth = null; list.innerHTML = ''; }
  if (timelineState.loading) return;
  timelineState.loading = true;
  if (more) more.disabled = true;
  try {
    const params = new URLSearchParams({ limit: String(HISTORY_PAGE_SIZE) });
    timelinePickerOf().params(params);
    if (timelineState.kind) params.set('kinds', timelineState.kind);
    if (timelineState.before) params.set('before', timelineState.before);
    const res = await fetch(`/api/vehicles/${currentVin}/timeline?${params}`);
    if (!res.ok) throw new Error(res.status);
    const data = await res.json();
    if (reset && !data.events.length) {
      list.innerHTML = '<div class="hist-empty">Todavía no hay nada apuntado de este coche.</div>';
    }
    for (const e of data.events) {
      historyMaybeMonth(list, timelineState, e.timestamp, () => '');
      list.appendChild(tlRow(e, false));
    }
    if (data.events.length) timelineState.before = data.events[data.events.length - 1].timestamp;
    historyMore(more, data.has_more);
    tlEl('tlTitle').textContent = 'Cronología';
  } catch (err) {
    list.innerHTML = '<div class="hist-empty is-error">No se pudo cargar la cronología.</div>';
    console.warn('timeline:', err);
  } finally {
    timelineState.loading = false;
  }
}

/** Search box: a short pause after typing, then one request; an empty box goes back to the timeline. */
function onTimelineSearchInput() {
  const q = (tlEl('tlSearch').value || '').trim();
  timelineState.query = q;
  clearTimeout(timelineState.searchTimer);
  if (!q) { loadTimeline(true); return; }
  timelineState.searchTimer = setTimeout(() => searchTimeline(false), 300);
}

async function searchTimeline() {
  const q = timelineState.query;
  const list = tlEl('tlList');
  const more = tlEl('tlMore');
  if (more) more.hidden = true;
  if (q.length < 2) { list.innerHTML = '<div class="hist-empty">Escribe al menos dos letras.</div>'; return; }
  try {
    const res = await fetch(`/api/search?q=${encodeURIComponent(q)}`);
    if (!res.ok) throw new Error(res.status);
    const data = await res.json();
    if (data.query !== timelineState.query) return;                  // an older answer arriving late
    timelineState.lastMonth = null;
    list.innerHTML = '';
    tlEl('tlTitle').textContent = t('Resultados de «{p0}»', {p0: data.query});
    if (!data.hits.length) { list.innerHTML = '<div class="hist-empty">Nada coincide en tus coches.</div>'; return; }
    let lastKind = null;
    for (const h of data.hits) {
      if (h.kind !== lastKind) {
        lastKind = h.kind;
        const head = document.createElement('div');
        head.className = 'hist-month';
        head.innerHTML = `<strong>${TIMELINE_KIND_LABEL[h.kind] || h.kind}</strong>`;
        list.appendChild(head);
      }
      list.appendChild(tlRow(h, true));
    }
  } catch (err) {
    list.innerHTML = '<div class="hist-empty is-error">No se pudo buscar.</div>';
    console.warn('search:', err);
  }
}

function clearTimelineSearch() {
  tlEl('tlSearch').value = '';
  timelineState.query = '';
  loadTimeline(true);
}

/** Jumps to the tab that shows the item; a hit of another car switches the car first. */
function openTimelineItem(kind, id, vin) {
  const tab = { drive: 'drives', charge: 'charges', refuel: 'refuels', maintenance: 'maintenance', expense: 'expenses',
                document: 'vehicle', geofence: 'geofences', part: 'vehicle' }[kind] || 'overview';
  if (vin && vin !== currentVin && typeof selectVehicle === 'function') selectVehicle(vin);
  if (tab === 'vehicle' && typeof openVehicleSettings === 'function') openVehicleSettings();
  else switchTab(tab);
}
