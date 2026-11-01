/* EVBro — PWA: Service Worker registration and installation as an app.
 * (Classic script: shares the global scope with the other files; see the load order in templates/index.html.)
 */

let deferredInstallPrompt = null;

if ('serviceWorker' in navigator) {
  window.addEventListener('load', () => {
    navigator.serviceWorker.register('/sw.js').then((reg) => {
      console.log('[PWA] Service Worker registered:', reg.scope);
    }).catch((err) => {
      console.warn('[PWA] Service Worker registration failed:', err);
    });
  });
}

window.addEventListener('beforeinstallprompt', (e) => {
  e.preventDefault();
  deferredInstallPrompt = e;
  const btn = document.getElementById('btnInstallApp');
  if (btn) btn.style.display = 'inline-flex';
});

function installPWA() {
  if (!deferredInstallPrompt) {
    alertDialog("En iPhone o iPad: pulsa el botón Compartir de Safari y elige «Añadir a la pantalla de inicio».", { title: 'Instalar EVBro' });
    return;
  }
  deferredInstallPrompt.prompt();
  deferredInstallPrompt.userChoice.then((choiceResult) => {
    if (choiceResult.outcome === 'accepted') {
      console.log('The user installed the EVBro PWA');
    }
    deferredInstallPrompt = null;
    const btn = document.getElementById('btnInstallApp');
    if (btn) btn.style.display = 'none';
  });
}
