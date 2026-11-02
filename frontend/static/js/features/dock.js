/* EVBro — The phone's bottom bar, chosen by each account: Inicio always on the left, Más always on the right and up to four
 * sections in between (six icons at most). Any section of Más can go there, as well as Carga and Confort (the default).
 * - The icons are taken from the Más menu itself (and the original Carga/Confort buttons), so they never drift apart.
 * - Each button keeps the `mobTab<Tab>` id: switchTab() lights it up, and lights Más only for sections not in the bar.
 * - Saved per account on the server (`/api/account/home-layout`, page `dock`) and copied in the device to draw it at once.
 * - On a computer there is no bottom bar (sidebar instead), so neither is its «Personalizar barra inferior» button.
 * (Classic script: shares the global scope with the other files; see the load order in templates/index.html.)
 */
const Dock = (function () {
  'use strict';

  const MAX = 4;                                   // between Inicio and Más
  const DEFAULT = ['charges', 'climate'];
  const STORE_KEY = 'evbro_dock';
  // `src`: where the icon is taken from; `click`: what the button does (a section by default)
  const ITEMS = {
    charges: { label: 'Carga', tab: 'charges', src: '#mobTabCharges' },
    climate: { label: 'Confort', tab: 'climate', src: '#mobTabClimate' },
    drives: { label: 'Viajes', tab: 'drives' },
    stats: { label: 'Gráficas', name: 'Estadísticas', tab: 'stats' },          // «Estadísticas» does not fit with six icons at 360 px
    timeline: { label: 'Cronología', tab: 'timeline' },
    maintenance: { label: 'Taller', name: 'Mantenimiento', tab: 'maintenance' },
    geofences: { label: 'Geocercas', tab: 'geofences' },
    refuels: { label: 'Repostajes', tab: 'refuels' },
    expenses: { label: 'Gastos', tab: 'expenses' },
    costs: { label: 'Costes', tab: 'costs' },
    vehicle: { label: 'Coches', tab: 'vehicle', click: 'openVehicleSettings()' },
    settings: { label: 'Ajustes', tab: 'settings' },
    sensors: { label: 'Sensores', name: 'Sensores y cables', click: 'openAllSensorsModal()' },
  };
  const $ = (id) => document.getElementById(id);
  const clean = (list) => (Array.isArray(list) ? list.filter((k, i) => ITEMS[k] && list.indexOf(k) === i).slice(0, MAX) : DEFAULT.slice());
  const cap = (s) => s.charAt(0).toUpperCase() + s.slice(1);
  const idOf = (key) => (ITEMS[key].tab ? `mobTab${cap(ITEMS[key].tab)}` : `mobDock${cap(key)}`);
  const clickOf = (key) => ITEMS[key].click || `switchTab('${ITEMS[key].tab}')`;

  let chosen = DEFAULT.slice();
  let synced = false;
  const icons = {};                                // key → svg markup, read once from the page

  function iconOf(key) {
    if (icons[key]) return icons[key];
    const it = ITEMS[key];
    const from = it.src ? document.querySelector(`${it.src} svg`) : document.querySelector(`#tabMore [data-on-click="${clickOf(key)}"] svg`);
    if (!from) return '';
    const svg = from.cloneNode(true);
    svg.setAttribute('width', '22');
    svg.setAttribute('height', '22');
    svg.setAttribute('aria-hidden', 'true');
    icons[key] = svg.outerHTML;
    return icons[key];
  }

  /** Draws the bar: Inicio, the chosen sections in order, Más. */
  function render() {
    const bar = document.querySelector('.native-bottom-dock');
    const home = $('mobTabOverview'), more = $('mobTabMore');
    if (!bar || !home || !more) return;
    Object.keys(ITEMS).forEach((k) => iconOf(k));          // before the original Carga/Confort buttons are replaced
    bar.querySelectorAll('.dock-item').forEach((b) => { if (b !== home && b !== more) b.remove(); });
    chosen.forEach((key) => {
      const b = document.createElement('button');
      b.type = 'button';
      b.className = 'dock-item';
      b.id = idOf(key);
      b.dataset.onClick = clickOf(key);
      b.dataset.dock = key;
      b.innerHTML = `${iconOf(key)}<span>${escapeHtml(t(ITEMS[key].label))}</span>`;
      more.before(b);
    });
    bar.dataset.count = String(chosen.length + 2);
    const current = window.currentTabName || 'overview';
    bar.querySelectorAll('.dock-item').forEach((b) => b.classList.remove('active'));
    const lit = $(`mobTab${cap(current)}`) || (current !== 'overview' ? more : home);
    if (lit && lit.parentElement === bar) lit.classList.add('active');
    if (typeof tagAccessElements === 'function') tagAccessElements();      // the same permissions as the Más buttons
  }

  function writeCached() { try { localStorage.setItem(STORE_KEY, JSON.stringify(chosen)); } catch (e) { /* only a convenience */ } }
  function readCached() { try { const raw = localStorage.getItem(STORE_KEY); return raw ? clean(JSON.parse(raw)) : null; } catch (e) { return null; } }

  /** The account's bar from the server, once (after signing in: called from core/vehicles.js). */
  async function sync() {
    if (synced || !window.WidgetBoards) return;
    const layout = await WidgetBoards.serverLayout();
    if (layout === undefined) return;                      // not read (offline): the device copy stays
    synced = true;
    const list = layout && layout.pages && Array.isArray(layout.pages.dock) ? clean(layout.pages.dock) : DEFAULT.slice();
    if (list.join() !== chosen.join()) { chosen = list; render(); }
    writeCached();
  }

  let saveTimer = null;
  function save() {
    writeCached();
    render();
    renderEditor();
    clearTimeout(saveTimer);
    saveTimer = setTimeout(async () => {
      try {
        const res = await fetch('/api/account/home-layout', { method: 'PUT', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ page: 'dock', widgets: chosen }) });
        if (!res.ok) throw new Error(res.status);
      } catch (e) { showToast(t('No se pudo guardar la barra; se queda en este dispositivo.'), 'warning', 5000); }
    }, 400);
  }

  // ───────────────────────── editor ─────────────────────────
  const ARROW_UP = '<svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M6 15l6-6 6 6"/></svg>';
  const ARROW_DOWN = '<svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M6 9l6 6 6-6"/></svg>';
  const CROSS = '<svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M6 6l12 12M18 6L6 18"/></svg>';
  const nameOf = (key) => t(ITEMS[key].name || ITEMS[key].label);

  function renderEditor() {
    const box = $('dockChosen');
    if (!box) return;
    const fixed = (label, icon) => `<div class="dock-edit-row is-fixed"><span class="dock-edit-icon">${icon}</span><span class="dock-edit-name">${escapeHtml(label)}<small>${escapeHtml(t('Siempre en la barra'))}</small></span></div>`;
    const homeIcon = document.querySelector('#mobTabOverview svg')?.outerHTML || '';
    const moreIcon = document.querySelector('#mobTabMore svg')?.outerHTML || '';
    box.innerHTML = fixed(t('Inicio'), homeIcon) + chosen.map((key, i) => {
      const name = nameOf(key);
      return `<div class="dock-edit-row" data-dock="${key}"><span class="dock-edit-icon">${iconOf(key)}</span><span class="dock-edit-name">${escapeHtml(name)}</span>
        <button type="button" class="hw-tool" data-on-click="Dock.move('${key}', -1)" aria-label="${escapeHtml(t('Subir {p0}', { p0: name }))}"${i === 0 ? ' disabled' : ''}>${ARROW_UP}</button>
        <button type="button" class="hw-tool" data-on-click="Dock.move('${key}', 1)" aria-label="${escapeHtml(t('Bajar {p0}', { p0: name }))}"${i === chosen.length - 1 ? ' disabled' : ''}>${ARROW_DOWN}</button>
        <button type="button" class="hw-tool hw-remove" data-on-click="Dock.remove('${key}')" aria-label="${escapeHtml(t('Quitar {p0}', { p0: name }))}">${CROSS}</button></div>`;
    }).join('') + fixed(t('Más'), moreIcon);
    const full = chosen.length >= MAX;
    $('dockFull').hidden = !full;
    $('dockFree').innerHTML = Object.keys(ITEMS).filter((k) => !chosen.includes(k)).map((key) => `<button type="button" class="dock-edit-row dock-edit-add" data-dock="${key}" data-on-click="Dock.add('${key}')"${full ? ' disabled' : ''}>
        <span class="dock-edit-icon">${iconOf(key)}</span><span class="dock-edit-name">${escapeHtml(nameOf(key))}</span><span class="dock-edit-plus" aria-hidden="true">+</span></button>`).join('');
  }

  function open() { renderEditor(); $('dockModal').classList.add('open'); }
  function close() { $('dockModal').classList.remove('open'); }
  function add(key) { if (ITEMS[key] && !chosen.includes(key) && chosen.length < MAX) { chosen.push(key); save(); } }
  function remove(key) { chosen = chosen.filter((k) => k !== key); save(); }
  function move(key, dir) {
    const i = chosen.indexOf(key), j = i + dir;
    if (i < 0 || j < 0 || j >= chosen.length) return;
    [chosen[i], chosen[j]] = [chosen[j], chosen[i]];
    save();
    const btn = document.querySelector(`#dockChosen [data-dock="${key}"] [data-on-click*="${dir < 0 ? '-1' : ', 1'}"]`);
    if (btn && !btn.disabled) btn.focus();
  }
  async function reset() {
    chosen = DEFAULT.slice();
    writeCached();
    render();
    renderEditor();
    clearTimeout(saveTimer);
    try { await fetch('/api/account/home-layout?page=dock', { method: 'DELETE' }); } catch (e) { /* the device copy is already the default */ }
  }

  document.addEventListener('DOMContentLoaded', () => {
    const cached = readCached();
    if (cached) chosen = cached;
    render();
  });

  return { sync, render, open, close, add, remove, move, reset, chosen: () => chosen.slice() };
})();
window.Dock = Dock;
