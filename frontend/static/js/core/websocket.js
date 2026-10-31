/* EVBro — WebSocket de telemetría en directo.
 * (Script clásico: comparte el ámbito global con los demás ficheros; ver el orden de carga en templates/index.html.)
 */

// Conexión WebSocket para telemetría en vivo
function initWebSocket() {
  if (window.EbroAuth && EbroAuth.locked) return;     // sin sesión no hay telemetría: no insistir
  const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
  const wsUrl = `${protocol}//${window.location.host}/api/ws`;

  ws = new WebSocket(wsUrl);

  ws.onopen = () => {
    console.log("WebSocket conectado.");
  };

  ws.onmessage = (event) => {
    try {
      const msg = JSON.parse(event.data);
      if (msg.type === 'telemetry' && msg.vin === currentVin) {
        applyTelemetry(msg.data);
      } else if (msg.type === 'command_result') {
        // The car confirms (or rejects) an order over MQTT: it updates the notice of the order just sent, or shows one if there is none
        if (typeof applyCommandResult === 'function') applyCommandResult(msg.message, msg.failed);
        else showToast(msg.message, msg.failed ? 'error' : 'success', 5000);
      } else if (msg.type === 'notification') {
        showAppNotificationBanner(msg.title, msg.message, msg.category);
      }
    } catch (e) {
      console.debug("WS parse:", e);
    }
  };

  ws.onclose = async () => {
    console.log("WebSocket cerrado.");
    // Si lo ha cerrado el servidor por falta de sesión, no se reconecta: se muestra el acceso.
    if (window.EbroAuth && !(await EbroAuth.verify())) return;
    console.log("Reconectando en 5s...");
    setTimeout(initWebSocket, 5000);
  };
}
