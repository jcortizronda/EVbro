/* EVBro — Offline: a fixed notice when the app shows saved data instead of fresh data.
 *
 * The Service Worker (sw.js) keeps the last good response of the API reads that render the app and, without a network,
 * returns it with the header `X-EVBro-Offline: <date of that response>`. Here `fetch` is wrapped to see it and show
 * the «Sin conexión» strip; it hides as soon as a fresh response arrives again or the browser reports the network is back.
 * (Classic script: shares the global scope with the other files; loaded right after core/feedback.js.)
 */
(function () {
  const banner = () => document.getElementById('offlineBanner');

  function showOffline(since) {
    const b = banner();
    if (!b) return;
    const when = since ? new Date(since) : null;
    const span = document.getElementById('offlineSince');
    if (span) span.textContent = when && !isNaN(when) ? ` ${t('(de las {p0})', {p0: when.toLocaleTimeString(I18n.locale, { hour: '2-digit', minute: '2-digit' })})}` : '';
    b.hidden = false;
    document.body.classList.add('is-offline');
  }

  function hideOffline() {
    const b = banner();
    if (b && !b.hidden) {
      b.hidden = true;
      document.body.classList.remove('is-offline');
    }
  }

  const realFetch = window.fetch.bind(window);
  window.fetch = async function (input, init) {
    const response = await realFetch(input, init);
    try {
      const url = typeof input === 'string' ? input : (input && input.url) || '';
      if (url.startsWith('/api/') || url.includes('/api/')) {
        const since = response.headers.get('X-EVBro-Offline');
        if (since) showOffline(since);
        else if (response.ok && (!init || !init.method || init.method === 'GET')) hideOffline();
      }
    } catch (e) { /* never break the caller because of the banner */ }
    return response;
  };

  window.addEventListener('offline', () => showOffline(null));
  window.addEventListener('online', hideOffline);
  document.addEventListener('DOMContentLoaded', () => { if (navigator.onLine === false) showOffline(null); });
  window.EbroOffline = { showOffline, hideOffline };
})();
