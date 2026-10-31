/* EVBro — Avisos y hojas de acción reutilizables (toasts, action sheets).
 * (Script clásico: comparte el ámbito global con los demás ficheros; ver el orden de carga en templates/index.html.)
 */

// Helper para notificaciones Toast flotantes (estilo Dynamic Island / iOS)
function showToast(message, type = 'info', duration = 3500) {
  let container = document.getElementById('appToastContainer');
  if (!container) {
    container = document.createElement('div');
    container.id = 'appToastContainer';
    container.className = 'app-toast-container';
    document.body.appendChild(container);
  }

  const icons = {
    'success': icon('check', 20),
    'error': icon('x', 20),
    'loading': '<span class="toast-spinner"></span>',
    'info': icon('info', 20),
    'warning': icon('alert', 20)
  };

  // The same text twice in a row is one notice: the existing toast is kept (its timer restarts below) instead of a second one
  const text = stripEmoji(message);
  const twin = [...container.querySelectorAll('.app-toast')].find((t) => t.dataset.text === text && t.dataset.live === '1');
  if (twin && type !== 'loading') {
    twin.className = `app-toast toast-${type}`;
    twin.querySelector('.toast-icon').innerHTML = icons[type] || icons.info;
    if (twin._dismiss) twin._dismiss.renew(duration);
    return twin._dismiss;
  }
  // At most three notices on screen: the oldest goes
  const live = [...container.querySelectorAll('.app-toast')].filter((t) => t.dataset.live === '1');
  while (live.length >= 3) live.shift().remove();

  const toast = document.createElement('div');
  toast.className = `app-toast toast-${type}`;
  toast.dataset.text = text;
  toast.dataset.live = '1';
  toast.innerHTML = `
    <span class="toast-icon">${icons[type] || icons.info}</span>
    <span class="u-flex-1_line-height-1p4">${escapeHtml(text)}</span>
  `;
  toast.addEventListener('click', () => { toast.dataset.live = '0'; toast.remove(); });   // a tap dismisses it

  container.appendChild(toast);

  if (navigator.vibrate) {
    try {
      navigator.vibrate(type === 'error' ? [40, 50, 40] : [25]);
    } catch(e) {}
  }

  let timer = null;
  const fade = () => {
    toast.dataset.live = '0';
    toast.style.opacity = '0';
    toast.style.transform = 'translateY(-12px) scale(0.95)';
    setTimeout(() => toast.remove(), 250);
  };
  const arm = (ms, kind) => {
    if (timer) clearTimeout(timer);
    timer = null;
    if (kind !== 'loading' && ms > 0) timer = setTimeout(fade, ms);
  };
  arm(duration, type);

  const handle = {
    update: (newMsg, newType = 'info', newDuration = 3500) => {
      const newText = stripEmoji(newMsg);
      toast.className = `app-toast toast-${newType}`;
      toast.dataset.text = newText;
      toast.innerHTML = `
        <span class="toast-icon">${icons[newType] || icons.info}</span>
        <span class="u-flex-1_line-height-1p4">${escapeHtml(newText)}</span>
      `;
      arm(newDuration, newType);
    },
    renew: (ms) => arm(ms, 'info'),
    remove: () => {
      if (timer) clearTimeout(timer);
      toast.dataset.live = '0';
      toast.remove();
    },
    get alive() { return toast.isConnected && toast.dataset.live === '1'; },
  };
  toast._dismiss = handle;
  return handle;
}

// ───────────────────────── App dialogs (instead of the browser's alert / confirm / prompt) ─────────────────────────
let appDialogResolver = null;

/** Opens the app's own dialog and resolves with the button pressed (true/false) or, with an input, the text or null. */
function openAppDialog({ title = '', message = '', confirmText = 'Aceptar', cancelText = 'Cancelar', danger = false, input = null }) {
  const modal = document.getElementById('appDialog');
  if (!modal) return Promise.resolve(input ? null : false);
  if (appDialogResolver) appDialogResolver(input ? null : false);          // a dialog on top of a dialog: the first one is cancelled
  document.getElementById('appDialogTitle').textContent = title;
  document.getElementById('appDialogTitle').hidden = !title;
  document.getElementById('appDialogMessage').textContent = message;
  const field = document.getElementById('appDialogInput');
  field.hidden = !input;
  if (input) {
    field.type = input.type || 'text';
    field.value = input.value || '';
    field.placeholder = input.placeholder || '';
    field.setAttribute('inputmode', input.inputmode || 'text');
    field.setAttribute('autocomplete', input.autocomplete || 'off');
    field.maxLength = input.maxLength || 200;
  }
  const ok = document.getElementById('appDialogOk');
  ok.textContent = confirmText;
  ok.className = danger ? 'btn-danger' : 'btn-primary';
  const cancel = document.getElementById('appDialogCancel');
  cancel.textContent = cancelText;
  cancel.hidden = cancelText === null;
  modal.classList.add('open');
  if (input) setTimeout(() => field.focus(), 50); else setTimeout(() => ok.focus(), 50);
  return new Promise((resolve) => {
    appDialogResolver = (value) => {
      appDialogResolver = null;
      modal.classList.remove('open');
      resolve(value);
    };
  });
}

