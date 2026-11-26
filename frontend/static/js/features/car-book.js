/* EVbro — Car book: a PDF with the sections you pick (Coches → sheet → «Libro del coche»).
 * (Classic script: shares the global scope with the other files; see the load order in templates/index.html.)
 */
(function () {
  const el = (id) => document.getElementById(id);

  function open() { el('bookModal').classList.add('open'); }
  function close() { el('bookModal').classList.remove('open'); }
  function all(on) { el('bookModal').querySelectorAll('input[name="bookSection"]').forEach((c) => { c.checked = on; }); }

  /** A plain link to the book: the browser's own download manager saves it. Fetching the PDF and saving it from memory (a `blob:`
   *  link) left a black screen on some phone browsers (Firefox for Android). The server takes a few seconds to build it. */
  function download() {
    const sections = [...el('bookModal').querySelectorAll('input[name="bookSection"]:checked')].map((c) => c.value);
    if (!sections.length) { showToast('Elige al menos una sección.', 'warning'); return; }
    const a = document.createElement('a');
    a.href = `/api/vehicles/${encodeURIComponent(currentVin)}/book?sections=${encodeURIComponent(sections.join(','))}`;
    a.download = '';
    document.body.appendChild(a);
    a.click();
    a.remove();
    close();
    showToast('Preparando el libro del coche: la descarga empezará en unos segundos.', 'info', 6000);
  }

  window.CarBook = { open, close, all, download };
})();
