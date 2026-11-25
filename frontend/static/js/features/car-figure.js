/* EVbro — Compact top-view car figure on the home tab: doors, hood, trunk, windows, sunroof, lock, charge cable and the four
 * tyre pressures, each coloured by what the car last reported (amber = open, neutral = closed, dashed grey = no data).
 * Tapping a part opens the matching action (windows, trunk, sunroof, lock). Nothing here assumes «closed» without data.
 * (Classic script: shares the global scope; telemetry.js calls `renderCarFigure(data)` after each reading.)
 */
(function () {
  'use strict';

  const $ = (id) => document.getElementById(id);
  const known = (v) => v !== null && v !== undefined;
  // the hose's horizontal line, in the picture's pixels: a little below the car, level with the rear wheel's pressure
  const HOSE_Y = 515;

  const PART_LABELS = {
    hood: 'Capó', trunk: 'Maletero', sunroof: 'Techo',
    'door-fl': 'Puerta del. izq.', 'door-fr': 'Puerta del. der.', 'door-rl': 'Puerta tras. izq.', 'door-rr': 'Puerta tras. der.',
    'win-fl': 'Ventanilla del. izq.', 'win-fr': 'Ventanilla del. der.', 'win-rl': 'Ventanilla tras. izq.', 'win-rr': 'Ventanilla tras. der.',
  };

  function setState(part, value) {
    const el = document.querySelector(`#carFigure [data-part="${part}"]`);
    if (!el) return;
    el.dataset.state = !known(value) ? 'unknown' : value ? 'open' : 'closed';
  }

  /** Tyre label: «2.4» in a pill next to the wheel, red when the car flags it or it drifts from the reference pressure of the car sheet. */
  function setTyre(key, bar, warn) {
    const text = $(`cfTyre${key.toUpperCase()}`);
    const wheel = document.querySelector(`#carFigure [data-wheel="${key}"]`);
    const pill = document.querySelector(`#carFigure [data-pill="${key}"]`);
    const has = known(bar) && bar > 0;
    const state = !has ? 'unknown' : warn ? 'warn' : 'ok';
    if (text) text.textContent = has ? historyNum(bar, 1) : '';
    if (wheel) wheel.dataset.state = state;
    if (pill) pill.dataset.state = state;
  }

  /* The car's own colour from the car sheet («Blanco perla», «Gris grafito»…): a tint for the body, and a seam colour that
   * stays visible on it. Free text, so only the common Spanish colour words are recognised; anything else keeps the theme's default. */
  const COLOURS = [
    ['blanc', '#f3f3f1'], ['perla', '#efede6'], ['negr', '#2a2b2f'], ['plata', '#c9cbcf'], ['platead', '#c9cbcf'], ['gris', '#8d9096'],
    ['grafit', '#55585e'], ['antracit', '#45484e'], ['azul', '#3f6fb5'], ['marin', '#2a4473'], ['celest', '#7fb2e5'], ['rojo', '#c23b2f'],
    ['roj', '#c23b2f'], ['burdeos', '#7a1f3a'], ['granat', '#7a1f3a'], ['verd', '#3e8c5a'], ['marr', '#7a5230'], ['beig', '#d9c9a7'],
    ['arena', '#d9c9a7'], ['champ', '#d8c79a'], ['naranj', '#e6832b'], ['amarill', '#e7c343'], ['dorad', '#c8a24c'], ['morad', '#6e4a9e'],
    ['violet', '#6e4a9e'], ['lila', '#a98bc9'], ['turques', '#2aa4a0'], ['cobre', '#b5673b'], ['bronce', '#9c7a4a'],
  ];
  function carColour(name) {
    const n = String(name || '').toLowerCase().normalize('NFD').replace(/[̀-ͯ]/g, '');
    if (!n) return null;
    const hit = COLOURS.find(([word]) => n.includes(word));
    return hit ? hit[1] : null;
  }
  function applyCarColour(root) {
    const v = typeof currentVehicle === 'function' ? currentVehicle() : null;
    const hex = carColour(v && v.color);
    if (!hex) { root.style.removeProperty('--cf-body'); root.style.removeProperty('--cf-line'); root.style.removeProperty('--cf-roof'); return; }
    const [r, g, b] = [1, 3, 5].map((i) => parseInt(hex.slice(i, i + 2), 16));
    const dark = (0.299 * r + 0.587 * g + 0.114 * b) < 128;
    root.style.setProperty('--cf-body', hex);
    root.style.setProperty('--cf-line', dark ? 'rgba(255, 255, 255, 0.38)' : 'rgba(0, 0, 0, 0.26)');
    root.style.setProperty('--cf-roof', dark ? 'rgba(255, 255, 255, 0.07)' : 'rgba(0, 0, 0, 0.05)');
  }

  function tyreWarnings(data) {
    const flags = data.tyre_warnings || {};
    const pressures = data.tyre_pressures || {};
    const v = typeof currentVehicle === 'function' ? currentVehicle() : null;
    const ref = { fl: v?.tyre_front_pressure_bar, fr: v?.tyre_front_pressure_bar, rl: v?.tyre_rear_pressure_bar, rr: v?.tyre_rear_pressure_bar };
    const tol = v?.tyre_pressure_tolerance_pct || 10;
    const out = {};
    for (const k of ['fl', 'fr', 'rl', 'rr']) {
      const p = pressures[k];
      const drift = ref[k] && known(p) && p > 0 ? Math.abs((p - ref[k]) / ref[k]) * 100 > tol : false;
      out[k] = Boolean(flags[k]) || drift;
    }
    return out;
  }

  // ───────────────────────── the car as a picture (layers rendered from a 3D model) ─────────────────────────
  /* When this server has the layers of the car's model (/static/car/<model>/layout.json: the body, and each door, window, hood,
   * tailgate and roof in each position, with masks of the paint and of the roof), the figure is drawn with them instead of the
   * plan above: the layer of each part follows what the car reported, the paint takes the colour of the car sheet (a multiply
   * over a mask, so the shading stays) and an SVG on top keeps the pressures, the lock, the cable and what can be tapped.
   * Without them (a server without the pictures) the plan stays as it was. */
  const PHOTO_MODELS = { s700: 's900', s800: 's900', s900: 's900' };     // the S900 pictures stand for the three for now
  const photo = { state: 'idle', model: null, layout: null, root: null, els: {}, last: null };

  // Ebro's own colour names (Carwow, 25-11-2026), before the generic words; hex sampled from the maker's pictures
  const EBRO_COLOURS = [
    ['khaki', '#e4e6ea'], ['nieve', '#eeeeef'], ['phantom', '#6f6d6d'], ['tech', '#6d6a6b'], ['galaxia', '#817b7a'],
    ['bambu', '#68707a'], ['eclipse', '#555858'], ['carbono', '#1c1c1b'], ['tinta', '#1c1c1b'], ['aurora', '#14211d'],
    ['blood', '#b51c27'], ['black', '#1c1c1b'], ['white', '#eeeeef'], ['grey', '#8d9096'], ['gray', '#8d9096'], ['red', '#b51c27'],
    ['green', '#14211d'], ['silver', '#c9cbcf'],
  ];
  /** {body, roof}: the paint of the car sheet's colour and, for a two-tone car («bitono», «… + …», «rojo y negro»), its roof. */
  function photoPaint(name) {
    const n = String(name || '').toLowerCase().normalize('NFD').replace(/[\u0300-\u036f]/g, '');
    if (!n.trim()) return { body: '#d7d9dc', roof: null };
    // Ebro's names win («Verde Aurora» is Aurora's near-black green, not the generic «verde»); the generic words only when none is there
    const find = (list) => {
      const out = [];
      for (const [word, hex] of list) {
        const at = n.indexOf(word);
        if (at >= 0 && !out.some((h) => at >= h.at && at < h.at + h.len)) out.push({ at, len: word.length, hex });
      }
      return out.sort((a, b) => a.at - b.at);
    };
    const own = find(EBRO_COLOURS);
    const hits = own.length ? own : find(COLOURS);
    const body = hits.length ? hits[0].hex : '#d7d9dc';
    const twoTone = /bitono|bi-tono|two.?tone|\+/.test(n) || (hits.length > 1 && hits[1].hex !== body && / y | and /.test(n));
    return { body, roof: twoTone ? (hits.find((h) => h.hex !== body) || { hex: '#1c1c1b' }).hex : null };
  }

  function photoModel() {
    const v = typeof currentVehicle === 'function' ? currentVehicle() : null;
    const m = String(v && v.model || '').toLowerCase().match(/s\s?([789])00/);
    return v && !v.is_manual ? PHOTO_MODELS[m ? `s${m[1]}00` : 's900'] : null;
  }

  // The last layout drawn, kept on the device: on the next load the picture is built at once from it (the images are in the cache),
  // instead of the plan showing for a moment until the data and the layout arrive (25-11-2026). «none»: this server has no pictures.
  const STORE = 'evbro_car_photo';
  const remember = (value) => { try { localStorage.setItem(STORE, JSON.stringify(value)); } catch (e) { /* private mode */ } };
  const recalled = () => { try { return JSON.parse(localStorage.getItem(STORE) || 'null'); } catch (e) { return null; } };

  function decided(withPhoto) {
    const fig = $('carFigure');
    if (!fig) return;
    fig.classList.toggle('has-photo', withPhoto);
    fig.classList.toggle('no-photo', !withPhoto);
  }

  async function loadPhoto() {
    const model = photoModel();
    if (!model || photo.state === 'loading') return;
    const rebuild = !(photo.state === 'ready' && photo.model === model);
    if (rebuild) photo.state = 'loading';
    try {
      const res = await fetch(`/static/car/${model}/layout.json`);
      if (!res.ok) throw new Error(res.status);
      const layout = await res.json();
      remember({ model, layout });
      if (rebuild || JSON.stringify(layout) !== JSON.stringify(photo.layout)) {
        photo.layout = layout;
        photo.model = model;
        buildPhoto();
      }
      showCredit(photo.layout.credit);
      photo.state = 'ready';
      if (photo.last) renderCarFigure(photo.last);
    } catch (e) {
      photo.state = 'none';                               // no pictures on this server: the plan stays
      remember('none');
      decided(false);
    }
  }

  function paintPhoto(paint) {
    if (!photo.root || !paint) return;
    photo.root.style.setProperty('--cf-paint', paint.body);
    photo.root.style.setProperty('--cf-paint-roof', paint.roof || paint.body);
  }

  /** At start-up, before any data: the picture from the last layout kept on the device, or the plan if there were none. */
  function startPhoto() {
    const fig = $('carFigure');
    if (!fig) return;
    const last = recalled();
    if (last === 'none') { decided(false); return; }
    if (last && last.layout && last.model) {
      photo.layout = last.layout;
      photo.model = last.model;
      buildPhoto();
      try { paintPhoto(JSON.parse(localStorage.getItem(`${STORE}_paint`) || 'null')); } catch (e) { /* no colour kept */ }
      photo.state = 'ready';
      photo.checked = false;                              // the layout on the server is read again with the first data
      decided(true);
    }
    setTimeout(() => { if (!fig.classList.contains('has-photo')) decided(false); }, 4000);    // never an empty box
  }
  document.addEventListener('DOMContentLoaded', startPhoto);

  function buildPhoto() {
    const L = photo.layout;
    const base = `/static/car/${photo.model}/`;
    const fig = $('carFigure');
    let root = $('cfPhoto');
    if (!root) {
      root = document.createElement('div');
      root.id = 'cfPhoto';
      root.className = 'cf-photo';
      fig.insertBefore(root, $('carFigureSummary'));
    }
    root.innerHTML = '';
    root.style.aspectRatio = `${L.width} / ${L.height}`;
    photo.els = {};
    for (const key of L.layers) {
      const layer = document.createElement('div');
      layer.className = 'cf-layer';
      layer.dataset.layer = key;
      layer.hidden = !(key === 'base' || key.endsWith('.closed'));      // closed until the car's state arrives
      const img = document.createElement('img');
      img.src = `${base}${key}.webp`;
      img.alt = '';
      img.decoding = 'async';
      layer.appendChild(img);
      for (const [cls, file] of [['cf-tint', `${key}.mask.webp`], ...((L.roof_layers || []).includes(key) ? [['cf-tint cf-tint-roof', `${key}.roof.webp`]] : [])]) {
        const tint = document.createElement('span');
        tint.className = cls;
        tint.style.maskImage = tint.style.webkitMaskImage = `url("${base}${file}")`;
        layer.appendChild(tint);
      }
      root.appendChild(layer);
      photo.els[key] = layer;
    }
    root.appendChild(buildOverlay(L));
    photo.root = root;
  }

  /** Ajustes → Versión: where the car's picture comes from, as the licence of its 3D model asks (title, author, licence, links). */
  function showCredit(c) {
    const box = $('carPictureCredit');
    if (!box || !c || !c.title) return;
    const link = (text, href) => {
      if (!href) return document.createTextNode(text);
      const a = document.createElement('a');
      a.href = href; a.target = '_blank'; a.rel = 'noopener'; a.textContent = text;
      return a;
    };
    box.replaceChildren(document.createTextNode(`${t('Imagen del coche basada en')} «`), link(c.title, c.url), document.createTextNode('», '),
      link(c.author, c.author_url), document.createTextNode(' · '), link(c.license, c.license_url));
    box.hidden = false;
  }

  /** The SVG on top of the picture, in its pixels: highlights of the windows and the roof, what can be tapped, pressures, lock, cable. */
  function buildOverlay(L) {
    const NS = 'http://www.w3.org/2000/svg';
    const el = (tag, attrs, parent) => { const e = document.createElementNS(NS, tag); Object.entries(attrs).forEach(([k, v]) => e.setAttribute(k, v)); if (parent) parent.appendChild(e); return e; };
    const svg = el('svg', { viewBox: `0 0 ${L.width} ${L.height}`, class: 'cf-overlay', role: 'img', 'aria-labelledby': 'carFigureSummary' });
    const P = L.parts;
    const rect = (b, attrs, parent) => el('rect', { x: b[0], y: b[1], width: b[2] - b[0], height: b[3] - b[1], ...attrs }, parent);
    const ACTION = { trunk: 'openTrunkActionSheet()', hood: '', sunroof: 'openSunroofActionSheet()' };
    // window strips: the glass hardly shows from above, so an open or ajar window is drawn in amber over its door
    for (const k of ['fl', 'fr', 'rl', 'rr']) {
      const b = P[`win-${k}`];
      if (!b) continue;
      const top = k === 'fr' || k === 'rr';                // the car's right side is the top of the picture
      const h = 9, y = top ? b[1] + 2 : b[3] - 2 - h;
      const g = el('g', { class: 'cf-hl cf-hl-window', 'data-part': `win-${k}`, 'data-state': 'closed', 'data-on-click': 'openWindowsActionSheet()' }, svg);
      el('rect', { x: b[0] + 6, y, width: b[2] - b[0] - 12, height: h, rx: 4.5 }, g);
      el('title', {}, g).textContent = PART_LABELS[`win-${k}`];
    }
    if (P.sunroof) {
      const g = el('g', { class: 'cf-hl cf-hl-roof', 'data-part': 'sunroof', 'data-state': 'closed', 'data-on-click': ACTION.sunroof }, svg);
      rect(P.sunroof, { rx: 10 }, g);
      el('title', {}, g).textContent = PART_LABELS.sunroof;
    }
    // a part the car has not reported: drawn closed (its picture would otherwise show the engine bay or the cabin through it),
    // with a dashed grey outline so it is not taken for «closed». Not the hood: many cars never report it (as in the summary).
    for (const part of ['door-fl', 'door-fr', 'door-rl', 'door-rr', 'trunk']) {
      if (P[part]) rect(P[part], { class: 'cf-unknown', 'data-part': part, 'data-state': 'closed', rx: 14 }, svg);
    }
    // what can be tapped (invisible), from the closed parts
    for (const [part, action] of [['trunk', ACTION.trunk], ['door-fl', 'openWindowsActionSheet()'], ['door-fr', 'openWindowsActionSheet()'],
      ['door-rl', 'openWindowsActionSheet()'], ['door-rr', 'openWindowsActionSheet()']]) {
      if (P[part] && action) rect(P[part], { class: 'cf-hit', 'data-on-click': action }, svg);
    }
    // pressure and temperature outside the body, beside each wheel (two lines; one when the car sends no temperature)
    const body = L.body;
    for (const [k, w] of Object.entries(L.wheels || {})) {
      const top = k.endsWith('r');
      const g = el('g', { class: 'cf-pill', 'data-pill': k, 'data-state': 'unknown', transform: `translate(${w[0]} ${top ? body[1] - 36 : body[3] + 36})` }, svg);
      el('rect', { class: 'cf-pill-box', x: -52, y: -29, width: 104, height: 58, rx: 22 }, g);
      const text = el('text', { class: 'cf-tyre', y: -1 }, g);
      text.id = `cfPhotoTyre${k.toUpperCase()}`;
      const temp = el('text', { class: 'cf-tyre-temp', y: 21 }, g);
      temp.id = `cfPhotoTemp${k.toUpperCase()}`;
    }
    // lock on the hood, cable at the rear left corner (as in the plan)
    const hood = P.hood || body;
    const lock = el('g', { class: 'cf-lock', 'data-part': 'lock', 'data-state': 'unknown', 'data-on-click': 'toggleLockState()',
      transform: `translate(${(hood[0] + hood[2]) / 2 - 20} ${(hood[1] + hood[3]) / 2 - 22}) scale(2)` }, svg);
    el('title', {}, lock).textContent = 'Cierre centralizado';
    el('path', { class: 'cf-lock-shackle', d: 'M6 10V7a4 4 0 0 1 8 0v3' }, lock);
    el('rect', { x: 3, y: 10, width: 14, height: 11, rx: 2.5 }, lock);
    // the charge port: on the left rear wing, just behind the wheel (moved forward from the corner, 25-11-2026)
    const port = [body[2] - 98, body[3] + 10];
    // with the cable plugged in, a short hose from the port down and back to a charging gun beside the rear wheel's pressure,
    // with the power and the time the charge ends under the gun: the home page can do without the charge widget. All of it
    // inside the picture, so it never runs into the summary under it however long that gets.
    const hose = el('g', { class: 'cf-hose', 'data-part': 'hose', 'data-state': 'off', 'data-on-click': "switchTab('charges')" }, svg);
    el('title', {}, hose).textContent = t('Ver la pantalla de carga');
    // the hose runs down and along, then turns up into the tail of a charging gun (drawn in its own box, scaled down: head with
    // the connector in front, a slanted grip and two collars where the cable goes in)
    const end = [port[0] + 40, HOSE_Y];
    el('path', { class: 'cf-hose-line', d: `M${port[0]} ${port[1]} V${HOSE_Y - 18} Q${port[0]} ${HOSE_Y} ${port[0] + 18} ${HOSE_Y} `
      + `H${end[0] - 10} Q${end[0]} ${end[1]} ${end[0] + 6} ${end[1] - 6}` }, hose);
    const GUN_SCALE = 0.25, GUN_TAIL = [43.6, 211.4], GUN_X = [40, 384];      // the gun's own box: tail point and left/right edges
    const gunAt = [end[0] + 6 - GUN_TAIL[0] * GUN_SCALE, end[1] - 6 - GUN_TAIL[1] * GUN_SCALE];
    const gun = el('g', { class: 'cf-hose-plug', transform: `translate(${gunAt[0]} ${gunAt[1]}) scale(${GUN_SCALE})` }, hose);
    el('path', { d: 'M185 20 Q195 12 212 12 H308 Q322 12 322 26 V140 H268 V128 H218 L95 195 L60 160 Z' }, gun);      // head and grip
    el('rect', { x: 332, y: 30, width: 52, height: 62, rx: 10 }, gun);                                              // connector
    el('path', { d: 'M57.7 166.1 L88.9 197.3 L74 205 L50 181 Z M46.4 188.8 L66.2 208.6 L55.6 215 L40 199.4 Z' }, gun);   // collars
    // under the gun, centred on it: the power and, below it, the time the charge ends (renderHose)
    const under = gunAt[0] + (GUN_X[0] + GUN_X[1]) / 2 * GUN_SCALE;
    el('text', { id: 'cfHosePower', class: 'cf-hose-power', x: under, y: HOSE_Y + 40 }, hose);
    el('text', { id: 'cfHoseEnd', class: 'cf-hose-end', x: under, y: HOSE_Y + 72, 'data-x': under }, hose);
    const cable = el('g', { class: 'cf-cable', 'data-part': 'cable', 'data-state': 'off', transform: `translate(${port[0]} ${port[1]}) scale(2.2)` }, svg);
    el('title', {}, cable).textContent = 'Cable de carga';
    el('circle', { r: 7.5 }, cable);
    el('path', { d: 'm-3 -1 h3 m-3 3 h3 m0 -5.5 v8 m0 -4 h3.5' }, cable);      // the plan's plug, in relative steps
    return svg;
  }

  /** The hose beside the car: shown with the cable plugged in, green while charging; under the gun the power and the time the
   * charge ends («18:45», «mañana 06:10»; «Sin cargar» while plugged in but not charging). Voltage and current: the Carga page. */
  function renderHose(root, data) {
    const hose = root.querySelector('[data-part="hose"]');
    if (!hose) return;
    const plugged = data.cable_connected === true;
    const charging = plugged && !!data.is_charging;
    hose.dataset.state = !plugged ? 'off' : charging ? 'charging' : 'plugged';
    if (!plugged) return;
    const num = (v, d = 1) => (typeof historyNum === 'function' ? historyNum(v, d) : String(v));
    const kw = Number(data.charge_power_kw);
    $('cfHosePower').textContent = `${num(Number.isFinite(kw) ? kw : 0)} kW`;
    const min = Number(data.remaining_charge_time_min);
    const endLine = $('cfHoseEnd');
    const ends = min > 0 && typeof chargeClock === 'function' ? chargeClock(min) : '';
    // on its own line it starts with a capital («Mañana 06:10», «Jue 06:10»); in the charge widget it goes mid-sentence
    endLine.textContent = !charging ? t('Sin cargar') : ends.charAt(0).toUpperCase() + ends.slice(1);
    // a long one («mañana 06:10») would run past the picture's right edge: moved left just enough
    const x = Number(endLine.dataset.x);
    endLine.setAttribute('x', x);
    try {
      const half = endLine.getComputedTextLength() / 2, right = photo.layout.width - 4;
      if (x + half > right) endLine.setAttribute('x', right - half);
    } catch (e) { /* not laid out yet: stays centred */ }
  }

  /** Window and roof positions: 0 closed; windows 1-2 ajar («Ventilar»), 3/10 open; roof 2 ajar (tilted), 3 open. */
  const windowLevel = (v) => (!known(v) ? 'unknown' : Number(v) === 0 ? 'closed' : [1, 2].includes(Number(v)) ? 'vent' : 'open');
  const roofLevel = (v) => (!known(v) ? 'unknown' : Number(v) === 0 ? 'closed' : Number(v) === 2 ? 'tilt' : 'open');

  function renderPhoto(data) {
    const root = photo.root;
    if (!root) return;
    const doors = data.doors || {};
    const windows = data.windows || {};
    const paint = photoPaint((typeof currentVehicle === 'function' ? currentVehicle() : null)?.color);
    paintPhoto(paint);
    try { localStorage.setItem(`${STORE}_paint`, JSON.stringify(paint)); } catch (e) { /* private mode */ }
    const show = (key, on, state) => {
      const layer = photo.els[key];
      if (!layer) return;
      layer.hidden = !on;
      if (state) layer.dataset.state = state;
    };
    show('base', true);
    const panel = (part, value) => {
      const state = !known(value) ? 'unknown' : value ? 'open' : 'closed';
      show(`${part}.closed`, state !== 'open', state);
      show(`${part}.open`, state === 'open', state);
      const mark = root.querySelector(`.cf-unknown[data-part="${part}"]`);
      if (mark) mark.dataset.state = state;
    };
    ['hood', 'trunk'].forEach((p) => panel(p, doors[p]));
    for (const k of ['fl', 'fr', 'rl', 'rr']) {
      panel(`door-${k}`, doors[k]);
      const w = windowLevel(windows[k]);
      const doorOpen = known(doors[k]) && Boolean(doors[k]);
      show(`win-${k}.closed`, !doorOpen && (w === 'closed' || w === 'unknown'), w);
      show(`win-${k}.vent`, !doorOpen && w === 'vent', w);
      const hl = root.querySelector(`.cf-hl[data-part="win-${k}"]`);
      if (hl) hl.dataset.state = w;
    }
    const r = roofLevel(windows.sunroof);
    show('sunroof.closed', r === 'closed' || r === 'unknown', r);
    show('sunroof.tilt', r === 'tilt', r);
    show('sunroof.open', r === 'open', r);
    const roofHl = root.querySelector('.cf-hl[data-part="sunroof"]');
    if (roofHl) roofHl.dataset.state = r;
    const lock = root.querySelector('[data-part="lock"]');
    if (lock) lock.dataset.state = data.is_locked === true ? 'locked' : data.is_locked === false ? 'unlocked' : 'unknown';
    const cable = root.querySelector('[data-part="cable"]');
    if (cable) cable.dataset.state = data.cable_connected === true ? (data.is_charging ? 'charging' : 'plugged') : 'off';
    renderHose(root, data);
    const warn = tyreWarnings(data);
    const tp = data.tyre_pressures || {};
    const tt = data.tyre_temps || {};
    for (const k of ['fl', 'fr', 'rl', 'rr']) {
      const bar = tp[k];
      const has = known(bar) && bar > 0;
      const temp = Number(tt[k]);
      const hasTemp = known(tt[k]) && Number.isFinite(temp) && temp > -40 && temp < 120;
      const pill = root.querySelector(`.cf-pill[data-pill="${k}"]`);
      if (pill) {
        pill.dataset.state = !has ? 'unknown' : warn[k] ? 'warn' : 'ok';
        const box = pill.querySelector('.cf-pill-box');            // one line without a temperature: a shorter pill
        if (box) { box.setAttribute('y', hasTemp ? -29 : -20); box.setAttribute('height', hasTemp ? 58 : 40); }
      }
      const text = $(`cfPhotoTyre${k.toUpperCase()}`);
      if (text) { text.textContent = has ? `${historyNum(bar, 1)} bar` : ''; text.setAttribute('y', hasTemp ? -1 : 8); }
      const tline = $(`cfPhotoTemp${k.toUpperCase()}`);
      if (tline) tline.textContent = has && hasTemp ? `${Math.round(temp)} °C` : '';
    }
  }

  function renderCarFigure(data) {
    const root = $('carFigure');
    if (!root || !data) return;
    photo.last = data;
    const wanted = photoModel();
    if (wanted && (photo.state === 'idle' || (photo.state === 'ready' && (photo.model !== wanted || photo.checked === false)))) {
      photo.checked = true;
      loadPhoto();
    }
    const withPhoto = Boolean(wanted) && photo.state === 'ready' && photo.model === wanted;
    if (withPhoto || !wanted || photo.state === 'none') decided(withPhoto);
    if (withPhoto) renderPhoto(data);
    const doors = data.doors || {};
    const windows = data.windows || {};
    applyCarColour(root);

    const states = {
      hood: doors.hood, trunk: doors.trunk, sunroof: windows.sunroof,
      'door-fl': doors.fl, 'door-fr': doors.fr, 'door-rl': doors.rl, 'door-rr': doors.rr,
      'win-fl': windows.fl, 'win-fr': windows.fr, 'win-rl': windows.rl, 'win-rr': windows.rr,
    };
    Object.entries(states).forEach(([part, value]) => setState(part, value));

    // Lock: a separate three-way state (locked / unlocked / unknown)
    const lock = root.querySelector('[data-part="lock"]');
    if (lock) lock.dataset.state = data.is_locked === true ? 'locked' : data.is_locked === false ? 'unlocked' : 'unknown';
    const cable = root.querySelector('[data-part="cable"]');
    if (cable) cable.dataset.state = data.cable_connected === true ? (data.is_charging ? 'charging' : 'plugged') : 'off';

    const warn = tyreWarnings(data);
    const tp = data.tyre_pressures || {};
    for (const k of ['fl', 'fr', 'rl', 'rr']) setTyre(k, tp[k], warn[k]);

    // One line: what is open, or «Todo cerrado», or «Sin datos» when the car has not reported anything yet.
    // When some parts are closed and others never reported, it names the missing ones instead of assuming they are closed.
    // The hood is left out of that list: many cars never report it, and «sin dato de capó» on every reading was noise
    // (owner, 2026-11-01). It still shows open when the car says so, and stays dashed grey in the drawing without data.
    const values = Object.values(states);
    // Each label translated on its own (the joined line is no key of the dictionaries); a window or the roof left ajar says so
    const label = (k, v) => {
      if (k.startsWith('win-') && [1, 2].includes(Number(v))) return t('{p0} entreabierta', {p0: t(PART_LABELS[k])});
      if (k === 'sunroof' && Number(v) === 2) return t('Techo entreabierto');
      return t(PART_LABELS[k]);
    };
    const open = Object.entries(states).filter(([, v]) => known(v) && Boolean(v)).map(([k, v]) => label(k, v));   // the car sends 0/1
    const pending = Object.entries(states).filter(([k, v]) => !known(v) && k !== 'hood').map(([k]) => t(PART_LABELS[k]));
    const summary = $('carFigureSummary');
    if (summary) {
      if (values.every((v) => !known(v))) { summary.textContent = 'Sin datos del coche'; summary.dataset.state = 'unknown'; }
      else if (open.length) { summary.textContent = open.join(' · '); summary.dataset.state = 'open'; }
      else if (!pending.length) { summary.textContent = 'Todo cerrado'; summary.dataset.state = 'closed'; }
      else { summary.textContent = t('Cerrado · sin dato de {p0}', {p0: pending.join(', ').toLowerCase()}); summary.dataset.state = 'closed'; }
    }
  }

  window.renderCarFigure = renderCarFigure;
})();
