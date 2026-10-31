/* EVBro — PWA: registro del Service Worker e instalación como app.
 * (Script clásico: comparte el ámbito global con los demás ficheros; ver el orden de carga en templates/index.html.)
 */

let deferredInstallPrompt = null;

if ('serviceWorker' in navigator) {
  window.addEventListener('load', () => {
    navigator.serviceWorker.register('/sw.js').then((reg) => {
      console.log('[PWA] Service Worker registrado correctamente:', reg.scope);
    }).catch((err) => {
      console.warn('[PWA] Fallo al registrar Service Worker:', err);
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
      console.log('El usuario instaló la aplicación EVBro PWA');
    }
    deferredInstallPrompt = null;
    const btn = document.getElementById('btnInstallApp');
    if (btn) btn.style.display = 'none';
  });
}
