/* EVBro — Theme before the first paint: avoids the light/dark flash. It used to be an inline <script> in index.html; the
 * Content-Security-Policy no longer allows inline code, so it lives here and loads synchronously in the <head>. */
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
  } catch (e) { /* no storage (private browsing): default theme */ }
})();
