/* EVBro — Arranque de la aplicación al cargar la página.
 * (Script clásico: comparte el ámbito global con los demás ficheros; ver el orden de carga en templates/index.html.)
 */

// Inicializar al cargar la página
window.addEventListener('DOMContentLoaded', async () => {
  initTheme();
  initMap();
  // Versiones anteriores guardaban el PIN de comandos en el navegador: se borra lo que hubiera.
  try { localStorage.removeItem('ebro_user_pin'); } catch (e) { /* almacenamiento no disponible */ }
  // Sin sesión se muestra la pantalla de acceso, que recarga la página al entrar.
  if (window.EbroAuth && !(await EbroAuth.ready())) return;
  checkAccountStatus();
  loadVehicles();
  initWebSocket();
});
