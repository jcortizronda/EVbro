/* EVBro — Pages made of widgets (Inicio, Carga and Confort): each account chooses which blocks it sees and in what order
 * (drag or arrows), and can add more. One engine, one «board» per page: `HomeWidgets`, `EnergyWidgets`, `ComfortWidgets`.
 *
 * - The blocks that were already on Inicio (range, car figure, orders, figures, charging, map, tyres, all data) are widgets that
 *   live in index.html: hiding one only hides it, its ids stay in the page because other scripts fill them.
 * - The new widgets (maintenance, last trip, last charge, month's spending, scheduled charge, climate, consumption) are built here
 *   and read APIs that already exist, only while they are shown and only when Inicio is opened: they never poll the car.
 * - Carga (the `charges` tab, called Energía until 2026-11-01) and Confort: their blocks (battery and target, figures, charge control, schedule, history; climate, glass and steering
 *   wheel, seats, scheduled climate) are widgets the same way; Carga can also take a few of the new cards.
 * - The layout is saved per account and page on the server (`/api/account/home-layout`) and copied in the device to draw it at once.
 * (Classic script: shares the global scope with the other files; see the load order in templates/index.html.)
 */
const WidgetBoards = (function () {
  'use strict';

  const $ = (id) => document.getElementById(id);
  const ICON = (inner) => `<svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">${inner}</svg>`;

  // `live`: needs the car's own data (not on a car without an Ebro account); `electric`: only plug-in hybrids and electric cars
  const CATALOG = {
    range: { label: 'Autonomía', desc: 'Batería y gasolina, con los kilómetros que quedan.', live: true },
    car: { label: 'Estado del coche', desc: 'Puertas, ventanillas, maletero, techo y cierre en el dibujo del coche.', live: true },
    actions: { label: 'Accesos y órdenes', desc: 'Cerrar, buscar, maletero, ventanillas, techo, GPS y alarma.', live: true },
    kpis: { label: 'Cifras', desc: 'Autonomía total, odómetro y velocidad.', live: true },
    charging: { label: 'Carga en curso', desc: 'Potencia y tiempo restante; solo aparece mientras el cable está conectado.', live: true, electric: true },
    map: { label: 'Mapa', desc: 'Dónde está el coche, con «Centrar» e «Ir a».' },
    tpms: { label: 'Presión de neumáticos', desc: 'Presión y temperatura de cada rueda.', live: true },
    sensors: { label: 'Todos los datos', desc: 'Botón para ver todos los datos que manda el coche.', live: true },
    service: { label: 'Próximo mantenimiento', desc: 'La revisión o tarea más cercana y la ITV.', tab: 'maintenance',
      icon: ICON('<path d="M14.7 6.3a4 4 0 0 0-5.4 5.4L3 18l3 3 6.3-6.3a4 4 0 0 0 5.4-5.4l-2.5 2.5-2.4-.6-.6-2.4z"/>') },
    last_trip: { label: 'Último viaje', desc: 'De dónde a dónde, distancia, duración y consumo.', tab: 'drives',
      icon: ICON('<circle cx="6" cy="18" r="2.5"/><circle cx="18" cy="6" r="2.5"/><path d="M8.5 18H15a3 3 0 0 0 0-6H9a3 3 0 0 1 0-6h6.5"/>') },
    last_charge: { label: 'Última carga', desc: 'Dónde, cuánta energía, de qué a qué porcentaje y su coste.', tab: 'charges', electric: true,
      icon: ICON('<path d="M13 2 4 14h7l-1 8 9-12h-7z"/>') },
    month_cost: { label: 'Gasto del mes', desc: 'Lo gastado este mes en cargas, gasolina, mantenimiento y otros gastos.', tab: 'costs',
      icon: ICON('<rect x="2.5" y="6" width="19" height="13" rx="2.5"/><path d="M2.5 10.5h19M7 15h3"/>') },
    charge_plan: { label: 'Carga programada', desc: 'El objetivo de batería y el horario de carga.', tab: 'charges', live: true, electric: true,
      icon: ICON('<circle cx="12" cy="13" r="8"/><path d="M12 9v4l2.5 2.5M9 2h6"/>') },
    climate: { label: 'Climatización', desc: 'Temperatura dentro y fuera, y encender o apagar el clima.', tab: 'climate', live: true,
      icon: ICON('<path d="M12 3v18M4.2 7.5l15.6 9M4.2 16.5l15.6-9"/>') },
    consumption: { label: 'Consumo', desc: 'Kilómetros y consumo medio de los últimos 30 días.', tab: 'stats',
      icon: ICON('<path d="M3 20h18M6 16v-4M11 16V8M16 16v-6M21 16V4"/>') },
  };
  const ENERGY_CATALOG = {
    energy: { label: 'Batería y objetivo', desc: 'La carga actual y el objetivo de batería, con su barra.' },
    kpis: { label: 'Cifras de energía', desc: 'Consumo eléctrico de 30 días y potencia de la batería.' },
    control: { label: 'Control de carga', desc: 'Parar o iniciar la carga.' },
    schedule: { label: 'Carga programada', desc: 'El horario de carga de cada día.' },
    charges: { label: 'Historial de cargas', desc: 'Todas las cargas, con su energía, lugar y coste.' },
    session: { label: 'Detalle de la carga', desc: 'La curva, la energía, el coste y el mapa de una carga.' },
    last_charge: CATALOG.last_charge,
    month_cost: CATALOG.month_cost,
    consumption: CATALOG.consumption,
  };
  const COMFORT_CATALOG = {
    temperature: { label: 'Climatización', desc: 'Encender o apagar el clima y su temperatura.' },
    defrost: { label: 'Lunas y volante', desc: 'Desempañado de las lunas y volante calefactado.' },
    seats: { label: 'Asientos', desc: 'Calefacción y ventilación de cada asiento.' },
    schedule: { label: 'Climatización programada', desc: 'Encender el clima a una hora y unos días.' },
  };
  const LOADERS = {};                  // key → async (vin) => html; filled in below
  const RELOAD_MS = 60 * 1000;         // a widget is not asked again for the same car within a minute (unless forced)
  let lastTelemetry = null;

  // ───────────────────────── the server copy of every page's layout (one request for all) ─────────────────────────
  let server = null;                   // the account's layout once read: { widgets?, pages? } or null (all defaults)
  let serverLoaded = false;
  let serverPromise = null;
  function loadServer() {
    if (!serverPromise) {
      serverPromise = (async () => {
        try {
          const res = await fetch('/api/account/home-layout');
          if (!res.ok) { serverPromise = null; return; }
          server = (await res.json()).layout || null;
          serverLoaded = true;
        } catch (e) { serverPromise = null; /* offline: the device copy stays */ }
      })();
    }
    return serverPromise;
  }

  /** A widget that this car cannot show (no Ebro account, or not electric); CSS hides it. */
  function unavailable(el) {
    const b = document.body.dataset;
    return (el.dataset.live !== undefined && b.manual === '1') || (el.dataset.electric !== undefined && !['phev', 'ev'].includes(b.fuel || 'phev'));
  }

  const boards = {};
  let addingTo = null;                 // the board whose «Añadir widget» list is open

  /** One page's board. `cfg`: name (its global), page, box/bar/customize ids, storeKey, catalog, def, resetText, onApply. */
  function createBoard(cfg) {
    const { catalog, def } = cfg;
    let layout = def.slice();
    let editing = false;
    let saveTimer = null;
    const loaded = {};                 // key → { vin, at }
    const box = () => $(cfg.box);
    const sel = (rest) => `#${cfg.box} > .home-widget${rest || ''}`;
    // A key that was split or renamed (e.g. Carga's `history` became `charges` + `session`) is read as its new keys
    const expand = (list) => list.flatMap((k) => (cfg.aliases && cfg.aliases[k]) || [k]);
    const clean = (list) => (Array.isArray(list) ? expand(list).filter((k, i, all) => catalog[k] && all.indexOf(k) === i) : def.slice());
    const fromServer = () => {
      const list = cfg.page === 'overview' ? server && server.widgets : server && server.pages && server.pages[cfg.page];
      return Array.isArray(list) ? clean(list) : def.slice();
    };

    function readCached() {
      try { const raw = localStorage.getItem(cfg.storeKey); return raw ? clean(JSON.parse(raw)) : null; } catch (e) { return null; }
    }
    function writeCached() {
      try { localStorage.setItem(cfg.storeKey, JSON.stringify(layout)); } catch (e) { /* only a convenience */ }
    }

    let synced = false;                // this board already took the server's copy (one request is shared by every board)
    async function syncServer() {
      if (synced) return;
      await loadServer();
      if (!serverLoaded || synced) return;
      synced = true;
      const list = fromServer();
      if (list.join() !== layout.join()) { layout = list; apply(); }
      writeCached();
    }

    function save() {
      writeCached();
      clearTimeout(saveTimer);
      saveTimer = setTimeout(async () => {
        try {
          const res = await fetch('/api/account/home-layout', { method: 'PUT', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ page: cfg.page, widgets: layout }) });
          if (!res.ok) throw new Error(res.status);
          server = (await res.json()).layout || null;
        } catch (e) { showToast('No se pudo guardar la disposición de la página; se queda en este dispositivo.', 'warning', 5000); }
      }, 400);
    }

    /** The wrapper of a widget; the new ones are built the first time they are shown. */
    function widgetEl(key) {
      let el = document.querySelector(sel(`[data-widget="${key}"]`));
      if (el || !LOADERS[key] || !box()) return el;
      const w = catalog[key];
      el = document.createElement('div');
      el.className = 'home-widget';
      el.dataset.widget = key;
      if (w.live) el.dataset.live = '';
      if (w.electric) el.dataset.electric = '';
      el.innerHTML = `<div class="glass hw-card" data-on-click="switchTab('${w.tab}')">
        <button type="button" class="hw-head" data-on-click="switchTab('${w.tab}')"><span class="hw-icon">${w.icon}</span><span class="hw-title">${escapeHtml(t(w.label))}</span><span class="hw-go" aria-hidden="true">›</span></button>
        <div class="hw-body" data-hw-body><span class="hw-muted">${escapeHtml(t('Cargando…'))}</span></div>
      </div>`;
      box().append(el);
      return el;
    }

    /** Puts the shown widgets in order and hides the rest. */
    function apply() {
      const b = box();
      if (!b) return;
      layout.forEach((key) => { const el = widgetEl(key); if (el) { el.hidden = false; b.append(el); } });
      b.querySelectorAll(':scope > .home-widget').forEach((el) => { if (!layout.includes(el.dataset.widget)) el.hidden = true; });
      if (editing) decorate();
      if (cfg.onApply) cfg.onApply();
    }

    /** Loads the new widgets that are shown. Called when the page opens (also after changing car) and when one is added. */
    async function refresh(force = false) {
      await syncServer();
      if (typeof currentVin === 'undefined' || !currentVin) return;
      const vin = currentVin;
      await Promise.all(layout.filter((k) => LOADERS[k]).map(async (key) => {
        const prev = loaded[key];
        if (!force && prev && prev.vin === vin && Date.now() - prev.at < RELOAD_MS) return;
        const el = widgetEl(key);
        if (!el || unavailable(el)) return;
        loaded[key] = { vin, at: Date.now() };
        let html;
        try { html = await LOADERS[key](vin); } catch (e) { html = `<span class="hw-muted">${escapeHtml(t('No se pudo cargar.'))}</span>`; delete loaded[key]; }
        if (vin === currentVin) el.querySelector('[data-hw-body]').innerHTML = html;
      }));
      if (typeof tagAccessElements === 'function') tagAccessElements();      // the climate buttons need the «control» permission
    }

    // ───── edit mode ─────
    function startEditing() {
      Object.values(boards).forEach((other) => { if (other !== api) other.stopEditing(); });
      editing = true;
      document.body.classList.add('home-editing');
      $(cfg.bar).hidden = false;
      $(cfg.customize).hidden = true;
      decorate();
      $(cfg.bar).scrollIntoView({ block: 'nearest', behavior: 'smooth' });
    }

    function stopEditing() {
      if (!editing) return;
      editing = false;
      document.body.classList.remove('home-editing');
      $(cfg.bar).hidden = true;
      $(cfg.customize).hidden = false;
      document.querySelectorAll(`#${cfg.box} .hw-tools`).forEach((el) => el.remove());
      if (cfg.onApply) cfg.onApply();
    }

    /** Each shown widget gets its bar: drag handle, name, up, down and remove. */
    function decorate() {
      document.querySelectorAll(sel()).forEach((el) => {
        const key = el.dataset.widget;
        let bar = el.querySelector(':scope > .hw-tools');
        if (el.hidden) { if (bar) bar.remove(); return; }
        if (!bar) {
          bar = document.createElement('div');
          bar.className = 'hw-tools';
          const name = t(catalog[key].label);
          bar.innerHTML = `<button type="button" class="hw-tool hw-drag" aria-label="${escapeHtml(t('Arrastrar {p0}', { p0: name }))}" title="${escapeHtml(t('Arrastrar'))}">${ICON('<circle cx="9" cy="6" r="1.2"/><circle cx="15" cy="6" r="1.2"/><circle cx="9" cy="12" r="1.2"/><circle cx="15" cy="12" r="1.2"/><circle cx="9" cy="18" r="1.2"/><circle cx="15" cy="18" r="1.2"/>')}</button>
            <span class="hw-tools-name">${escapeHtml(name)}${unavailable(el) ? `<small>${escapeHtml(t('No disponible en este coche'))}</small>` : ''}</span>
            <button type="button" class="hw-tool" data-on-click="${cfg.name}.move('${key}', -1)" aria-label="${escapeHtml(t('Subir {p0}', { p0: name }))}">${ICON('<path d="M6 15l6-6 6 6"/>')}</button>
            <button type="button" class="hw-tool" data-on-click="${cfg.name}.move('${key}', 1)" aria-label="${escapeHtml(t('Bajar {p0}', { p0: name }))}">${ICON('<path d="M6 9l6 6 6-6"/>')}</button>
            <button type="button" class="hw-tool hw-remove" data-on-click="${cfg.name}.remove('${key}')" aria-label="${escapeHtml(t('Quitar {p0}', { p0: name }))}">${ICON('<path d="M6 6l12 12M18 6L6 18"/>')}</button>`;
          bar.querySelector('.hw-drag').addEventListener('pointerdown', (ev) => dragStart(ev, el));
          el.prepend(bar);
        }
      });
      syncArrows();
    }

    function syncArrows() {
      document.querySelectorAll(sel(':not([hidden])')).forEach((el) => {
        const i = layout.indexOf(el.dataset.widget);
        const [up, down] = el.querySelectorAll('.hw-tools [data-on-click*="move"]');
        if (up) up.disabled = i <= 0;
        if (down) down.disabled = i >= layout.length - 1;
      });
    }

    function move(key, dir) {
      const i = layout.indexOf(key), j = i + dir;
      if (i < 0 || j < 0 || j >= layout.length) return;
      [layout[i], layout[j]] = [layout[j], layout[i]];
      apply();
      save();
      const btn = document.querySelector(`${sel(`[data-widget="${key}"]`)} .hw-tools [data-on-click*="${dir < 0 ? '-1' : ', 1'}"]`);
      if (btn && !btn.disabled) btn.focus(); else widgetEl(key).querySelector('.hw-drag').focus();
      widgetEl(key).scrollIntoView({ block: 'nearest', behavior: 'smooth' });
    }

    function remove(key) {
      layout = layout.filter((k) => k !== key);
      apply();
      save();
      showToast(t('«{p0}» quitado. Puedes volver a ponerlo con «Añadir widget».', { p0: t(catalog[key].label) }), 'info', 4000);
    }

    // ───── add and reset ─────
    function openAdd() {
      addingTo = api;
      const free = Object.keys(catalog).filter((k) => !layout.includes(k));
      $('homeAddList').innerHTML = free.length ? free.map((k) => {
        const w = catalog[k];
        const probe = document.createElement('div');
        if (w.live) probe.dataset.live = '';
        if (w.electric) probe.dataset.electric = '';
        const na = unavailable(probe);
        return `<button type="button" class="hw-add-item" data-on-click="${cfg.name}.add('${k}')">
          <strong>${escapeHtml(t(w.label))}${na ? ` <small class="hw-na">${escapeHtml(t('No disponible en este coche'))}</small>` : ''}</strong><span>${escapeHtml(t(w.desc))}</span></button>`;
      }).join('') : `<p class="hw-muted">${escapeHtml(t('Ya tienes todos los widgets en esta página.'))}</p>`;
      $('homeAddModal').classList.add('open');
    }

    function add(key) {
      if (!catalog[key] || layout.includes(key)) return;
      layout.push(key);
      closeAdd();
      apply();
      save();
      refresh();
      const el = widgetEl(key);
      if (el) setTimeout(() => el.scrollIntoView({ block: 'center', behavior: 'smooth' }), 80);
    }

    async function reset() {
      if (!await confirmDialog(t(cfg.resetText), { confirmText: t('Restablecer') })) return;
      layout = def.slice();
      apply();
      writeCached();
      clearTimeout(saveTimer);
      try {
        const res = await fetch(`/api/account/home-layout?page=${cfg.page}`, { method: 'DELETE' });
        if (res.ok) server = (await res.json()).layout || null;
      } catch (e) { /* the device copy is already the default */ }
      refresh();
    }

    // ───── drag (mouse, finger or pen: pointer events on the handle) ─────
    function dragStart(ev, el) {
      if (ev.button !== undefined && ev.button !== 0) return;
      ev.preventDefault();
      const handle = ev.currentTarget;
      const b = box();
      const rect = el.getBoundingClientRect();
      const offset = ev.clientY - rect.top;
      const ph = document.createElement('div');
      ph.className = 'hw-placeholder';
      ph.style.height = `${rect.height}px`;
      el.before(ph);
      el.classList.add('hw-dragging');
      Object.assign(el.style, { width: `${rect.width}px`, left: `${rect.left}px`, top: `${rect.top}px` });
      try { handle.setPointerCapture(ev.pointerId); } catch (e) { /* older browsers: the move still works */ }
      let lastY = ev.clientY, scroller = null;

      const place = () => {
        el.style.top = `${lastY - offset}px`;
        const others = [...b.querySelectorAll(':scope > .home-widget:not([hidden]):not(.hw-dragging)')];
        const before = others.find((o) => { const r = o.getBoundingClientRect(); return lastY < r.top + r.height / 2; });
        if (before) { if (ph.nextElementSibling !== before) before.before(ph); } else if (b.lastElementChild !== ph) b.append(ph);
      };
      const autoScroll = () => {       // near the top or bottom edge the page scrolls by itself
        const edge = 70, h = window.innerHeight;
        const dy = lastY < edge ? -12 : lastY > h - edge ? 12 : 0;
        if (dy) { window.scrollBy(0, dy); place(); }
        scroller = requestAnimationFrame(autoScroll);
      };
      const onMove = (e) => { lastY = e.clientY; place(); };
      const onUp = () => {
        cancelAnimationFrame(scroller);
        handle.removeEventListener('pointermove', onMove);
        handle.removeEventListener('pointerup', onUp);
        handle.removeEventListener('pointercancel', onUp);
        el.classList.remove('hw-dragging');
        el.style.width = el.style.left = el.style.top = '';
        ph.replaceWith(el);
        const order = [...b.querySelectorAll(':scope > .home-widget:not([hidden])')].map((x) => x.dataset.widget);
        if (order.join() !== layout.join()) { layout = order; save(); }
        syncArrows();
        if (cfg.onApply) cfg.onApply();
      };
      handle.addEventListener('pointermove', onMove);
      handle.addEventListener('pointerup', onUp);
      handle.addEventListener('pointercancel', onUp);
      scroller = requestAnimationFrame(autoScroll);
    }

    /** The device copy draws the page at once; the server's copy arrives with the first refresh (after signing in). */
    function init() {
      const cached = readCached();
      if (cached) layout = cached;
      apply();
    }

    const api = { refresh, startEditing, stopEditing, move, remove, openAdd, add, reset, init, page: cfg.page,
      layout: () => layout.slice(), shows: (key) => layout.includes(key), box };
    boards[cfg.page] = api;
    return api;
  }

  function closeAdd() { $('homeAddModal').classList.remove('open'); addingTo = null; }

  // ───────────────────────── data of the new widgets ─────────────────────────
  const num = (n, d = 0) => Number(n).toLocaleString(I18n.locale, { minimumFractionDigits: d, maximumFractionDigits: d });
  const money = (n) => Number(n || 0).toLocaleString(I18n.locale, { style: 'currency', currency: 'EUR' });
  const day = (iso) => serverDate(iso).toLocaleDateString(I18n.locale, { day: 'numeric', month: 'short' });
  const time = (iso) => serverDate(iso).toLocaleTimeString(I18n.locale, { hour: '2-digit', minute: '2-digit' });
  const when = (iso) => {
    const d = serverDate(iso);
    const today = new Date();
    const yest = new Date(today.getTime() - 86400000);
    if (d.toDateString() === today.toDateString()) return t('Hoy, {p0}', { p0: time(iso) });
    if (d.toDateString() === yest.toDateString()) return t('Ayer, {p0}', { p0: time(iso) });
    return `${day(iso)}, ${time(iso)}`;
  };
  const line = (main, sub, state) => `<div class="hw-line"${state ? ` data-state="${state}"` : ''}><span>${escapeHtml(main)}</span>${sub ? `<small>${escapeHtml(sub)}</small>` : ''}</div>`;
  const muted = (text) => `<span class="hw-muted">${escapeHtml(text)}</span>`;
  const getJson = async (url) => { const res = await fetch(url); if (!res.ok) throw new Error(res.status); return res.json(); };

  /** «faltan 480 km · 30 días» or «pasada de 120 km» */
  function remaining(km, days) {
    const parts = [];
    if (km !== null && km !== undefined) parts.push(km >= 0 ? t('{p0} km', { p0: num(km) }) : t('pasada de {p0} km', { p0: num(-km) }));
    if (days !== null && days !== undefined) parts.push(days >= 0 ? t('{p0} días', { p0: num(days) }) : t('vencida hace {p0} días', { p0: num(-days) }));
    return parts.length && (km ?? 0) >= 0 && (days ?? 0) >= 0 ? t('Faltan {p0}', { p0: parts.join(' · ') }) : parts.join(' · ');
  }
  const stateOf = (s) => (s === 'due' ? 'bad' : s === 'warning' ? 'warn' : s === 'ok' ? 'ok' : '');

  LOADERS.service = async (vin) => {
    const plan = await getJson(`/api/vehicles/${vin}/maintenance/plan`);
    const out = [];
    if (plan.mode === 'official' && plan.official && plan.official.next) {
      const n = plan.official.next;
      out.push(line(t('Revisión {p0}', { p0: n.label }), remaining(n.remaining_km, n.remaining_days), stateOf(n.status)));
    } else {
      const items = ((plan.custom && plan.custom.items) || []).filter((i) => i.remaining_km !== null || i.remaining_days !== null);
      const rank = { due: 0, warning: 1, ok: 2 };
      items.sort((a, b) => (rank[a.status] ?? 3) - (rank[b.status] ?? 3) || (a.remaining_days ?? 1e9) - (b.remaining_days ?? 1e9));
      if (items[0]) out.push(line(items[0].name, remaining(items[0].remaining_km, items[0].remaining_days), stateOf(items[0].status)));
    }
    const itv = plan.itv || {};
    if (itv.next_due_date) out.push(line(t('ITV: {p0}', { p0: serverDate(itv.next_due_date).toLocaleDateString(I18n.locale) }), remaining(null, itv.remaining_days), stateOf(itv.status)));
    return out.join('') || muted(t('No hay nada previsto. Configura el plan en Mantenimiento.'));
  };

  LOADERS.last_trip = async (vin) => {
    const d = ((await getJson(`/api/vehicles/${vin}/drives?limit=1`)).drives || [])[0];
    if (!d) return muted(t('Todavía no hay viajes.'));
    const route = [d.start_address, d.end_address].filter(Boolean).map((a) => a.split(',')[0]).join(' → ');
    const cons = [d.consumption_kwh_100km ? t('{p0} kWh/100 km', { p0: num(d.consumption_kwh_100km, 1) }) : null,
      d.consumption_l_100km ? t('{p0} l/100 km', { p0: num(d.consumption_l_100km, 1) }) : null].filter(Boolean).join(' · ');
    return `<div class="hw-big">${num(d.distance_km || 0, 1)} <small>km</small></div>`
      + line(route || t('Sin dirección'), [when(d.start_time), d.duration_min ? t('{p0} min', { p0: num(d.duration_min) }) : null].filter(Boolean).join(' · '))
      + (cons ? `<div class="hw-foot">${escapeHtml(cons)}</div>` : '');
  };

  LOADERS.last_charge = async (vin) => {
    const c = (await getJson(`/api/vehicles/${vin}/charges/latest`)).charge;
    if (!c) return muted(t('Todavía no hay cargas.'));
    const pct = c.start_battery_pct !== null && c.end_battery_pct !== null ? `${num(c.start_battery_pct)} → ${num(c.end_battery_pct)} %` : null;
    const foot = [pct, c.has_cost || c.charge_cost ? money(c.charge_cost) : null].filter(Boolean).join(' · ');
    return `<div class="hw-big">${c.kwh_added !== null && c.kwh_added !== undefined ? `+${num(c.kwh_added, 1)}` : '—'} <small>kWh</small></div>`
      + line(c.address || t('Sin dirección'), c.is_active ? t('Cargando ahora') : when(c.start_time))
      + (foot ? `<div class="hw-foot">${escapeHtml(foot)}</div>` : '');
  };

  LOADERS.month_cost = async (vin) => {
    const d = await getJson(`/api/vehicles/${vin}/costs?period=month`);
    const tot = d.totals || {};
    const parts = [[tot.charges, 'Cargas'], [tot.refuels, 'Gasolina'], [tot.maintenance, 'Mantenimiento'], [tot.expenses, 'Otros']]
      .filter(([v]) => v).map(([v, l]) => `${t(l)} ${money(v)}`);
    return `<div class="hw-big">${money(tot.running || 0)}</div>`
      + (parts.length ? `<div class="hw-foot">${escapeHtml(parts.join(' · '))}</div>` : muted(t('Nada anotado este mes.')))
      + (d.per_100km ? `<div class="hw-foot">${escapeHtml(t('{p0} cada 100 km', { p0: money(d.per_100km) }))}</div>` : '');
  };

  LOADERS.charge_plan = async (vin) => {
    const p = await getJson(`/api/vehicles/${vin}/charge/plan`);        // without `live`: what EVBro keeps, no question to the cloud
    const s = p.schedule || {};
    const sched = s.enabled && s.start ? t('Programada de {p0} a {p1}', { p0: s.start, p1: s.end || '—' }) : t('Sin horario de carga');
    return `<div class="hw-big">${num(p.target_pct ?? 100)} <small>%</small></div>`
      + line(p.target_pct && p.target_pct < 100 ? t('Objetivo de batería') : t('Sin límite de carga'), sched, s.enabled ? 'ok' : '');
  };

  LOADERS.consumption = async (vin) => {
    const months = (await getJson(`/api/vehicles/${vin}/stats?period=30d`)).monthly || [];
    const sum = (k) => months.reduce((a, m) => a + (m[k] || 0), 0);
    const km = sum('km');
    if (!km) return muted(t('Sin viajes en los últimos 30 días.'));
    const kwh = sum('kwh_used'), l = sum('liters_used');
    const cons = [kwh ? t('{p0} kWh/100 km', { p0: num(kwh / km * 100, 1) }) : null, l ? t('{p0} l/100 km', { p0: num(l / km * 100, 1) }) : null].filter(Boolean).join(' · ');
    return `<div class="hw-big">${num(km)} <small>km</small></div>` + line(t('Últimos 30 días'), cons || null);
  };

  /** Climate: from the data the car already sent (no request of its own). */
  LOADERS.climate = async () => climateHtml(lastTelemetry);
  function climateHtml(d) {
    const temp = (v) => (v === null || v === undefined ? '—' : `${num(v, 1)} °C`);
    const on = d ? d.climate_on : null;
    const state = on === true ? t('Encendido') : on === false ? t('Apagado') : t('Sin datos');
    return `<div class="hw-temps"><span><small>${escapeHtml(t('Dentro'))}</small>${temp(d && d.inside_temp)}</span><span><small>${escapeHtml(t('Fuera'))}</small>${temp(d && d.outside_temp)}</span>`
      + `<span><small>${escapeHtml(t('Clima'))}</small><b data-state="${on === true ? 'on' : on === false ? 'off' : 'unknown'}">${escapeHtml(state)}</b></span></div>`
      + `<div class="hw-buttons"><button type="button" class="btn-secondary needs-control" data-on-click="promptCommand('clima_on', 'Encender Climatizador')">${escapeHtml(t('Encender'))}</button>`
      + `<button type="button" class="btn-secondary needs-control" data-on-click="promptCommand('clima_off', 'Apagar Climatizador')">${escapeHtml(t('Apagar'))}</button></div>`;
  }

  /** The climate card of Inicio follows the data the car just sent. */
  function onTelemetry(data) {
    lastTelemetry = data;
    const home = boards.overview;
    const el = home && home.shows('climate') && document.querySelector('#homeWidgets > .home-widget[data-widget="climate"] [data-hw-body]');
    if (el) el.innerHTML = climateHtml(data);
  }

  /** On every tab change: the other pages stop editing and this one's widgets load. */
  function onTab(tabName) {
    Object.values(boards).forEach((b) => { if (b.page !== tabName) b.stopEditing(); });
    if (boards[tabName]) boards[tabName].refresh();
  }

  const fixMap = () => setTimeout(() => { if (typeof liveMap !== 'undefined' && liveMap) liveMap.invalidateSize(); }, 120);
  const fixCharts = () => setTimeout(() => { if (typeof redrawChargeCharts === 'function') redrawChargeCharts(); }, 120);

  createBoard({ name: 'HomeWidgets', page: 'overview', box: 'homeWidgets', bar: 'homeEditBar', customize: 'homeCustomize', storeKey: 'evbro_home_layout',
    catalog: CATALOG, def: ['range', 'car', 'actions', 'kpis', 'charging', 'map', 'tpms', 'sensors'], onApply: fixMap,
    resetText: '¿Volver al inicio de siempre? Se quitan los widgets añadidos y se recupera el orden original.' });
  createBoard({ name: 'EnergyWidgets', page: 'charges', box: 'energyWidgets', bar: 'energyEditBar', customize: 'energyCustomize', storeKey: 'evbro_energy_layout',
    catalog: ENERGY_CATALOG, def: ['energy', 'kpis', 'control', 'charges', 'schedule', 'session'], onApply: fixCharts, aliases: { history: ['charges', 'session'] },
    resetText: '¿Volver a la página de siempre? Se quitan los widgets añadidos y se recupera el orden original.' });
  createBoard({ name: 'ComfortWidgets', page: 'climate', box: 'comfortWidgets', bar: 'comfortEditBar', customize: 'comfortCustomize', storeKey: 'evbro_comfort_layout',
    catalog: COMFORT_CATALOG, def: ['temperature', 'defrost', 'seats', 'schedule'],
    resetText: '¿Volver a la página de siempre? Se quitan los widgets añadidos y se recupera el orden original.' });

  document.addEventListener('DOMContentLoaded', () => Object.values(boards).forEach((b) => b.init()));

  return { onTelemetry, onTab, closeAdd, boards };
})();
window.WidgetBoards = WidgetBoards;
// One global per page board: the `data-on-click` of the edit bars and of each widget's tools call these
window.HomeWidgets = WidgetBoards.boards.overview;
window.EnergyWidgets = WidgetBoards.boards.charges;
window.ComfortWidgets = WidgetBoards.boards.climate;
