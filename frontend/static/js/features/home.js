/* EVBro — home tab access tiles (lock, boot, windows, sunroof).
 *
 * Each tile changes ICON with the state (padlock locked/unlocked, tailgate closed/raised, window up/down,
 * sunroof closed/open) and the icon changes COLOUR (green = closed, amber = open, grey = no data); the button
 * stays neutral. If the car has not reported yet it says «Sin datos»: nothing is ever assumed closed without knowing.
 */
(function () {
  'use strict';

  const SVG = (inner) => `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">${inner}</svg>`;
  const ICONS = {
    lock: {
      closed: SVG('<rect x="4.5" y="10.5" width="15" height="10" rx="2.5"/><path d="M8 10.5V7.5a4 4 0 0 1 8 0v3"/><circle cx="12" cy="15.5" r="1" fill="currentColor"/>'),
      open: SVG('<rect x="4.5" y="10.5" width="15" height="10" rx="2.5"/><path d="M8 10.5V7.5a4 4 0 0 1 7.6-1.7"/><circle cx="12" cy="15.5" r="1" fill="currentColor"/>'),
    },
    trunk: {   // rear view: tailgate closed / tailgate raised
      closed: SVG('<path d="M4.5 17.5v-7a2.5 2.5 0 0 1 2.5-2.5h10a2.5 2.5 0 0 1 2.5 2.5v7"/><path d="M3 17.5h18"/><path d="M7.5 12h9"/><circle cx="7.5" cy="19.8" r="1.3"/><circle cx="16.5" cy="19.8" r="1.3"/>'),
      open: SVG('<path d="M5 17.5V13h14v4.5"/><path d="M3 17.5h18"/><path d="M6.5 10.2 8.5 4h7l2 6.2"/><path d="M9 7h6"/><circle cx="7.5" cy="19.8" r="1.3"/><circle cx="16.5" cy="19.8" r="1.3"/>'),
    },
    windows: {   // window up (with reflection) / window down (down arrow)
      closed: SVG('<rect x="4" y="3.5" width="16" height="17" rx="3"/><path d="M8.5 16.5 15.5 9.5M8.5 12l3.5-3.5"/>'),
      open: SVG('<rect x="4" y="3.5" width="16" height="17" rx="3"/><path d="M4 11h16"/><path d="M12 13.5v4.5M9.8 16l2.2 2.2 2.2-2.2"/>'),
    },
    sunroof: {   // sunroof closed (glass) / sunroof open (sun)
      closed: SVG('<rect x="3" y="5.5" width="18" height="13" rx="4"/><rect x="7" y="9" width="10" height="6" rx="1.5"/>'),
      open: SVG('<rect x="3" y="5.5" width="18" height="13" rx="4"/><circle cx="12" cy="12" r="2.4"/><path d="M12 7.6v.9M12 15.5v.9M7.6 12h.9M15.5 12h.9"/>'),
    },
  };

  const $ = (id) => document.getElementById(id);

  /** Draws a tile: icon, state (data-state, which colours the icon) and its text. */
  function setTile(buttonId, state, icon, subtitleId, subtitle) {
    const button = $(buttonId);
    if (!button) return;
    button.dataset.state = state;
    const wrap = button.querySelector('.card-icon-wrap');
    if (wrap && wrap.dataset.icon !== `${state}`) { wrap.innerHTML = icon; wrap.dataset.icon = state; }
    const sub = $(subtitleId);
    if (sub) sub.textContent = subtitle;
  }
  const known = (v) => v !== null && v !== undefined;

  /** Number of open windows (out of 4), or null if the car has not reported any yet. */
  function openWindows(windows) {
    const vals = [windows.fl, windows.fr, windows.rl, windows.rr];
    if (vals.every((v) => !known(v))) return null;
    return vals.filter(Boolean).length;
  }
  const windowsText = (n) => (n === null ? 'Sin datos' : n === 0 ? 'Cerradas' : n === 1 ? '1 abierta' : `${n} abiertas`);

  function renderHome(data) {
    // Central locking
    const title = $('cardLockAction');
    if (data.is_locked === true) {
      setTile('btnLockAction', 'closed', ICONS.lock.closed, 'cardLockStatus', 'Cerrado');
    } else if (data.is_locked === false) {
      setTile('btnLockAction', 'open', ICONS.lock.open, 'cardLockStatus', 'Abierto');
    } else {
      setTile('btnLockAction', 'unknown', ICONS.lock.closed, 'cardLockStatus', 'Sin datos');
    }
    if (title) title.textContent = 'Cierre';

    const doors = data.doors || {};
    const windows = data.windows || {};
    const tile = (value, buttonId, icons, subtitleId, openText, closedText) => {
      if (!known(value)) setTile(buttonId, 'unknown', icons.closed, subtitleId, 'Sin datos');
      else if (value) setTile(buttonId, 'open', icons.open, subtitleId, openText);
      else setTile(buttonId, 'closed', icons.closed, subtitleId, closedText);
    };
    // The tile title already says what it is: the state only says Abierto/Cerrado (or how many windows).
    tile(doors.trunk, 'btnTrunkCard', ICONS.trunk, 'cardTrunkStatus', 'Abierto', 'Cerrado');
    const open = openWindows(windows);
    tile(open === null ? null : open > 0, 'btnWindowsCard', ICONS.windows, 'cardWindowsStatus', windowsText(open), 'Cerradas');
    tile(windows.sunroof, 'btnSunroofCard', ICONS.sunroof, 'cardSunroofStatus', 'Abierto', 'Cerrado');
    if (window.HomeWidgets) HomeWidgets.onTelemetry(data);     // the climate widget shows the temperatures the car just sent
  }

  window.renderHome = renderHome;
  window.windowsStateText = (windows) => windowsText(openWindows(windows || {}));
})();
