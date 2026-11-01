/* EVBro — App shortcuts: holding the installed app's icon offers «Cerrar el coche», «Encender el clima», «Carga» and «Viajes»
 * (manifest.json «shortcuts»). Each one opens `/?go=<what>`; this file reads it once the cars are loaded.
 * - A page (`charges`, `drives`) is just opened.
 * - An order is never sent on its own: the app opens on the right page and asks first, naming the car; whatever needs the PIN
 *   still asks for it (core/commands.js), and accounts that cannot send orders get a notice instead.
 * (Classic script: shares the global scope with the other files; see the load order in templates/index.html.)
 */
const AppShortcuts = (function () {
  'use strict';

  const PAGES = { charges: 'charges', drives: 'drives' };
  const ORDERS = {
    lock: { tab: 'overview', question: '¿Cerrar «{p0}»?', ok: 'Cerrar el coche', send: () => executeCommandDirect('bloquear', 'Bloquear puertas') },
    climate_on: { tab: 'climate', question: '¿Encender el clima de «{p0}»?', ok: 'Encender', send: () => promptCommand('clima_on', 'Encender Climatizador') },
  };

  // Read and drop the parameter at once: reloading the page or going back must not repeat the shortcut
  let pending = null;
  try {
    const url = new URL(window.location.href);
    pending = url.searchParams.get('go');
    if (pending) {
      url.searchParams.delete('go');
      history.replaceState(history.state, '', url.pathname + url.search + url.hash);
    }
  } catch (e) { pending = null; }

  /** Called by core/vehicles.js once the cars (and so the permissions on the current one) are known. */
  async function run() {
    const go = pending;
    pending = null;
    if (!go || typeof currentVin === 'undefined' || !currentVin) return;
    if (PAGES[go]) { switchTab(PAGES[go]); return; }
    const order = ORDERS[go];
    if (!order) return;
    switchTab(order.tab);
    if (!document.body.classList.contains('can-control')) {
      showToast(t('Este coche no admite órdenes desde tu cuenta.'), 'warning', 5000);
      return;
    }
    const car = (typeof currentVehicle === 'function' && currentVehicle()) || null;
    const name = car && typeof vehicleLabel === 'function' ? vehicleLabel(car) : currentVin;
    if (!await confirmDialog(t(order.question, { p0: name }), { confirmText: t(order.ok) })) return;
    order.send();
  }

  return { run };
})();
window.AppShortcuts = AppShortcuts;
