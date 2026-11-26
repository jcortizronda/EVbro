/* EVbro — Live telemetry WebSocket.
 * (Classic script: shares the global scope with the other files; see the load order in templates/index.html.)
 */

// WebSocket connection for live telemetry
function initWebSocket() {
  if (window.EbroAuth && EbroAuth.locked) return;     // no session means no telemetry: do not insist
  const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
  const wsUrl = `${protocol}//${window.location.host}/api/ws`;

  ws = new WebSocket(wsUrl);

  ws.onopen = () => {
    console.log("WebSocket connected.");
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
      } else if (msg.type === 'cloud_status') {
        showCloudStatus(msg);
      } else if (msg.type === 'notification') {
        showAppNotificationBanner(msg.title, msg.message, msg.category);
      }
    } catch (e) {
      console.debug("WS parse:", e);
    }
  };

  ws.onclose = async () => {
    console.log("WebSocket closed.");
    // If the server closed it for lack of a session, do not reconnect: show the sign-in screen.
    if (window.EbroAuth && !(await EbroAuth.verify())) return;
    console.log("Reconnecting in 5s...");
    setTimeout(initWebSocket, 5000);
  };
}


/** The strip «La nube de Ebro no responde» (services/cloud_health.py): the server says when every read has been failing for a while
 *  and when one gets through again. Nothing is asked for it; a page that opens during an outage is told on connecting. */
function showCloudStatus(msg) {
  const strip = document.getElementById('cloudBanner');
  if (!strip) return;
  const wasDown = !strip.hidden;
  strip.hidden = !msg.down;
  const since = document.getElementById('cloudSince');
  if (since) {
    const at = msg.down && msg.since ? new Date(msg.since) : null;
    since.textContent = at && !isNaN(at) ? ' ' + t('desde las {p0}', { p0: at.toLocaleTimeString(I18n.locale, { hour: '2-digit', minute: '2-digit' }) }) : '';
  }
  if (wasDown && !msg.down && typeof showToast === 'function') showToast(t('La nube de Ebro vuelve a responder.'), 'success', 5000);
}
