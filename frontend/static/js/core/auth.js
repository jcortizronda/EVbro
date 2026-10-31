/* EVBro — acceso (login propio, sesión por cookie).
 *
 * Se carga ANTES que app.js. Hace tres cosas:
 *   1. Intercepta fetch: si la API contesta 401 (sin sesión o caducada) muestra la pantalla de
 *      acceso. Los errores de la cuenta Ebro NO usan 401 (ver routes_commands.py), así que no se
 *      confunden con esto.
 *   2. Ofrece `EbroAuth.ready()` para que app.js no cargue datos hasta saber si hay sesión.
 *   3. Gestiona primer arranque (crear usuario), inicio/cierre de sesión (también con huella) y cambio de contraseña.
 *
 * La sesión vive en una cookie HttpOnly: este script nunca ve el token.
 */
(function () {
  'use strict';

  const nativeFetch = window.fetch.bind(window);
  const state = { locked: false, authenticated: false, username: null, noAccounts: false, readyPromise: null };
  const $ = (id) => document.getElementById(id);

  function apiPath(input) {
    try {
      const url = new URL(typeof input === 'string' ? input : input.url, window.location.origin);
      return url.origin === window.location.origin ? url.pathname : null;
    } catch (e) {
      return null;
    }
  }

  // 401 de la API (salvo las propias rutas de acceso) = no hay sesión válida.
  window.fetch = async function (input, init) {
    const res = await nativeFetch(input, init);
    const path = apiPath(input);
    if (res.status === 401 && path && path.startsWith('/api/') && !path.startsWith('/api/auth/')) {
      state.locked = true;
      showOverlay();
    }
    return res;
  };

  async function fetchStatus() {
    const res = await nativeFetch('/api/auth/status', { credentials: 'same-origin' });
    if (!res.ok) throw new Error('HTTP ' + res.status);
    const data = await res.json();
    state.authenticated = !!data.authenticated;
    state.username = data.username || null;
    state.noAccounts = !!data.no_accounts;
    window.accountInfo = { role: data.role || null, vehicleLimit: data.vehicle_limit, memberLimit: data.member_limit };
    if (typeof applyAccessLevel === 'function') applyAccessLevel();
    return data;
  }

  function errorText(data, fallback) {
    if (data && typeof data.detail === 'string') return data.detail;
    if (data && Array.isArray(data.detail)) return 'Revisa los datos introducidos.';
    return fallback;
  }

  function setError(id, message) {
    const el = $(id);
    if (!el) return;
    el.textContent = message || '';
    el.hidden = !message;
  }

  // ───────────────────────── pantalla de acceso ─────────────────────────
  async function showOverlay() {
    const overlay = $('authOverlay');
    if (!overlay) return;
    try {
      await fetchStatus();
      if (state.authenticated) { state.locked = false; return; }   // la sesión sigue viva: falso aviso
    } catch (e) { /* sin red: se enseña el login igualmente */ }
    configureForm();
    overlay.hidden = false;
    document.body.classList.add('auth-open');
    const first = $('authUsername');
    if (first) setTimeout(() => first.focus(), 50);
  }

  function configureForm() {
    const reg = !!state.registerMode;                          // creating a manager account with an invitation code
    $('authTitle').textContent = reg ? 'Crear cuenta' : 'Iniciar sesión';
    $('authSubtitle').textContent = reg
      ? 'Necesitas un código de invitación del administrador de este servidor. Elige tu usuario y una contraseña de al menos 10 caracteres.'
      : (state.noAccounts
        ? 'Este servidor aún no tiene cuentas. El administrador debe crearlas desde la consola de administración (o darte un código de invitación).'
        : 'Introduce tu usuario y contraseña de EVBro.');
    $('authInviteRow').hidden = !reg;
    $('authPassword2Row').hidden = !reg;
    $('authPassword').autocomplete = reg ? 'new-password' : 'current-password';
    $('authSubmit').textContent = reg ? 'Crear cuenta y entrar' : 'Entrar';
    $('authSwitchRow').hidden = false;
    const consent = $('authConsentRow');
    if (consent) consent.hidden = !reg;
    $('authSwitch').textContent = reg ? 'Ya tengo cuenta' : 'Tengo una invitación';
    const pk = $('authPasskeyRow');
    if (pk) pk.hidden = reg || !window.PublicKeyCredential;      // sign-in with the fingerprint (features/passkeys.js)
    setError('authError', '');
  }

  async function submitPasskey() {
    const btn = $('authPasskey');
    if (!window.EbroPasskeys) { setError('authError', 'El acceso con huella no está disponible.'); return; }
    btn.disabled = true;
    setError('authError', '');
    try {
      await window.EbroPasskeys.signIn($('authUsername').value.trim());
      window.location.reload();          // same as a password login: the app starts with the session
    } catch (e) {
      setError('authError', (e && e.message) || 'No se pudo entrar con la huella.');
    } finally {
      btn.disabled = false;
    }
  }

  async function submitAuthForm(event) {
    event.preventDefault();
    const reg = !!state.registerMode;
    const username = $('authUsername').value.trim();
    const password = $('authPassword').value;
    if (!username || !password) { setError('authError', 'Escribe el usuario y la contraseña.'); return; }
    if (reg && password !== $('authPassword2').value) { setError('authError', 'Las contraseñas no coinciden.'); return; }
    if (reg && !$('authInviteCode').value.trim()) { setError('authError', 'Escribe el código de invitación.'); return; }

    const body = reg ? { username, password, invite_token: $('authInviteCode').value.trim(), accept_privacy: !!($('authConsent') && $('authConsent').checked) } : { username, password };
    if (reg && !body.accept_privacy) { setError('authError', 'Marca la casilla de la información sobre tus datos para crear la cuenta.'); return; }
    const btn = $('authSubmit');
    btn.disabled = true;
    setError('authError', '');
    try {
      const res = await nativeFetch(reg ? '/api/auth/register' : '/api/auth/login', {
        method: 'POST',
        credentials: 'same-origin',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(body),
      });
      const data = await res.json().catch(() => ({}));
      if (res.ok) {
        window.location.reload();          // arranca la app ya con sesión
        return;
      }
      setError('authError', errorText(data, 'No se pudo iniciar sesión (HTTP ' + res.status + ').'));
      $('authPassword').value = '';
      if ($('authPassword2')) $('authPassword2').value = '';
    } catch (e) {
      setError('authError', 'No hay conexión con el servidor.');
    } finally {
      btn.disabled = false;
    }
  }

  // ───────────────────────── cierre de sesión y contraseña ─────────────────────────
  async function logout() {
    try { await nativeFetch('/api/auth/logout', { method: 'POST', credentials: 'same-origin' }); } catch (e) { /* da igual */ }
    window.location.reload();
  }

  function openPasswordModal() {
    ['pwdCurrent', 'pwdNew', 'pwdNew2'].forEach((id) => { if ($(id)) $(id).value = ''; });
    setError('pwdError', '');
    $('passwordModal').classList.add('open');
    setTimeout(() => $('pwdCurrent').focus(), 50);
  }

  function closePasswordModal() {
    $('passwordModal').classList.remove('open');
  }

  async function submitPasswordChange(event) {
    event.preventDefault();
    const current = $('pwdCurrent').value;
    const next = $('pwdNew').value;
    if (next !== $('pwdNew2').value) { setError('pwdError', 'Las contraseñas nuevas no coinciden.'); return; }
    const btn = $('pwdSubmit');
    btn.disabled = true;
    setError('pwdError', '');
    try {
      const res = await fetch('/api/auth/password', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ current_password: current, new_password: next }),
      });
      const data = await res.json().catch(() => ({}));
      if (res.ok) {
        closePasswordModal();
        if (typeof showToast === 'function') showToast('Contraseña cambiada. Las demás sesiones se han cerrado.', 'success', 4500);
      } else {
        setError('pwdError', errorText(data, 'No se pudo cambiar la contraseña.'));
      }
    } catch (e) {
      setError('pwdError', 'No hay conexión con el servidor.');
    } finally {
      btn.disabled = false;
    }
  }

  // ───────────────────────── API pública ─────────────────────────
  /** Resuelve a true si se puede cargar la app; a false si se ha mostrado la pantalla de acceso. */
  function ready() {
    if (!state.readyPromise) {
      state.readyPromise = (async () => {
        try {
          await fetchStatus();
        } catch (e) {
          return true;                      // sin red: la app funciona como antes (caché del Service Worker)
        }
        const label = $('authCurrentUser');
        if (label) label.textContent = state.username || '—';
        if (state.authenticated) return true;
        state.locked = true;
        showOverlay();
        return false;
      })();
    }
    return state.readyPromise;
  }

  /** ¿Sigue habiendo sesión? Lo usa el WebSocket antes de reconectar. Sin red → true (se reintenta). */
  async function verify() {
    try {
      await fetchStatus();
    } catch (e) {
      return true;
    }
    if (!state.authenticated) { state.locked = true; showOverlay(); }
    return state.authenticated;
  }

  document.addEventListener('DOMContentLoaded', () => {
    const form = $('authForm');
    if (form) form.addEventListener('submit', submitAuthForm);
    const sw = $('authSwitch');
    if (sw) sw.addEventListener('click', () => { state.registerMode = !state.registerMode; configureForm(); });
    const pk = $('authPasskey');
    if (pk) pk.addEventListener('click', submitPasskey);
    const pwdForm = $('passwordForm');
    if (pwdForm) pwdForm.addEventListener('submit', submitPasswordChange);
  });

  window.EbroAuth = {
    get locked() { return state.locked; },
    get username() { return state.username; },
    ready, verify, logout, openPasswordModal, closePasswordModal,
  };
})();
