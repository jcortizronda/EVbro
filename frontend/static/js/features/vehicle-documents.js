/* EVBro — Documentos del coche (Ajustes → Coche): factura de compra, póliza del seguro y otros (permiso de circulación, ficha técnica).
 * Se suben en el momento (no dependen de «Guardar cambios»). El servidor decide el tipo por el contenido y solo deja subir y borrar al dueño.
 * (Script clásico: comparte el ámbito global con los demás ficheros; ver el orden de carga en templates/index.html.)
 */

const VDOC_KIND_LABELS = {
  purchase_invoice: 'Factura de compra',
  insurance_policy: 'Póliza del seguro',
  registration: 'Permiso de circulación',
  technical_sheet: 'Ficha técnica',
  other: 'Otro documento',
};
const VDOC_LISTS = { purchase_invoice: 'vdocListPurchase', insurance_policy: 'vdocListPolicy', registration: 'vdocListOther', technical_sheet: 'vdocListOther', other: 'vdocListOther' };
const VDOC_MAX_BYTES = 15 * 1024 * 1024;
let vdocLoadToken = 0;          // a newer load (another car) wins over a slower older one

function vdocFileType(contentType) {
  return { 'application/pdf': 'PDF', 'image/jpeg': 'JPG', 'image/png': 'PNG', 'image/webp': 'WebP' }[contentType] || 'Fichero';
}

function vdocSize(bytes) {
  if (bytes >= 1024 * 1024) return `${(bytes / (1024 * 1024)).toLocaleString(I18n.locale, { maximumFractionDigits: 1 })} MB`;
  return `${Math.max(1, Math.round(bytes / 1024)).toLocaleString(I18n.locale)} KB`;
}

function vdocDate(iso) {
  const d = serverDate(iso);
  return isNaN(d) ? '' : d.toLocaleDateString(I18n.locale, { day: 'numeric', month: 'short', year: 'numeric' });
}

function vdocItem(doc) {
  const li = document.createElement('li');
  li.className = 'vdoc-item';
  li.dataset.id = doc.id;

  const link = document.createElement('a');
  link.className = 'vdoc-open';
  link.href = doc.url;
  link.target = '_blank';
  link.rel = 'noopener';
  // Opened inside the app: leaving the page to the PDF made «atrás» restart the app (PWA). The href stays for «abrir en otra pestaña».
  link.addEventListener('click', (ev) => {
    if (typeof openFileViewer !== 'function') return;
    ev.preventDefault();
    openFileViewer(doc.url, doc.title || doc.original_name, doc.content_type, VDOC_KIND_LABELS[doc.kind] || '');
  });
  link.innerHTML = icon(doc.content_type === 'application/pdf' ? 'doc' : 'image', 20);
  const text = document.createElement('span');
  text.className = 'vdoc-text';
  const name = document.createElement('span');
  name.className = 'vdoc-name';
  // What the document IS, not how the file was called: «Factura de compra», «Permiso de circulación»… A title wins; «Otro» without
  // a title keeps the file name (it is all we know about it).
  name.textContent = doc.title || (doc.kind === 'other' ? doc.original_name : VDOC_KIND_LABELS[doc.kind]) || doc.original_name;
  const meta = document.createElement('span');
  meta.className = 'vdoc-meta';
  meta.textContent = `${vdocFileType(doc.content_type)} · ${vdocSize(doc.size)} · ${vdocDate(doc.uploaded_at)}`;
  text.append(name, meta);
  link.append(text);

  const del = document.createElement('button');
  del.type = 'button';
  del.className = 'vdoc-del needs-owner';
  del.setAttribute('aria-label', `Borrar ${doc.title || doc.original_name}`);
  del.innerHTML = icon('trash', 18);
  del.addEventListener('click', () => deleteVehicleDocument(doc));

  li.append(link, del);
  return li;
}

function renderVehicleDocuments(docs) {
  const groups = {};
  for (const id of new Set(Object.values(VDOC_LISTS))) groups[id] = [];
  for (const doc of docs) (groups[VDOC_LISTS[doc.kind] || 'vdocListOther']).push(doc);
  for (const [id, items] of Object.entries(groups)) {
    const ul = document.getElementById(id);
    if (!ul) continue;
    ul.replaceChildren(...items.map(vdocItem));
    if (!items.length) {
      const empty = document.createElement('li');
      empty.className = 'vdoc-empty';
      empty.textContent = ul.dataset.empty || 'Ninguno todavía.';
      ul.append(empty);
    }
  }
}

function vdocMegabytes(bytes) {
  return (bytes / (1024 * 1024)).toLocaleString(I18n.locale, { minimumFractionDigits: 1, maximumFractionDigits: 1 });
}

/** «Espacio usado: 12,3 MB de 50 MB»: the files of the manager's cars against their limit (a member sees its manager's space). */
async function loadStorageUsage() {
  const line = document.getElementById('vdocStorage');
  if (!line) return;
  try {
    const res = await fetch('/api/account/storage');
    if (!res.ok) { line.hidden = true; return; }
    const { used_bytes: used, limit_mb: limit } = await res.json();
    const usedText = `${vdocMegabytes(used)} MB`;
    line.textContent = limit < 0 ? t('Documentos, facturas y fotos: {p0} (sin límite de espacio)', {p0: usedText})
      : t('Documentos, facturas y fotos: {p0} de {p1} MB de espacio', {p0: usedText, p1: limit.toLocaleString(I18n.locale)});
    line.classList.toggle('is-full', limit >= 0 && used >= limit * 1024 * 1024 * 0.95);
    line.hidden = false;
  } catch (e) {
    line.hidden = true;
  }
}

