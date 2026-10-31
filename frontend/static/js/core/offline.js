/* EVBro — Sin conexión: aviso fijo cuando la aplicación enseña datos guardados en vez de datos frescos.
 *
 * El Service Worker (sw.js) guarda la última respuesta buena de las lecturas de la API que pintan la aplicación y, si no hay
 * red, la devuelve con la cabecera `X-EVBro-Offline: <fecha de esa respuesta>`. Aquí se envuelve `fetch` para verla y
 * enseñar la franja «Sin conexión»; se oculta en cuanto vuelve a llegar una respuesta fresca o el navegador avisa de que hay red.
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
