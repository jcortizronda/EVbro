// Preferences saved under the previous key names move to the evbro_* keys once
(function () {
  try {
    Object.keys(localStorage).forEach(function (k) {
      if (k.indexOf('ebromate_') === 0) {
        var n = 'evbro_' + k.slice(9);
        if (localStorage.getItem(n) === null) localStorage.setItem(n, localStorage.getItem(k));
        localStorage.removeItem(k);
      }
    });
  } catch (e) { /* storage unavailable */ }
})();
/* EVBro — Tema antes del primer pintado: evita el destello claro/oscuro. Era un <script> en línea en index.html; la
 * Content-Security-Policy ya no permite código en línea, así que vive aquí y se carga de forma síncrona en el <head>. */
(function () {
  try {
    var savedTheme = localStorage.getItem('evbro_theme') || 'dark';
    var activeTheme = savedTheme;
    if (savedTheme === 'auto') {
      activeTheme = window.matchMedia('(prefers-color-scheme: light)').matches ? 'light' : 'dark';
    }
    document.documentElement.setAttribute('data-theme', activeTheme);
    var accent = localStorage.getItem('evbro_accent');
    if (accent && accent !== 'blue' && /^[a-z]+$/.test(accent)) document.documentElement.setAttribute('data-accent', accent);
    var metaTheme = document.querySelector('meta[name="theme-color"]');
    if (metaTheme) {
      metaTheme.setAttribute('content', activeTheme === 'light' ? '#f5f5f7' : '#000000');
    }
  } catch (e) { /* sin almacenamiento (navegación privada): tema por defecto */ }
})();
