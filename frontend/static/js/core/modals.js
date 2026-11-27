/* EVbro — One behaviour for every window (`.modal-overlay`), whichever feature opens it.
 *
 * Each window keeps its own open and close functions; this file only watches when one gains or loses the `open` class and adds
 * what a window must do (accessibility and dialogs audits, 27-11-2026: Escape closed 0 of 34 windows, the phone's «back» left the
 * page instead of closing them, the focus stayed behind the window and Tab went out of it, and 30 had no dialog role):
 *   - it is announced as a dialog (`role`, `aria-modal`, `aria-labelledby` from its first heading);
 *   - the focus goes into it (the field marked `autofocus`, else the first field, else the first button) and back to what had it
 *     when it closes; Tab and Shift+Tab stay inside;
 *   - Escape and the phone's «back» close the top window with its own close button (the one whose `data-on-click` closes it), so
 *     whatever that function tidies up is still done.
 * The app dialog (`#appDialog`, core/feedback.js) already handles its keys and focus: it is only kept in the stack.
 * (Classic script: shares the global scope with the other files; see the load order in templates/index.html.)
 */
(function () {
  'use strict';

  const stack = [];                     // open windows, the top one last: { el, opener, pushed }
  let closingFromHistory = false;
  const FOCUSABLE = 'a[href], button:not([disabled]), input:not([disabled]):not([type="hidden"]), select:not([disabled]), textarea:not([disabled]), [tabindex]:not([tabindex="-1"])';

  const visible = (el) => !!(el.offsetWidth || el.offsetHeight || el.getClientRects().length) && !el.closest('[hidden]');
  const focusables = (modal) => [...modal.querySelectorAll(FOCUSABLE)].filter(visible);
  const isAppDialog = (modal) => modal.id === 'appDialog';

  function label(modal) {
    if (!modal.getAttribute('role')) modal.setAttribute('role', 'dialog');
    modal.setAttribute('aria-modal', 'true');
    if (!modal.getAttribute('aria-labelledby')) {
      const heading = modal.querySelector('h1, h2, h3, h4');
      if (heading) {
        if (!heading.id) heading.id = `${modal.id || 'modal'}Title`;
        modal.setAttribute('aria-labelledby', heading.id);
      }
    }
  }

  /** The button that closes this window with its own function (Cancelar, Cerrar, ×…). */
  function closeButton(modal) {
    const candidates = [...modal.querySelectorAll('[data-on-click]')].filter((b) => /close|cancel|hide/i.test(b.getAttribute('data-on-click')));
    return candidates.find(visible) || candidates[0] || null;
  }

  function requestClose(modal) {
    if (isAppDialog(modal)) {
      if (typeof appDialogAnswer === 'function') appDialogAnswer(false);
      return;
    }
    const button = closeButton(modal);
    if (button) button.click();
    else modal.classList.remove('open');
  }

  function opened(modal) {
    if (stack.some((s) => s.el === modal)) return;
    const entry = { el: modal, opener: document.activeElement, pushed: false };
    stack.push(entry);
    if (isAppDialog(modal)) return;
    label(modal);
    // The phone's «back» closes the window instead of leaving the page (as the expanded map does, core/map.js)
    try { history.pushState({ evbroModal: modal.id || true }, ''); entry.pushed = true; } catch (e) { /* not available */ }
    // The window is visible once the class has applied: then the focus goes in
    requestAnimationFrame(() => setTimeout(() => {
      if (!modal.classList.contains('open') || modal.contains(document.activeElement)) return;
      const target = modal.querySelector('[autofocus]:not([disabled])') || focusables(modal).find((el) => /^(INPUT|SELECT|TEXTAREA)$/.test(el.tagName))
        || focusables(modal)[0];
      if (target) target.focus({ preventScroll: true });
    }, 30));
  }

  function closed(modal) {
    const i = stack.findIndex((s) => s.el === modal);
    if (i < 0) return;
    const [entry] = stack.splice(i, 1);
    if (entry.pushed && !closingFromHistory && history.state && history.state.evbroModal) {
      // Closed with its own button: the entry «back» would have used goes too, quietly
      ignoreNextPop = true;
      history.back();
    }
    const opener = entry.opener;
    if (opener && document.contains(opener) && typeof opener.focus === 'function' && !stack.length) {
      setTimeout(() => opener.focus({ preventScroll: true }), 0);
    }
  }

  let ignoreNextPop = false;
  window.addEventListener('popstate', () => {
    if (ignoreNextPop) { ignoreNextPop = false; return; }
    const top = stack[stack.length - 1];
    if (!top || !top.pushed) return;
    closingFromHistory = true;
    try { requestClose(top.el); } finally { closingFromHistory = false; }
  });

  document.addEventListener('keydown', (e) => {
    const top = stack[stack.length - 1];
    if (!top || isAppDialog(top.el)) return;              // the app dialog answers its own keys (core/feedback.js)
    if (e.key === 'Escape') {
      e.preventDefault();
      requestClose(top.el);
    } else if (e.key === 'Tab') {
      const items = focusables(top.el);
      if (!items.length) return;
      const first = items[0], last = items[items.length - 1];
      if (!top.el.contains(document.activeElement)) { e.preventDefault(); first.focus(); }
      else if (e.shiftKey && document.activeElement === first) { e.preventDefault(); last.focus(); }
      else if (!e.shiftKey && document.activeElement === last) { e.preventDefault(); first.focus(); }
    }
  });

  function watch(modal) {
    if (modal.dataset.modalWatched) return;
    modal.dataset.modalWatched = '1';
    new MutationObserver(() => (modal.classList.contains('open') ? opened(modal) : closed(modal)))
      .observe(modal, { attributes: true, attributeFilter: ['class'] });
    if (modal.classList.contains('open')) opened(modal);
  }

  function watchAll(root) {
    (root.querySelectorAll ? root.querySelectorAll('.modal-overlay') : []).forEach(watch);
  }

  document.addEventListener('DOMContentLoaded', () => {
    watchAll(document);
    // Windows that JavaScript adds later
    new MutationObserver((records) => records.forEach((r) => r.addedNodes.forEach((n) => {
      if (n.nodeType !== 1) return;
      if (n.classList && n.classList.contains('modal-overlay')) watch(n);
      watchAll(n);
    }))).observe(document.body, { childList: true, subtree: true });
  });

  window.EbroModals = { requestClose, stack: () => stack.map((s) => s.el.id) };
})();
