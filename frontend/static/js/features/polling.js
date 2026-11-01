/* EVBro — Immediate cloud poll (refresh button). The poll intervals belong to the server and are set in the admin
 * console, not here.
 * (Classic script: shares the global scope with the other files; see the load order in templates/index.html.)
 */

// Force an immediate poll of the current car
async function pollCurrentVehicle() {
  if (!currentVin) {
    showToast('Elige un coche primero.', 'warning');
    return;
  }
  try {
    const res = await fetch(`/api/vehicles/${currentVin}/poll`, { method: 'POST' });
    const data = await res.json();
    if (res.ok) {
      showToast('Datos del coche actualizados.', 'success');
      if (data.telemetry) applyTelemetry(data.telemetry);
    } else {
      showToast(data.detail || 'No se pudo actualizar.', 'error', 6000);
    }
  } catch (e) {
    console.error("Error forcing a poll:", e);
    showToast('Error de conexión al actualizar.', 'error');
  }
}
