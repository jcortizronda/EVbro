/* EVBro — Shared pieces of the trip and charge history lists: one compact row per item that unfolds its details,
 * month separators with totals from the server and a «Ver más» button that loads the next page.
 * (Classic script: shares the global scope; it must load before drives.js and charge-sessions.js.)
 */

const HISTORY_PAGE_SIZE = 30;
const HISTORY_MONTHS = ['Enero', 'Febrero', 'Marzo', 'Abril', 'Mayo', 'Junio', 'Julio', 'Agosto', 'Septiembre', 'Octubre', 'Noviembre', 'Diciembre'];

/** Number in Spanish format with a fixed number of decimals («3,1»). */
function historyNum(value, decimals = 1) {
  return Number(value || 0).toLocaleString(I18n.locale, { minimumFractionDigits: decimals, maximumFractionDigits: decimals });
}

/** «2026-10» of a timestamp, in the browser's local time (the server groups with the configured zone). */
function historyMonthKey(iso) {
  const d = serverDate(iso);
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}`;
}

function historyMonthLabel(key) {
  const [y, m] = key.split('-');
  return `${HISTORY_MONTHS[Number(m) - 1] || m} ${y}`;
}

/** «vie 24 oct · 18:05» */
function historyWhen(iso) {
  const d = serverDate(iso);
  const day = d.toLocaleDateString(I18n.locale, { weekday: 'short', day: 'numeric', month: 'short' });
  const time = d.toLocaleTimeString(I18n.locale, { hour: '2-digit', minute: '2-digit' });
  return `${day} · ${time}`;
}

function historyDuration(seconds) {
  const mins = Math.round((seconds || 0) / 60);
  if (mins < 60) return `${mins} min`;
  return `${Math.floor(mins / 60)} h ${String(mins % 60).padStart(2, '0')} min`;
}

/** Appends a month separator if `iso` starts a month not shown yet. `monthText(key)` returns its totals line. */
function historyMaybeMonth(container, state, iso, monthText) {
  const key = historyMonthKey(iso);
  if (state.lastMonth === key) return;
  state.lastMonth = key;
  const head = document.createElement('div');
  head.className = 'hist-month';
  const name = document.createElement('strong');
  name.textContent = historyMonthLabel(key);
  head.appendChild(name);
  const totals = monthText(key);
  if (totals) {
    const span = document.createElement('span');
    span.textContent = totals;
    head.appendChild(span);
  }
  container.appendChild(head);
}

/** One row. `head` = {when, place, big, small} as HTML built by the caller with every server value escaped:
 * line 1 «when ……… big», line 2 «place ……… small». `detail` is the unfolded part. */
function historyRow(head, detail) {
  const row = document.createElement('details');
  row.className = 'hist-row';
  row.innerHTML = `<summary>
      <span class="hist-when">${head.when}</span><strong class="hist-big">${head.big}</strong>
      <span class="hist-place">${head.place}</span><span class="hist-small">${head.small}</span>
      <span class="hist-chevron" aria-hidden="true"></span>
    </summary><div class="hist-detail">${detail}</div>`;
  return row;
}

/** Label/value pairs of the unfolded part; pairs whose value is empty are left out. */
function historyFacts(pairs) {
  const items = pairs.filter(([, v]) => v !== null && v !== undefined && v !== '')
    .map(([k, v]) => `<div><dt>${escapeHtml(k)}</dt><dd>${escapeHtml(v)}</dd></div>`).join('');
  return `<dl class="hist-facts">${items}</dl>`;
}

/** Shows or hides the «Ver más» button below a list. */
function historyMore(button, hasMore) {
  if (!button) return;
  button.hidden = !hasMore;
  button.disabled = false;
}
