/* EVBro — Posición por GPS desde un servidor Traccar (Ajustes → Sistema → «Posición por GPS (Traccar)», para el coche elegido).
 * El coche tiene su propio localizador que reporta a Traccar; EVBro se conecta con el token de la API y recibe cada
 * posición en directo. Para un coche sin cuenta de Ebro, esas posiciones construyen los viajes.
 * (Classic script: shares the global scope with the other files; see the load order in templates/index.html.)
 */
(function () {
  const el = (id) => document.getElementById(id);
  let link = null;
  let devices = [];

  function form() {
    return { host: el('trHost').value.trim(), port: el('trPort').value ? parseInt(el('trPort').value, 10) : null,
             use_ssl: el('trSsl').checked, verify_ssl: !el('trNoVerify').checked, api_token: el('trToken').value.trim() || null };
  }

  let pickedVin = null;
  const carVin = () => pickedVin || (typeof currentVin !== 'undefined' ? currentVin : null);

  /** The card lists every car of the account (the header's is preselected): a tracker can be linked to a car that is not the one shown. */
  function renderCarPicker() {
    const sel = el('trCar');
    if (!sel) return;
    const cars = (typeof allVehiclesList !== 'undefined' && Array.isArray(allVehiclesList)) ? allVehiclesList : [];
    const vin = carVin();
    sel.innerHTML = cars.map((c) => `<option value="${escapeHtml(c.vin)}"${c.vin === vin ? ' selected' : ''}>${escapeHtml(c.name || c.vin)}</option>`).join('')
      || `<option value="${escapeHtml(vin || '')}">${escapeHtml(vin || '—')}</option>`;
  }

  function pickCar(vin) {
    pickedVin = vin || null;
    link = null;
    load();
  }

  async function load() {
    renderCarPicker();
    if (!carVin() || !el('trHost')) return;
    try {
      const res = await fetch(`/api/vehicles/${carVin()}/traccar`);
      if (res.status === 403) { el('vpTraccarSection').hidden = true; return; }     // not the owner
      el('vpTraccarSection').hidden = false;
      link = res.ok ? (await res.json()).link : null;
    } catch (e) { link = null; }
    render();
    if (link && link.has_token) test(true);        // a saved link: list every device so another can be picked
  }

  function render() {
    el('trHost').value = link ? link.host : '';
    el('trPort').value = link && link.port ? link.port : '';
    el('trSsl').checked = link ? !!link.use_ssl : false;
    el('trNoVerify').checked = link ? !link.verify_ssl : false;
    el('trToken').value = '';
    el('trToken').placeholder = link && link.has_token ? 'Guardado (déjalo vacío para no cambiarlo)' : 'Token de la API de Traccar';
    el('trBuildTrips').checked = link ? !!link.build_trips : false;
    el('trStopMinutes').value = link ? link.stop_minutes : 5;
    el('trEnabled').checked = link ? !!link.enabled : true;
    const sel = el('trDevice');
    sel.innerHTML = link ? `<option value="${link.device_id}">${escapeHtml(link.device_name || `Dispositivo ${link.device_id}`)}</option>` : '<option value="">Prueba la conexión para elegirlo</option>';
    el('trDelete').hidden = !link;
    const st = el('trStatus');
    if (!link) { st.textContent = 'Sin configurar. Este coche no recibe posiciones por GPS.'; st.dataset.tone = 'neutral'; return; }
    if (!link.enabled) { st.textContent = 'En pausa: no se reciben posiciones.'; st.dataset.tone = 'neutral'; return; }
    if (link.error || link.last_error) { st.textContent = t('Sin conexión con Traccar: {p0}', {p0: link.error || link.last_error}); st.dataset.tone = 'bad'; return; }
    const when = link.last_position_at ? serverDate(link.last_position_at).toLocaleString(I18n.locale, { day: '2-digit', month: 'short', hour: '2-digit', minute: '2-digit' }) : 'todavía ninguna';
    st.textContent = t('{p0} · última posición: {p1}.', {p0: link.connected ? 'Conectado a Traccar' : 'Conectando con Traccar…', p1: when});
    st.dataset.tone = link.connected ? 'ok' : 'neutral';
  }

  /** Asks Traccar for its devices and fills the picker. `silent` = no toasts (used when the card opens with a saved link). */
  async function test(silent = false) {
    const body = form();
    if (!body.host) { if (!silent) showToast('Indica el servidor de Traccar.', 'warning'); return false; }
    if (!body.api_token && !(link && link.has_token)) { if (!silent) showToast('Pega el token de la API de Traccar.', 'warning'); return false; }
    el('trTest').disabled = true;
    try {
      const res = await fetch(`/api/vehicles/${carVin()}/traccar/test`, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) });
      const data = await res.json().catch(() => ({}));
      if (!res.ok) throw new Error(Array.isArray(data.detail) ? data.detail.map((d) => d.msg).join(' · ') : (data.detail || res.status));
      devices = data.devices || [];
      const sel = el('trDevice');
      sel.innerHTML = devices.length ? devices.map((d) => `<option value="${d.id}">${escapeHtml(d.name || d.unique_id || `Dispositivo ${d.id}`)}${d.status ? ` (${escapeHtml(d.status)})` : ''}</option>`).join('') : '<option value="">Traccar no tiene dispositivos</option>';
      if (link && devices.some((d) => d.id === link.device_id)) sel.value = String(link.device_id);
      if (!silent) showToast(t('Traccar {p0} responde: {p1} dispositivo{p2}. Elige el del coche y guarda.', {p0: data.server && data.server.version ? data.server.version : '', p1: devices.length, p2: devices.length === 1 ? '' : 's'}), 'success', 6000);
      return devices.length > 0;
    } catch (e) {
      if (!silent) showToast(t('No se pudo conectar: {p0}', {p0: e.message || e}), 'error', 7000);
      return false;
    } finally {
      el('trTest').disabled = false;
    }
  }

  /** Host or token typed: refresh the device list on its own, so «Guardar» has something to pick from. */
  function onCredentialsChange() {
    const body = form();
    if (body.host && (body.api_token || (link && link.has_token))) test(true);
  }

  async function save() {
    const body = form();
    const deviceId = parseInt(el('trDevice').value, 10);
    if (!body.host) { showToast('Indica el servidor de Traccar.', 'warning'); return; }
    if (!deviceId) {
      // Saving without having tested: ask Traccar now; with a single device there is nothing to choose
      if (!await test(true)) { showToast('No se pudo conectar con Traccar: revisa el servidor y el token.', 'error', 7000); return; }
      if (devices.length === 1) { el('trDevice').value = String(devices[0].id); return save(); }
      showToast('Elige el dispositivo del coche en la lista y vuelve a guardar.', 'warning', 6000);
      return;
    }
    const dev = devices.find((d) => d.id === deviceId);
    Object.assign(body, { device_id: deviceId, device_name: dev ? dev.name : (link && link.device_id === deviceId ? link.device_name : null),
      enabled: el('trEnabled').checked, build_trips: el('trBuildTrips').checked, stop_minutes: parseInt(el('trStopMinutes').value, 10) || 5 });
    try {
      const res = await fetch(`/api/vehicles/${carVin()}/traccar`, { method: 'PUT', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) });
      const data = await res.json().catch(() => ({}));
      if (!res.ok) throw new Error(Array.isArray(data.detail) ? data.detail.map((d) => d.msg).join(' · ') : (data.detail || res.status));
      link = data.link;
      render();
      if (typeof applyVehicleCapabilities === 'function') applyVehicleCapabilities();
      showToast('Traccar guardado. Las posiciones llegarán en cuanto el coche se mueva.', 'success');
    } catch (e) {
      showToast(t('No se pudo guardar: {p0}', {p0: e.message || e}), 'error', 7000);
    }
  }

  async function remove() {
    if (!await confirmDialog('¿Quitar la conexión con Traccar? Se dejan de recibir posiciones; los viajes ya guardados se conservan.')) return;
    const res = await fetch(`/api/vehicles/${carVin()}/traccar`, { method: 'DELETE' });
    if (res.ok) { link = null; devices = []; render(); if (typeof applyVehicleCapabilities === 'function') applyVehicleCapabilities(); showToast('Conexión con Traccar quitada', 'success'); }
  }

  window.Traccar = { load, test, save, remove, onCredentialsChange, pickCar };
})();
