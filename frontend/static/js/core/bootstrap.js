/* EVBro — App start-up when the page loads.
 * (Classic script: shares the global scope with the other files; see the load order in templates/index.html.)
 */

// Initialise when the page loads
window.addEventListener('DOMContentLoaded', async () => {
  initTheme();
  initMap();
  // Older versions stored the command PIN in the browser: clear whatever is left.
  try { localStorage.removeItem('ebro_user_pin'); } catch (e) { /* almacenamiento no disponible */ }
  // Without a session the sign-in screen is shown, which reloads the page on sign-in.
  if (window.EbroAuth && !(await EbroAuth.ready())) return;
  checkAccountStatus();
  loadVehicles();
  initWebSocket();
});
