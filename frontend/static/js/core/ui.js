/* EVBro — interface helpers for phones.
 *
 * 1. Tables as cards: on narrow screens the CSS (`.data-table`) shows each row as a card and puts
 *    the column name before each cell. That name comes from the <thead>: here it is copied to
 *    `data-label` on every cell, whatever code rendered the table.
 * 2. Sub-tab bars scroll horizontally: the active one is centred when tapped.
 */
(function () {
  'use strict';

  function labelTables() {
    document.querySelectorAll('table.data-table').forEach((table) => {
      const heads = Array.from(table.querySelectorAll('thead th')).map((th) => th.textContent.trim());
      if (!heads.length) return;
      table.querySelectorAll('tbody tr').forEach((row) => {
        let col = 0;
        Array.from(row.children).forEach((cell) => {
          if (cell.colSpan > 1) { col += cell.colSpan; return; }   // «Cargando…», «Sin datos» (placeholder rows)
          const label = heads[col];
          if (label && cell.dataset.label !== label) cell.dataset.label = label;
          col += 1;
        });
      });
    });
  }

  // At most one pass per frame, even if the table changes many times in a row.
  let queued = false;
  function schedule() {
    if (queued) return;
    queued = true;
    requestAnimationFrame(() => { queued = false; labelTables(); });
  }

  document.addEventListener('DOMContentLoaded', () => {
    schedule();
    // Only node additions/removals are observed: setting `data-label` is an attribute and does not re-trigger this.
    new MutationObserver(schedule).observe(document.body, { childList: true, subtree: true });
  });

  document.addEventListener('click', (event) => {
    const btn = event.target.closest('.sub-nav-btn');
    if (btn && btn.scrollIntoView) btn.scrollIntoView({ inline: 'center', block: 'nearest', behavior: 'smooth' });
  });
})();
