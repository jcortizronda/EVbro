/* EVBro — Maintenance: attached invoices and receipts.
 * (Classic script: shares the global scope with the other files; see the load order in templates/index.html.)
 */

let targetLogIdForInvoiceUpload = null;

async function uploadMaintenanceInvoiceFile(logId, file) {
  const formData = new FormData();
  const data = await readUploadFile(file);                 // features/vehicle-documents.js: a clear message for files that cannot be read (cloud)
  if (!data) return null;
  formData.append('file', data, file.name);

  const res = await fetch(`/api/maintenance/logs/${logId}/invoice`, {
    method: 'POST',
    body: formData
  });

  if (!res.ok) {
    const err = await res.json();
    throw new Error(err.detail || 'Error al subir la factura.');
  }
  return await res.json();
}

function triggerAttachInvoice(logId) {
  targetLogIdForInvoiceUpload = logId;
  const fileInput = document.getElementById('directInvoiceUploadInput');
  if (fileInput) {
    fileInput.value = '';
    fileInput.click();
  }
}

async function handleDirectInvoiceSelected(event) {
  const file = event.target.files && event.target.files[0];
  if (!file || !targetLogIdForInvoiceUpload) return;

  const logId = targetLogIdForInvoiceUpload;
  try {
    if (await uploadMaintenanceInvoiceFile(logId, file)) {
      showToast('Factura guardada.', 'success');
      loadMaintenanceLogs();
    }
  } catch (err) {
    showToast(t('No se ha podido subir la factura: {p0}', {p0: err.message}), 'error', 7000);
  } finally {
    targetLogIdForInvoiceUpload = null;
    event.target.value = '';
  }
}

async function deleteMaintenanceInvoice(logId) {
  if (!await confirmDialog("¿Seguro que deseas eliminar la factura adjunta a este registro?", { confirmText: 'Borrar', danger: true })) return;

  try {
    const res = await fetch(`/api/maintenance/logs/${logId}/invoice`, {
      method: 'DELETE'
    });
    if (res.ok) {
      showToast('Factura borrada.', 'success');
      loadMaintenanceLogs();
    } else {
      showToast('No se ha podido borrar la factura.', 'error');
    }
  } catch (e) {
    showToast('Error de red al borrar la factura.', 'error');
  }
}

function openInvoiceViewer(logId, filename, contentType, itemName) {
  openFileViewer(`/api/maintenance/logs/${logId}/invoice?t=${Date.now()}`, filename || 'Factura de mantenimiento', contentType,
    itemName ? t('Intervención: {p0}', {p0: itemName}) : '');
}

/**
 * Shows a PDF or an image of the app (invoice, car document) inside the app, with a download button. It registers a history entry so
 * that «atrás» on the phone closes the viewer instead of leaving (and restarting) the application.
 */
function openFileViewer(url, filename, contentType, subtitle) {
  const titleElem = document.getElementById('invoiceViewerTitle');
  const subElem = document.getElementById('invoiceViewerSubtitle');
  const dlBtn = document.getElementById('invoiceViewerDownloadBtn');
  const contentBox = document.getElementById('invoiceViewerContent');

  if (titleElem) titleElem.innerText = filename || 'Documento';
  if (subElem) subElem.innerText = subtitle || '';
  if (dlBtn) {
    dlBtn.href = url;
    dlBtn.download = filename || 'documento';
  }

  const isPdf = (contentType || '').includes('pdf') || (filename || '').toLowerCase().endsWith('.pdf');

  if (contentBox) {
    contentBox.innerHTML = '';
    if (isPdf) {
      const iframe = document.createElement('iframe');
      iframe.src = url;
      iframe.style.width = '100%';
      iframe.style.height = '72vh';
      iframe.style.border = 'none';
      iframe.style.borderRadius = '6px';
      contentBox.appendChild(iframe);
    } else {
      const img = document.createElement('img');
      img.src = url;
      img.alt = filename;
      img.style.maxWidth = '100%';
      img.style.maxHeight = '72vh';
      img.style.objectFit = 'contain';
      img.style.borderRadius = '6px';
      img.style.boxShadow = '0 6px 20px rgba(0,0,0,0.6)';
      contentBox.appendChild(img);
    }
  }

  document.getElementById('invoiceViewerModal').classList.add('open');
  history.pushState({ ebroViewer: true }, '');
}

function closeInvoiceViewerModal(fromHistory = false) {
  const modal = document.getElementById('invoiceViewerModal');
  if (!modal.classList.contains('open')) return;
  const contentBox = document.getElementById('invoiceViewerContent');
  if (contentBox) contentBox.innerHTML = '';
  modal.classList.remove('open');
  if (!fromHistory && history.state && history.state.ebroViewer) history.back();   // drop the entry the viewer added
}

window.addEventListener('popstate', () => closeInvoiceViewerModal(true));
