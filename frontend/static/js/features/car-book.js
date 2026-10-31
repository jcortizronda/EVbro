/* EVBro — Libro del coche: un PDF con las secciones que elijas (Coches → ficha → «Libro del coche»).
 * (Classic script: shares the global scope with the other files; see the load order in templates/index.html.)
 */
(function () {
  const el = (id) => document.getElementById(id);

  function open() { el('bookModal').classList.add('open'); }
  function close() { el('bookModal').classList.remove('open'); }
  function all(on) { el('bookModal').querySelectorAll('input[name="bookSection"]').forEach((c) => { c.checked = on; }); }

  async function download() {
    const sections = [...el('bookModal').querySelectorAll('input[name="bookSection"]:checked')].map((c) => c.value);
    if (!sections.length) { showToast('Elige al menos una sección.', 'warning'); return; }
    const btn = el('bookDownload');
    btn.disabled = true;
    btn.textContent = 'Preparando…';
    try {
      const res = await fetch(`/api/vehicles/${currentVin}/book`, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ sections }) });
      if (!res.ok) { const d = await res.json().catch(() => ({})); throw new Error(d.detail || res.status); }
      const blob = await res.blob();
      const name = (res.headers.get('Content-Disposition') || '').match(/filename\*?=(?:UTF-8'')?"?([^";]+)/);
      const a = document.createElement('a');
      a.href = URL.createObjectURL(blob);
      a.download = name ? decodeURIComponent(name[1]) : 'libro-del-coche.pdf';
      document.body.appendChild(a);
      a.click();
      setTimeout(() => { URL.revokeObjectURL(a.href); a.remove(); }, 2000);
      close();
      showToast('Libro del coche descargado', 'success');
    } catch (e) {
      showToast(t('No se pudo generar el libro: {p0}', {p0: e.message || e}), 'error', 6000);
    } finally {
      btn.disabled = false;
      btn.textContent = 'Descargar PDF';
    }
  }

  window.CarBook = { open, close, all, download };
})();
