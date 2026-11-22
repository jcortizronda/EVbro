/* EVBro — Tus datos (Ajustes → Cuenta): download everything as a ZIP, import an EVBro ZIP into this account, and delete the account.
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

  // ───────────────────────── deleting the account ─────────────────────────
  // It cannot be undone: the button stays off until the username is typed by hand, and the server also asks for the password.
  const typedName = () => el('adDeleteConfirm').value.trim().toLowerCase();
  const ownName = () => String(EbroAuth.username || '').toLowerCase();

  function deleteError(message) {
    const box = el('adDeleteError');
    box.textContent = message || '';
    box.hidden = !message;
  }

  function checkDelete() {
    el('adDeleteSubmit').disabled = !ownName() || typedName() !== ownName() || !el('adDeletePassword').value;
  }

  function openDelete() {
    el('adDeleteConfirm').value = '';
    el('adDeletePassword').value = '';
    el('adDeleteComment').value = '';
    document.querySelectorAll('input[name="adReason"]').forEach((box) => { box.checked = false; });
    el('adDeleteConfirmLabel').textContent = t('Escribe «{p0}» para confirmar', {p0: EbroAuth.username || ''});
    deleteError('');
    checkDelete();
    el('accountDeleteModal').classList.add('open');
    el('accountDeleteModal').querySelector('.modal-card').scrollTop = 0;
  }

  function closeDelete() {
    el('accountDeleteModal').classList.remove('open');
    el('adDeletePassword').value = '';
  }

  /** What this browser kept of the account: the offline copy of its data and the choices tied to its cars (theme and colour stay). */
  async function forgetAccountHere() {
    try {
      Object.keys(localStorage).filter((k) => k.startsWith('evbro_') && k !== 'evbro_theme' && k !== 'evbro_accent').forEach((k) => localStorage.removeItem(k));
    } catch (e) { /* private mode: nothing was kept */ }
    try {
      if (window.caches) await caches.delete('evbro-api-v1');
      const reg = navigator.serviceWorker && await navigator.serviceWorker.getRegistration();
      const sub = reg && reg.pushManager && await reg.pushManager.getSubscription();
      if (sub) await sub.unsubscribe();
    } catch (e) { /* best effort: the server no longer has anything to send or to show */ }
  }

  async function submitDelete(event) {
    event.preventDefault();
    if (el('adDeleteSubmit').disabled) return;
    const btn = el('adDeleteSubmit');
    btn.disabled = true;
    deleteError('');
    try {
      const res = await fetch('/api/account/delete', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          confirm: typedName(), password: el('adDeletePassword').value,
          // the optional survey: why the account is deleted
          reasons: [...document.querySelectorAll('input[name="adReason"]:checked')].map((box) => box.value),
          comment: el('adDeleteComment').value.trim() || null,
        }),
      });
      const data = await res.json().catch(() => ({}));
      if (!res.ok) {
        deleteError(typeof data.detail === 'string' ? data.detail : t('No se pudo borrar la cuenta.'));
        checkDelete();
        return;
      }
      await forgetAccountHere();
      window.location.reload();                                  // no session any more: the sign-in screen
    } catch (e) {
      deleteError(t('No hay conexión con el servidor.'));
      checkDelete();
    }
  }

  document.addEventListener('DOMContentLoaded', () => {
    const form = el('accountDeleteForm');
    if (!form) return;
    form.addEventListener('submit', submitDelete);
    ['adDeleteConfirm', 'adDeletePassword'].forEach((id) => el(id).addEventListener('input', checkDelete));
  });

  window.AccountData = { download, pick, importFile, openDelete, closeDelete };
})();
