/* EVBro — Compact top-view car figure on the home tab: doors, hood, trunk, windows, sunroof, lock, charge cable and the four
 * tyre pressures, each coloured by what the car last reported (amber = open, neutral = closed, dashed grey = no data).
 * Tapping a part opens the matching action (windows, trunk, sunroof, lock). Nothing here assumes «closed» without data.
 * (Classic script: shares the global scope; telemetry.js calls `renderCarFigure(data)` after each reading.)
 */
(function () {
  'use strict';

  const $ = (id) => document.getElementById(id);
  const known = (v) => v !== null && v !== undefined;

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
    if (text) text.textContent = has ? bar.toFixed(1) : '';
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

  function renderCarFigure(data) {
    const root = $('carFigure');
    if (!root || !data) return;
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
    const open = Object.entries(states).filter(([, v]) => known(v) && Boolean(v)).map(([k]) => PART_LABELS[k]);   // the car sends 0/1
    const pending = Object.entries(states).filter(([k, v]) => !known(v) && k !== 'hood').map(([k]) => PART_LABELS[k]);
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