function appDialogAnswer(accepted) {
  if (!appDialogResolver) return;
  const field = document.getElementById('appDialogInput');
  const withInput = field && !field.hidden;
  appDialogResolver(withInput ? (accepted ? field.value : null) : Boolean(accepted));
}

/** «¿Seguro?» → true/false. */
function confirmDialog(message, { title = '', confirmText = 'Aceptar', cancelText = 'Cancelar', danger = false } = {}) {
  return openAppDialog({ title, message, confirmText, cancelText, danger });
}

/** A short piece of text (or null when cancelled). `type` can be 'password'. */
function promptDialog(message, { title = '', confirmText = 'Aceptar', cancelText = 'Cancelar', type = 'text', inputmode = 'text', placeholder = '', value = '', maxLength = 200, autocomplete = 'off' } = {}) {
  return openAppDialog({ title, message, confirmText, cancelText, input: { type, inputmode, placeholder, value, maxLength, autocomplete } });
}

/** Something to read and close; prefer a toast for short transient notices. */
function alertDialog(message, { title = '', confirmText = 'Entendido' } = {}) {
  return openAppDialog({ title, message, confirmText, cancelText: null });
}

document.addEventListener('keydown', (e) => {
  if (!appDialogResolver) return;
  if (e.key === 'Escape') { e.preventDefault(); appDialogAnswer(false); }
  else if (e.key === 'Enter' && e.target && e.target.id === 'appDialogInput') { e.preventDefault(); appDialogAnswer(true); }
});

// Action Sheets nativos para comandos directos en móvil
function openActionSheet(title, subtitle, actions) {
  const modal = document.getElementById('nativeActionSheetModal');
  const titleEl = document.getElementById('actionSheetTitle');
  const subEl = document.getElementById('actionSheetSubtitle');
  const cont = document.getElementById('actionSheetButtonsContainer');
  if (!modal || !cont) return;

  if (titleEl) titleEl.innerText = title;
  if (subEl) subEl.innerText = subtitle || '';

  cont.innerHTML = actions.map(act => `
    <button class="action-sheet-btn ${act.type || ''}" data-on-click="${act.action}">
      <div class="u-flex-1">
        <div class="u-font-weight-700">${act.label}</div>
        ${act.desc ? `<div class="u-font-size-0p78rem_opacity-0p75_font-weight-normal_margin-top-1px">${act.desc}</div>` : ''}
      </div>
    </button>
  `).join('');

  modal.classList.add('open');
  if (navigator.vibrate) try { navigator.vibrate([20]); } catch(e) {}
}

function closeActionSheet() {
  const modal = document.getElementById('nativeActionSheetModal');
  if (modal) modal.classList.remove('open');
}

/** Escapa texto del servidor antes de meterlo en un toast (que usa innerHTML). */
/**
 * A Date from a value the server sent. The server stores and serialises times in UTC WITHOUT a zone ("2026-10-29T20:49:45"), and a
 * browser reads such a string as local time, which showed every history in UTC. A date-only value ("2026-10-29") stays a calendar day,
 * a value with a zone (Z or ±hh:mm) and a number (epoch ms) are used as they are.
 */
function serverDate(value) {
  if (value instanceof Date || typeof value === 'number') return new Date(value);
  const text = String(value ?? '');
  const naiveDateTime = /^\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}/.test(text) && !/(Z|[+-]\d{2}:?\d{2})$/.test(text);
  return new Date(naiveDateTime ? `${text.replace(' ', 'T')}Z` : text);
}

function escapeHtml(value) {
  return String(value ?? '').replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
}


/**
 * Dynamic inline styles without `style="…"` (the page's CSP has no 'unsafe-inline' for styles): a template writes
 * `data-css="width:37%; height:4px"` and, once in the DOM, this sets each declaration through the CSSOM, which the policy allows.
 * Values come from numbers we compute, never from user text. Call it on the container right after setting innerHTML.
 */
function applyDataStyles(root) {
  if (!root) return;
  const nodes = root.matches && root.matches('[data-css]') ? [root] : [];
  root.querySelectorAll('[data-css]').forEach((n) => nodes.push(n));
  nodes.forEach((el) => {
    el.dataset.css.split(';').forEach((decl) => {
      const i = decl.indexOf(':');
      if (i > 0) el.style.setProperty(decl.slice(0, i).trim(), decl.slice(i + 1).trim());
    });
  });
}
