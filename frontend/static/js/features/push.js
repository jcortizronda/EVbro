/* Web Push notifications of this device (on Android, Chrome delivers them through Google FCM).
   The server stores the encrypted subscription and sends the alerts; here they are only turned on/off. */
(function () {
  const $ = (id) => document.getElementById(id);
  const api = (path, opts) => fetch('/api/notifications' + path, opts);
  const post = (path, body) => api(path, {
    method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body || {}),
  });
  const toast = (msg, type) => (typeof showToast === 'function' ? showToast(msg, type || 'info') : console.log(msg));

  const supported = () => 'serviceWorker' in navigator && 'PushManager' in window && 'Notification' in window;

  function keyToBytes(b64) {
    const pad = '='.repeat((4 - (b64.length % 4)) % 4);
    const raw = atob((b64 + pad).replace(/-/g, '+').replace(/_/g, '/'));
    return Uint8Array.from(raw, (c) => c.charCodeAt(0));
  }

  function deviceLabel() {
    const ua = navigator.userAgent;
    const os = /Android/i.test(ua) ? 'Android' : /iPhone|iPad/i.test(ua) ? 'iOS' : /Windows/i.test(ua) ? 'Windows'
      : /Mac/i.test(ua) ? 'macOS' : /Linux/i.test(ua) ? 'Linux' : 'Dispositivo';
    const br = /Edg\//.test(ua) ? 'Edge' : /Firefox/i.test(ua) ? 'Firefox' : /Chrome/i.test(ua) ? 'Chrome'
      : /Safari/i.test(ua) ? 'Safari' : 'Navegador';
    const pwa = window.matchMedia && matchMedia('(display-mode: standalone)').matches ? ' (app instalada)' : '';
    return `${br} en ${os}${pwa}`;
  }

  async function currentSub() {
    const reg = await navigator.serviceWorker.ready;
    return reg.pushManager.getSubscription();
  }

  /* State of this device: unsupported | insecure | denied | on | off */
  async function state() {
    if (!window.isSecureContext) return 'insecure';
    if (!supported()) return 'unsupported';
    if (Notification.permission === 'denied') return 'denied';
    try {
      const sub = await currentSub();
      if (!sub) return 'off';
      const r = await post('/push/status', { endpoint: sub.endpoint });
      return r.ok && (await r.json()).subscribed ? 'on' : 'off';
    } catch (e) { return 'off'; }
  }

  const TEXT = {
    insecure: ['Requiere HTTPS', 'Los navegadores solo permiten notificaciones en páginas seguras (https:// o localhost). Abre EVBro detrás de tu proxy con HTTPS.'],
    unsupported: ['No disponible en este navegador', 'En iPhone/iPad hay que añadir EVBro a la pantalla de inicio (Compartir → Añadir a pantalla de inicio) y abrirla desde ahí.'],
    denied: ['Bloqueadas en el navegador', 'Permite las notificaciones de este sitio en los ajustes del navegador y vuelve aquí.'],
    off: ['Desactivadas en este dispositivo', 'Actívalas para recibir los avisos como notificaciones del sistema, también con la app cerrada.'],
    on: ['Activas en este dispositivo', 'Recibirás los avisos como notificaciones del sistema, también con la app cerrada.'],
  };

  async function refresh() {
    const box = $('pushStatus');
    if (!box) return;
    const st = await state();
    box.dataset.state = st;
    $('pushStatusTitle').textContent = TEXT[st][0];
    $('pushStatusHint').textContent = TEXT[st][1];
    const toggle = $('btnPushToggle');
    toggle.disabled = !['on', 'off'].includes(st);
    toggle.textContent = st === 'on' ? 'Desactivar en este dispositivo' : 'Activar en este dispositivo';
    toggle.classList.toggle('btn-primary', st !== 'on');
    toggle.classList.toggle('btn-secondary', st === 'on');
    $('btnPushTest').disabled = st !== 'on';
    loadDevices();
  }

  async function enable() {
    const btn = $('btnPushToggle');
    btn.disabled = true;
    try {
      if ((await Notification.requestPermission()) !== 'granted') {
        toast('No has permitido las notificaciones.', 'error');
        return;
      }
      const { public_key } = await (await api('/push/public-key')).json();
      const reg = await navigator.serviceWorker.ready;
      let sub = await reg.pushManager.getSubscription();
      const wanted = keyToBytes(public_key);
      const same = sub && sub.options && sub.options.applicationServerKey
        && new Uint8Array(sub.options.applicationServerKey).join() === wanted.join();
      if (sub && !same) { await sub.unsubscribe(); sub = null; }   // different VAPID key: the subscription is useless
      if (!sub) sub = await reg.pushManager.subscribe({ userVisibleOnly: true, applicationServerKey: wanted });
      const json = sub.toJSON();
      const r = await post('/push/subscribe', { endpoint: json.endpoint, keys: json.keys, label: deviceLabel() });
      if (!r.ok) throw new Error((await r.json()).detail || r.status);
      toast('Notificaciones activadas en este dispositivo.', 'success');
    } catch (e) {
      toast('No se pudieron activar: ' + (e.message || e), 'error');
    } finally {
      refresh();
    }
  }

  async function disable() {
    try {
      const sub = await currentSub();
      if (sub) {
        await post('/push/unsubscribe', { endpoint: sub.endpoint });
        await sub.unsubscribe();
      }
      toast('Notificaciones desactivadas en este dispositivo.', 'info');
    } catch (e) {
      toast('No se pudieron desactivar: ' + (e.message || e), 'error');
    }
    refresh();
  }

  async function toggle() {
    return (await state()) === 'on' ? disable() : enable();
  }

  async function test() {
    const r = await post('/test', { channel: 'webpush' });
    if (r.ok) toast('Notificación de prueba enviada.', 'success');
    else toast((await r.json().catch(() => ({}))).detail || 'No se pudo enviar la prueba.', 'error');
    loadDevices();
  }

  async function removeDevice(id) {
    await api('/push/devices/' + id, { method: 'DELETE' });
    refresh();
  }

  async function loadDevices() {
    const list = $('pushDevices');
    if (!list) return;
    try {
      const rows = await (await api('/push/devices')).json();
      list.replaceChildren();
      for (const d of rows) {
        const li = document.createElement('li');
        li.className = 'push-device';
        const info = document.createElement('div');
        const name = document.createElement('strong');
        name.textContent = d.label;
        const meta = document.createElement('small');
        const when = d.last_success ? 'último aviso ' + serverDate(d.last_success + 'Z').toLocaleString() : 'sin avisos aún';
        meta.textContent = d.last_error ? 'Error: ' + d.last_error : when;
        if (d.last_error) meta.className = 'push-device-error';
        info.append(name, meta);
        const del = document.createElement('button');
        del.type = 'button';
        del.className = 'btn-sm btn-secondary';
        del.textContent = 'Quitar';
        del.addEventListener('click', () => removeDevice(d.id));
        li.append(info, del);
        list.append(li);
      }
      $('pushDevicesWrap').hidden = rows.length === 0;
    } catch (e) { /* no session or no network: the list stays as it was */ }
  }

  window.EbroPush = { refresh, toggle, test };
  document.addEventListener('DOMContentLoaded', () => {
    const btn = $('btnPushToggle');
    if (btn) { btn.addEventListener('click', toggle); $('btnPushTest').addEventListener('click', test); }
    const go = () => refresh();
    if (window.EbroAuth && EbroAuth.ready) EbroAuth.ready().then(go); else go();
  });
})();