/** Loads the documents of the selected car (called when the car sheet loads and when the car changes). */
async function loadVehicleDocuments() {
  loadStorageUsage();
  const vin = typeof currentVin !== 'undefined' ? currentVin : null;
  if (!vin || !document.getElementById('vdocListPurchase')) return;
  const token = ++vdocLoadToken;
  try {
    const res = await fetch(`/api/vehicles/${encodeURIComponent(vin)}/documents`);
    if (token !== vdocLoadToken) return;
    if (!res.ok) { renderVehicleDocuments([]); return; }
    renderVehicleDocuments(await res.json());
  } catch (e) {
    if (token === vdocLoadToken) showToast('No se han podido cargar los documentos del coche.', 'warning');
  }
}

/**
 * The bytes of a chosen file as a Blob, or null after telling the user why. Chrome on Android hands over files that only exist in
 * Google Drive (not downloaded) and then fails to read them during the upload, which looked like a network error.
 */
async function readUploadFile(file) {
  try {
    return new Blob([await file.arrayBuffer()], { type: file.type || 'application/octet-stream' });
  } catch (e) {
    showToast('No se ha podido leer el fichero. Si está en Google Drive u otra nube, descárgalo primero al móvil y vuelve a intentarlo.', 'error', 8000);
    return null;
  }
}

function pickVehicleDocument(inputId) {
  const input = document.getElementById(inputId);
  if (!input) return;
  input.value = '';
  input.click();
}

async function uploadVehicleDocument(input) {
  const file = input.files && input.files[0];
  const vin = typeof currentVin !== 'undefined' ? currentVin : null;
  if (!file || !vin) return;
  if (file.size > VDOC_MAX_BYTES) { showToast('El documento pesa más de 15 MB.', 'warning'); input.value = ''; return; }
  const kindSelect = input.dataset.kindSelect && document.getElementById(input.dataset.kindSelect);
  const titleInput = input.dataset.title && document.getElementById(input.dataset.title);
  const form = new FormData();
  const data = await readUploadFile(file);                 // read here: a file that only lives in a cloud app cannot be streamed
  if (!data) { input.value = ''; return; }
  form.append('file', data, file.name);
  form.append('kind', kindSelect ? kindSelect.value : input.dataset.kind);
  if (titleInput && titleInput.value.trim()) form.append('title', titleInput.value.trim());

  const button = input.parentElement.querySelector('.vdoc-add');
  if (button) { button.disabled = true; button.dataset.label = button.textContent; button.textContent = 'Subiendo…'; }
  try {
    const res = await fetch(`/api/vehicles/${encodeURIComponent(vin)}/documents`, { method: 'POST', body: form });
    if (!res.ok) {
      let detail = 'No se ha podido subir el documento.';
      try { detail = (await res.json()).detail || detail; } catch (e) { /* respuesta sin JSON */ }
      showToast(stripEmoji(String(detail)), 'error');
      return;
    }
    if (titleInput) titleInput.value = '';
    showToast('Documento guardado.', 'success');
    await loadVehicleDocuments();          // also refreshes the space used
  } catch (e) {
    // The reason helps to tell a dropped connection from a browser refusal (the text is escaped by showToast)
    showToast(t('Error de red al subir el documento ({p0}).', {p0: e && e.message ? e.message : e}), 'error', 7000);
  } finally {
    input.value = '';
    if (button) { button.disabled = false; button.textContent = button.dataset.label || button.textContent; }
  }
}

async function deleteVehicleDocument(doc) {
  const name = doc.title || doc.original_name;
  if (!await confirmDialog(t('¿Borrar «{p0}»? El fichero se elimina del servidor.', {p0: name}), { confirmText: 'Borrar', danger: true })) return;
  try {
    const res = await fetch(doc.url, { method: 'DELETE' });
    if (!res.ok) { showToast('No se ha podido borrar el documento.', 'error'); return; }
    showToast('Documento borrado.', 'success');
    await loadVehicleDocuments();
  } catch (e) {
    showToast('Error de red al borrar el documento.', 'error');
  }
}

/** «Otros documentos»: the name field only makes sense for «Otro»; a registration certificate or a technical sheet is already named. */
function syncOtherDocumentTitle() {
  const kind = document.getElementById('vdocOtherKind');
  const field = document.getElementById('vdocOtherTitleField');
  if (!kind || !field) return;
  field.hidden = kind.value !== 'other';
  if (field.hidden) document.getElementById('vdocOtherTitle').value = '';
}

document.addEventListener('DOMContentLoaded', () => {
  document.querySelectorAll('.vdoc-file').forEach((input) => input.addEventListener('change', () => uploadVehicleDocument(input)));
  const kind = document.getElementById('vdocOtherKind');
  if (kind) { kind.addEventListener('change', syncOtherDocumentTitle); syncOtherDocumentTitle(); }
});
