/* EVBro — fingerprint sign-in (passkeys / WebAuthn).
 *
 * The fingerprint (or face, or screen lock) is checked on the phone itself: the server only receives a signature made with a
 * key that never leaves the device. Here are the base64url ↔ bytes conversions, the registration of this device (Ajustes → Sistema)
 * and the call the sign-in screen uses (core/auth.js → EbroPasskeys.signIn).
 */
(function () {
  'use strict';

  const $ = (id) => document.getElementById(id);
  const toast = (msg, type) => (typeof showToast === 'function' ? showToast(msg, type || 'info') : console.log(msg));

  // ───────────────────────── base64url ─────────────────────────
  function b64urlToBuf(value) {
    const s = String(value).replace(/-/g, '+').replace(/_/g, '/');
    const raw = atob(s + '='.repeat((4 - (s.length % 4)) % 4));
    return Uint8Array.from(raw, (c) => c.charCodeAt(0)).buffer;
  }

  function bufToB64url(buf) {
    const bytes = new Uint8Array(buf);
    let s = '';
    for (let i = 0; i < bytes.length; i++) s += String.fromCharCode(bytes[i]);
    return btoa(s).replace(/\+/g, '-').replace(/\//g, '_').replace(/=+$/, '');
  }

  const supported = () => !!(window.PublicKeyCredential && navigator.credentials && navigator.credentials.create);

  function creationOptions(o) {
    return Object.assign({}, o, {
      challenge: b64urlToBuf(o.challenge),
      user: Object.assign({}, o.user, { id: b64urlToBuf(o.user.id) }),
      excludeCredentials: (o.excludeCredentials || []).map((c) => Object.assign({}, c, { id: b64urlToBuf(c.id) })),
    });
  }

  function requestOptions(o) {
    const out = Object.assign({}, o, { challenge: b64urlToBuf(o.challenge) });
    if (o.allowCredentials) out.allowCredentials = o.allowCredentials.map((c) => Object.assign({}, c, { id: b64urlToBuf(c.id) }));
    return out;
  }

  function credentialJSON(cred) {
    const r = cred.response;
    const response = { clientDataJSON: bufToB64url(r.clientDataJSON) };
    if (r.attestationObject) {
      response.attestationObject = bufToB64url(r.attestationObject);
      if (typeof r.getTransports === 'function') response.transports = r.getTransports();
    }
    if (r.authenticatorData) response.authenticatorData = bufToB64url(r.authenticatorData);
    if (r.signature) response.signature = bufToB64url(r.signature);
    if (r.userHandle) response.userHandle = bufToB64url(r.userHandle);
    return {
      id: cred.id, rawId: bufToB64url(cred.rawId), type: cred.type,
      authenticatorAttachment: cred.authenticatorAttachment || undefined,
      clientExtensionResults: typeof cred.getClientExtensionResults === 'function' ? cred.getClientExtensionResults() : {},
      response,
    };
  }

  /** Spanish message for the browser errors. */
  function browserError(e, registering) {
    const name = e && e.name;
    if (name === 'NotAllowedError') return 'Se ha cancelado o se ha agotado el tiempo. Vuelve a intentarlo.';
    if (name === 'InvalidStateError') return registering ? 'Este dispositivo ya está registrado para entrar con huella.' : 'Este dispositivo no tiene una huella registrada.';
    if (name === 'SecurityError') return 'La huella solo funciona abriendo EVBro por HTTPS y con su nombre (no con una dirección IP).';
    if (name === 'NotSupportedError') return 'Este dispositivo o navegador no admite el acceso con huella.';
    if (name === 'AbortError') return 'Se ha cancelado la operación.';
    return null;
  }

  async function postJSON(url, body) {
    const res = await fetch(url, {
      method: 'POST', credentials: 'same-origin', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body || {}),
    });
    const data = await res.json().catch(() => ({}));
    if (!res.ok) {
      const err = new Error(typeof data.detail === 'string' ? data.detail : 'Error del servidor (HTTP ' + res.status + ').');
      err.status = res.status;
      throw err;
    }
    return data;
  }

  function deviceLabel() {
    const ua = navigator.userAgent;
    const os = /Android/i.test(ua) ? 'Android' : /iPhone|iPad/i.test(ua) ? 'iOS' : /Windows/i.test(ua) ? 'Windows'
      : /Mac/i.test(ua) ? 'macOS' : /Linux/i.test(ua) ? 'Linux' : 'dispositivo';
    const br = /Edg\//.test(ua) ? 'Edge' : /Firefox/i.test(ua) ? 'Firefox' : /Chrome/i.test(ua) ? 'Chrome'
      : /Safari/i.test(ua) ? 'Safari' : 'Navegador';
    return `${br} en ${os}`;
  }

  // ───────────────────────── sign in ─────────────────────────
  /** Signs in with the fingerprint. Throws an Error with a message ready to show. */
  async function signIn(username) {
    if (!supported()) throw new Error('Este navegador no admite el acceso con huella.');
    const start = await postJSON('/api/auth/passkeys/login/options', username ? { username } : {});
    let cred;
    try {
      cred = await navigator.credentials.get({ publicKey: requestOptions(start.options) });
    } catch (e) {
      throw new Error(browserError(e, false) || 'No se pudo usar la huella: ' + (e.message || e));
    }
    if (!cred) throw new Error('Se ha cancelado la operación.');
    return postJSON('/api/auth/passkeys/login/verify', { challenge_id: start.challenge_id, credential: credentialJSON(cred) });
  }

  // ───────────────────────── Ajustes → Sistema ─────────────────────────
  async function addThisDevice() {
    const btn = $('btnPasskeyAdd');
    if (btn) btn.disabled = true;
    try {
      const start = await postJSON('/api/passkeys/register/options');
      let cred;
      try {
        cred = await navigator.credentials.create({ publicKey: creationOptions(start.options) });
      } catch (e) {
        throw new Error(browserError(e, true) || 'No se pudo registrar la huella: ' + (e.message || e));
      }
      await postJSON('/api/passkeys/register/verify', { challenge_id: start.challenge_id, credential: credentialJSON(cred), label: deviceLabel() });
      toast('Listo: ya puedes entrar con la huella desde este dispositivo.', 'success');
    } catch (e) {
      toast(e.message || String(e), 'error');
    } finally {
      if (btn) btn.disabled = !supported();
      refresh();
    }
  }

  async function remove(id, label) {
    if (!await confirmDialog(t('¿Quitar «{p0}»? Ya no podrá entrar con la huella.', {p0: label || 'este dispositivo'}), { confirmText: 'Borrar', danger: true })) return;
    const res = await fetch('/api/passkeys/' + encodeURIComponent(id), { method: 'DELETE' });
    if (res.ok) toast('Dispositivo quitado.', 'info');
    else toast('No se pudo quitar el dispositivo.', 'error');
    refresh();
  }

  const fmtDate = (iso) => (iso ? serverDate(iso + 'Z').toLocaleString(I18n.locale, { dateStyle: 'short', timeStyle: 'short' }) : null);

  async function refresh() {
    const list = $('passkeyList');
    if (!list) return;
    const add = $('btnPasskeyAdd');
    const hint = $('passkeyUnsupported');
    if (add) add.disabled = !supported();
    if (hint) hint.hidden = supported();
    let rows;
    try {
      const res = await fetch('/api/passkeys');
      if (!res.ok) return;
      rows = await res.json();
    } catch (e) { return; }                     // no network: the list stays as it was
    list.replaceChildren();
    for (const p of rows) {
      const li = document.createElement('li');
      li.className = 'passkey-item';
      const info = document.createElement('div');
      const name = document.createElement('strong');
      name.textContent = p.label || 'Dispositivo';
      const meta = document.createElement('small');
      const used = fmtDate(p.last_used_at);
      meta.textContent = 'Añadido ' + (fmtDate(p.created_at) || '—') + ' · ' + (used ? 'último uso ' + used : 'sin usar aún');
      info.append(name, meta);
      const del = document.createElement('button');
      del.type = 'button';
      del.className = 'btn-secondary';
      del.textContent = 'Quitar';
      del.setAttribute('aria-label', 'Quitar ' + (p.label || 'dispositivo'));
      del.addEventListener('click', () => remove(p.id, p.label));
      li.append(info, del);
      list.append(li);
    }
    const empty = $('passkeyEmpty');
    if (empty) empty.hidden = rows.length > 0;
  }

  window.EbroPasskeys = { supported, signIn, addThisDevice, refresh, b64urlToBuf, bufToB64url };

  document.addEventListener('DOMContentLoaded', () => {
    const add = $('btnPasskeyAdd');
    if (add) add.addEventListener('click', addThisDevice);
    const tab = $('btnSettingsSubAccount');          // the card lives in Ajustes → Cuenta
    if (tab) tab.addEventListener('click', refresh);
    if (window.EbroAuth && EbroAuth.ready) EbroAuth.ready().then((ok) => { if (ok) refresh(); });
  });
})();
