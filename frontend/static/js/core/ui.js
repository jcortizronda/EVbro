/* EVBro — ayudas de interfaz para móvil.
 *
 * 1. Tablas como tarjetas: en pantallas estrechas el CSS (`.data-table`) muestra cada fila como una
 *    tarjeta y antepone a cada celda el nombre de su columna. Ese nombre sale del <thead>: aquí se
 *    copia a `data-label` en cada celda, sea cual sea el código que haya pintado la tabla.
 * 2. Las barras de subpestañas se desplazan en horizontal: la activa se centra al pulsarla.
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
          if (cell.colSpan > 1) { col += cell.colSpan; return; }   // «Cargando…», «Sin datos»
          const label = heads[col];
          if (label && cell.dataset.label !== label) cell.dataset.label = label;
          col += 1;
        });
      });
    });
  }

  // Una pasada por fotograma como máximo, aunque la tabla cambie muchas veces seguidas.
  let queued = false;
  function schedule() {
    if (queued) return;
    queued = true;
    requestAnimationFrame(() => { queued = false; labelTables(); });
  }

  document.addEventListener('DOMContentLoaded', () => {
    schedule();
    // Solo se observan altas/bajas de nodos: poner `data-label` es un atributo y no re-dispara esto.
    new MutationObserver(schedule).observe(document.body, { childList: true, subtree: true });
  });

  document.addEventListener('click', (event) => {
    const btn = event.target.closest('.sub-nav-btn');
    if (btn && btn.scrollIntoView) btn.scrollIntoView({ inline: 'center', block: 'nearest', behavior: 'smooth' });
  });
})();
