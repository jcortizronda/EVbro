/* EVBro — Tus datos (Ajustes → Cuenta): descargar todo en un ZIP e importar un ZIP de EVBro en esta cuenta.
 * (Classic script: shares the global scope with the other files; see the load order in templates/index.html.)
 */
(function () {
  const el = (id) => document.getElementById(id);

  async function download() {
    const btn = el('adExport');
    btn.disabled = true;
    try {
      const res = await fetch('/api/account/export');
      if (!res.ok) throw new Error(res.status);
      const blob = await res.blob();
      const name = (res.headers.get('Content-Disposition') || '').match(/filename\*?=(?:UTF-8'')?"?([^";]+)/);
      const a = document.createElement('a');
      a.href = URL.createObjectURL(blob);
      a.download = name ? decodeURIComponent(name[1]) : 'evbro-export.zip';
      document.body.appendChild(a);
      a.click();
      setTimeout(() => { URL.revokeObjectURL(a.href); a.remove(); }, 2000);
      showToast('Exportación descargada. Guárdala en un sitio seguro: lleva tus documentos y fotos.', 'success', 6000);
    } catch (e) {
      showToast(t('No se pudo exportar: {p0}', {p0: e.message || e}), 'error', 6000);
    } finally {
      btn.disabled = false;
    }
  }

  function pick() { el('adImportFile').click(); }

  async function importFile(input) {
    const file = input.files && input.files[0];
    input.value = '';
    if (!file) return;
    if (!await confirmDialog(t('¿Importar «{p0}» en esta cuenta? Los coches que ya existan se saltan; el resto se añade como coches sin cuenta de Ebro.', {p0: file.name}))) return;
    const box = el('adImportReport');
    box.hidden = false;
    box.textContent = 'Importando… puede tardar un poco si lleva muchos ficheros.';
    try {
      const form = new FormData();
      form.append('file', file);
      const res = await fetch('/api/account/import', { method: 'POST', body: form });
      const data = await res.json().catch(() => ({}));
      if (!res.ok) throw new Error(data.detail || res.status);
      const lines = data.vehicles.map((v) => t('{p0}: {p1} viajes, {p2} cargas, {p3} repostajes, {p4} gastos, {p5} intervenciones, {p6} piezas, {p7} documentos', {p0: v.name || v.vin, p1: v.drives, p2: v.charges, p3: v.refuels, p4: v.expenses, p5: v.maintenance, p6: v.parts, p7: v.documents}));
      data.skipped.forEach((s) => lines.push(t('{p0}: no importado ({p1})', {p0: s.vin, p1: s.reason})));
      if (data.geofences) lines.push(t('{p0} geocercas nuevas', {p0: data.geofences}));
      box.textContent = lines.length ? lines.join('\n') : 'El fichero no traía nada que importar.';
      showToast('Importación terminada', 'success');
      if (typeof loadVehicles === 'function') loadVehicles();
    } catch (e) {
      box.textContent = t('No se pudo importar: {p0}', {p0: e.message || e});
    }
  }

  window.AccountData = { download, pick, importFile };
})();
