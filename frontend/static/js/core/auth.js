/* EVBro — sign-in (own login, cookie session).
 *
 * Loaded BEFORE app.js. It does three things:
 *   1. Intercepts fetch: if the API answers 401 (no session or expired) it shows the sign-in
 *      screen. Ebro account errors do NOT use 401 (see routes_commands.py), so they are not
 *      confused with this.
 *   2. Offers `EbroAuth.ready()` so app.js does not load data until it knows whether there is a session.
 *   3. Handles first start (create user), sign-in/sign-out (also with fingerprint), password change, the account's e-mail and
 *      «¿Olvidaste la contraseña?» (a link by e-mail that opens the app with `#reset=<token>`; the fragment never reaches a server).
 *
 * The session lives in an HttpOnly cookie: this script never sees the token.
 */
(function () {
  'use strict';

  const nativeFetch = window.fetch.bind(window);
  const state = { locked: false, authenticated: false, username: null, noAccounts: false, readyPromise: null,
                  mode: 'login', passwordReset: false, email: null, resetToken: null, notice: null };
  const $ = (id) => document.getElementById(id);

  function apiPath(input) {
    try {
      const url = new URL(typeof input === 'string' ? input : input.url, window.location.origin);
      return url.origin === window.location.origin ? url.pathname : null;
    } catch (e) {
      return null;
    }
  }

  // 401 from the API (except the sign-in routes themselves) = no valid session.
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
    state.passwordReset = !!data.password_reset;
    state.email = data.email || null;
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

  // ───────────────────────── sign-in screen ─────────────────────────
  async function showOverlay() {
    const overlay = $('authOverlay');
    if (!overlay) return;
    try {
      await fetchStatus();
      if (state.authenticated && state.mode !== 'reset') { state.locked = false; return; }   // the session is still alive: false alarm
    } catch (e) { /* no network: show the login anyway */ }
    configureForm();
    overlay.hidden = false;
    document.body.classList.add('auth-open');
    const first = $({ forgot: 'authForgotLogin', reset: 'authResetNew' }[state.mode] || 'authUsername');
    if (first) setTimeout(() => first.focus(), 50);
  }

  function configureForm() {
    const mode = state.mode;
    const alt = mode === 'forgot' || mode === 'reset';
    $('authForm').hidden = alt;
    $('authForgotForm').hidden = mode !== 'forgot';
    $('authResetForm').hidden = mode !== 'reset';
    $('authForgotRow').hidden = alt || !!state.registerMode || !state.passwordReset;
    if (alt) {
      $('authTitle').textContent = mode === 'forgot' ? 'Recuperar la contraseña' : 'Contraseña nueva';
      $('authSubtitle').textContent = mode === 'forgot'
        ? 'Escribe tu usuario o el email de tu cuenta y te mandaremos un enlace para elegir otra contraseña.'
        : 'Elige la contraseña nueva de tu cuenta de EVBro (al menos 10 caracteres). Se cerrarán todas sus sesiones abiertas.';
      ['authSwitchRow', 'authConsentRow', 'authPasskeyRow'].forEach((id) => { if ($(id)) $(id).hidden = true; });
      setError('authForgotError', '');
      setError('authResetError', '');
      return;
    }
    const reg = !!state.registerMode;                          // creating a manager account with an invitation code
    $('authTitle').textContent = reg ? 'Crear cuenta' : 'Iniciar sesión';
    $('authSubtitle').textContent = reg
      ? 'Necesitas un código de invitación del administrador de este servidor. Elige tu usuario y una contraseña de al menos 10 caracteres.'
      : state.notice || (state.noAccounts
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
        window.location.reload();          // start the app with the session in place
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

  function setMode(mode) {
    state.mode = mode;
    state.notice = null;
    if (mode !== 'reset') state.resetToken = null;
    configureForm();
    showOverlay();
  }

  // ───────────────────────── «¿Olvidaste la contraseña?» ─────────────────────────
  async function submitForgot(event) {
    event.preventDefault();
    const login = $('authForgotLogin').value.trim();
    if (!login) { setError('authForgotError', 'Escribe tu usuario o tu email.'); return; }
    const btn = $('authForgotSubmit');
    btn.disabled = true;
    setError('authForgotError', '');
    $('authForgotSent').hidden = true;
    try {
      const res = await nativeFetch('/api/auth/password-reset', {
        method: 'POST', credentials: 'same-origin', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ login }),
      });
      const data = await res.json().catch(() => ({}));
      if (res.ok) $('authForgotSent').hidden = false;
      else setError('authForgotError', errorText(data, 'No se pudo pedir el enlace (HTTP ' + res.status + ').'));
    } catch (e) {
      setError('authForgotError', 'No hay conexión con el servidor.');
    } finally {
      btn.disabled = false;
    }
  }

  async function submitReset(event) {
    event.preventDefault();
    const next = $('authResetNew').value;
    if (!next) { setError('authResetError', 'Escribe la contraseña nueva.'); return; }
    if (next !== $('authResetNew2').value) { setError('authResetError', 'Las contraseñas nuevas no coinciden.'); return; }
    const btn = $('authResetSubmit');
    btn.disabled = true;
    setError('authResetError', '');
    try {
      const res = await nativeFetch('/api/auth/password-reset/confirm', {
        method: 'POST', credentials: 'same-origin', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ token: state.resetToken, new_password: next }),
      });
      const data = await res.json().catch(() => ({}));
      if (!res.ok) { setError('authResetError', errorText(data, 'No se pudo cambiar la contraseña (HTTP ' + res.status + ').')); return; }
      ['authResetNew', 'authResetNew2', 'authPassword'].forEach((id) => { $(id).value = ''; });
      $('authUsername').value = data.username || '';
      state.authenticated = false;               // every session of the account was closed, this one too if it was its own
      setMode('login');
      state.notice = 'Contraseña cambiada. Ya puedes entrar con la nueva.';      // kept: showOverlay paints the form again
      configureForm();
      setTimeout(() => $('authPassword').focus(), 60);
    } catch (e) {
      setError('authResetError', 'No hay conexión con el servidor.');
    } finally {
      btn.disabled = false;
    }
  }

  /** The link of the e-mail (`#reset=<token>`): keep the token and take it out of the address bar (and of the history) at once. */
  function takeResetToken() {
    const hash = window.location.hash || '';
    if (!hash.startsWith('#reset=')) return false;
    state.resetToken = decodeURIComponent(hash.slice(7));
    state.mode = 'reset';
    history.replaceState(null, '', window.location.pathname + window.location.search);
    return true;
  }
  // The link opened in a tab where EVBro already runs: only the fragment changes and the page does not load again
  window.addEventListener('hashchange', () => {
    if (takeResetToken()) { state.locked = true; configureForm(); showOverlay(); }
  });

  // ───────────────────────── the account's e-mail ─────────────────────────
  function paintEmail() {
    const label = $('authCurrentEmail');
    if (label) label.textContent = state.email || 'sin poner';
    const noMail = $('authEmailNoMail');
    if (noMail) noMail.hidden = state.passwordReset;
  }

  function openEmailModal() {
    $('emailNew').value = state.email || '';
    $('emailPassword').value = '';
    setError('emailError', '');
    $('emailModal').classList.add('open');
    setTimeout(() => $('emailNew').focus(), 50);
  }

  function closeEmailModal() {
    $('emailModal').classList.remove('open');
    $('emailPassword').value = '';
  }

  async function submitEmail(event) {
    event.preventDefault();
    const email = $('emailNew').value.trim();
    const password = $('emailPassword').value;
    if (!password) { setError('emailError', 'Escribe tu contraseña de EVBro.'); return; }
    const btn = $('emailSubmit');
    btn.disabled = true;
    setError('emailError', '');
    try {
      const res = await fetch('/api/auth/email', {
        method: 'PUT', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ email: email || null, current_password: password }),
      });
      const data = await res.json().catch(() => ({}));
      if (!res.ok) { setError('emailError', errorText(data, 'No se pudo guardar el email.')); return; }
      state.email = data.email || null;
      paintEmail();
      closeEmailModal();
      if (typeof showToast === 'function') showToast(state.email ? 'Email guardado.' : 'Email quitado.', 'success', 4000);
    } catch (e) {
      setError('emailError', 'No hay conexión con el servidor.');
    } finally {
      btn.disabled = false;
    }
  }

  // ───────────────────────── sign-out and password ─────────────────────────
  async function logout() {
    try { await nativeFetch('/api/auth/logout', { method: 'POST', credentials: 'same-origin' }); } catch (e) { /* does not matter */ }
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

  // ───────────────────────── public API ─────────────────────────
  /** Resolves to true if the app can load; to false if the sign-in screen has been shown. */
  function ready() {
    if (!state.readyPromise) {
      state.readyPromise = (async () => {
        takeResetToken();
        try {
          await fetchStatus();
        } catch (e) {
          if (state.mode !== 'reset') return true;   // no network: the app works as before (Service Worker cache)
        }
        const label = $('authCurrentUser');
        if (label) label.textContent = state.username || '—';
        paintEmail();
        if (state.mode === 'reset') { state.locked = true; showOverlay(); return false; }
        if (state.authenticated) return true;
        state.locked = true;
        showOverlay();
        return false;
      })();
    }
    return state.readyPromise;
  }

  /** Is there still a session? Used by the WebSocket before reconnecting. No network → true (it retries). */
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
    if (sw) sw.addEventListener('click', () => { state.registerMode = !state.registerMode; state.notice = null; configureForm(); });
    const pk = $('authPasskey');
    if (pk) pk.addEventListener('click', submitPasskey);
    const pwdForm = $('passwordForm');
    if (pwdForm) pwdForm.addEventListener('submit', submitPasswordChange);
    const forgot = $('authForgotLink');
    if (forgot) forgot.addEventListener('click', () => {
      $('authForgotLogin').value = $('authUsername').value.trim();
      $('authForgotSent').hidden = true;
      setMode('forgot');
    });
    document.querySelectorAll('[data-auth-back]').forEach((b) => b.addEventListener('click', () => setMode('login')));
    const forgotForm = $('authForgotForm');
    if (forgotForm) forgotForm.addEventListener('submit', submitForgot);
    const resetForm = $('authResetForm');
    if (resetForm) resetForm.addEventListener('submit', submitReset);
    const emailForm = $('emailForm');
    if (emailForm) emailForm.addEventListener('submit', submitEmail);
  });

  window.EbroAuth = {
    get locked() { return state.locked; },
    get username() { return state.username; },
    ready, verify, logout, openPasswordModal, closePasswordModal, openEmailModal, closeEmailModal,
  };
})();
