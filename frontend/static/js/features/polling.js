/* EVBro — Sondeo inmediato de la nube (botón de actualizar). Los intervalos del sondeo son del servidor y se ajustan en la consola de
 * administración, no aquí.
 * (Script clásico: comparte el ámbito global con los demás ficheros; ver el orden de carga en templates/index.html.)
 */

// Forzar sondeo inmediato del coche actual
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
    console.error("Error forzando sondeo:", e);
    showToast('Error de conexión al actualizar.', 'error');
  }
}
