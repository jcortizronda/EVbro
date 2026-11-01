"""Fingerprint sign-in (passkeys) with Chromium's virtual authenticator (CDP WebAuthn). See tools/e2e/README.md.

WebAuthn does not accept an IP address as RP ID: open the demo as http://localhost:PORT, not 127.0.0.1.
"""
import os
import sys
import tempfile
from playwright.sync_api import sync_playwright

BASE = os.environ.get("EVBRO_URL", "http://localhost:8099").replace("127.0.0.1", "localhost")
USER = os.environ.get("EVBRO_USER", "juan")
PASSWORD = os.environ.get("EVBRO_PASSWORD", "una-contraseña-larga-1")
OUT = sys.argv[1] if len(sys.argv) > 1 else tempfile.mkdtemp(prefix="evbro-e2e-")
fails = []


def ok(c, m):
    print(("  ✔ " if c else "  ✘ ") + m)
    if not c:
        fails.append(m)


def authenticated(pg):
    return pg.evaluate("fetch('/api/auth/status').then(r => r.json()).then(d => d.authenticated)")


def open_account(pg):
    pg.evaluate("switchTab('settings'); switchSettingsSubTab('account')")
    pg.wait_for_timeout(600)


def items(pg):
    return pg.evaluate("[...document.querySelectorAll('#passkeyList .passkey-item')].map(li => li.innerText)")


with sync_playwright() as p:
    b = p.chromium.launch()
    for theme in ("dark", "light"):
        print("==", theme)
        # bypass_csp: `wait_for_function` evaluates a string in the page, which the app's CSP forbids (csp.py checks the policy itself)
        ctx = b.new_context(viewport={"width": 390, "height": 844}, is_mobile=True, has_touch=True, locale="es-ES", bypass_csp=True)
        ctx.add_init_script(f"localStorage.setItem('evbro_theme','{theme}')")
        ctx.add_init_script("new MutationObserver(() => { const b = document.querySelector('#appDialog.open #appDialogOk'); if (b) b.click(); }).observe(document, { attributes: true, subtree: true, attributeFilter: ['class'] })")   # the app's own confirm dialog is accepted
        pg = ctx.new_page()
        errs = []
        pg.on("pageerror", lambda e: errs.append(str(e)))
        pg.on("dialog", lambda d: (errs.append("browser dialog: " + d.message), d.dismiss()))
        cdp = ctx.new_cdp_session(pg)
        cdp.send("WebAuthn.enable")
        auth_id = cdp.send("WebAuthn.addVirtualAuthenticator", {"options": {
            "protocol": "ctap2", "transport": "internal", "hasResidentKey": True, "hasUserVerification": True,
            "isUserVerified": True, "automaticPresenceSimulation": True}})["authenticatorId"]

        # 1. sign-in screen: the button shows up; sign in with the password
        pg.goto(BASE)
        pg.wait_for_selector("#authOverlay:not([hidden])")
        ok(pg.is_visible("#authPasskey") and "Entrar con huella" in pg.inner_text("#authPasskey"), "the sign-in screen offers «Entrar con huella»")
        pg.screenshot(path=f"{OUT}/passkey-login-{theme}.png")
        pg.fill("#authUsername", USER)
        pg.fill("#authPassword", PASSWORD)
        with pg.expect_navigation():
            pg.click("#authSubmit")
        pg.wait_for_timeout(1500)
        ok(authenticated(pg), "signs in with username and password")

        # 2. register this device in Ajustes → Cuenta
        open_account(pg)
        ok(pg.is_visible("#passkeyCard") and pg.is_visible("#btnPasskeyAdd"), "Ajustes → Cuenta shows «Acceso con huella» and «Añadir este dispositivo»")
        ok(pg.is_visible("#passkeyEmpty") and items(pg) == [], "no devices: it says so")
        pg.click("#btnPasskeyAdd")
        pg.wait_for_function("document.querySelectorAll('#passkeyList .passkey-item').length === 1", timeout=8000)
        rows = items(pg)
        ok(len(rows) == 1 and "Chrome en " in rows[0] and "sin usar aún" in rows[0], f"the device is registered: {rows}")
        creds = cdp.send("WebAuthn.getCredentials", {"authenticatorId": auth_id})["credentials"]
        ok(len(creds) == 1 and creds[0]["isResidentCredential"] and creds[0]["rpId"] == "localhost", "the credential is discoverable and its RP ID is the host")
        ok(pg.is_hidden("#passkeyEmpty"), "the empty-list text disappears")
        pg.locator("#passkeyCard").screenshot(path=f"{OUT}/passkey-card-{theme}.png")
        ok(pg.evaluate("document.documentElement.scrollWidth <= window.innerWidth"), "no horizontal overflow at 390 px")

        # 3. sign out and sign in with the fingerprint (without typing the username)
        with pg.expect_navigation():
            pg.evaluate("EbroAuth.logout()")
        pg.wait_for_selector("#authOverlay:not([hidden])")
        ok(not authenticated(pg), "signs out")
        with pg.expect_navigation():
            pg.click("#authPasskey")
        pg.wait_for_timeout(1500)
        ok(authenticated(pg) and pg.is_hidden("#authOverlay"), "signs in with «Entrar con huella»")

        # 4. the list reflects the use; remove the device
        open_account(pg)
        rows = items(pg)
        ok(len(rows) == 1 and "último uso" in rows[0], f"the list shows the last use: {rows}")
        pg.click("#passkeyList .passkey-item button")
        pg.wait_for_function("document.querySelectorAll('#passkeyList .passkey-item').length === 0", timeout=8000)
        ok(items(pg) == [] and pg.is_visible("#passkeyEmpty"), "removing the device deletes it from the list")

        # 5. a fingerprint the server no longer knows: clear error, no session
        with pg.expect_navigation():
            pg.evaluate("EbroAuth.logout()")
        pg.wait_for_selector("#authOverlay:not([hidden])")
        pg.click("#authPasskey")
        pg.wait_for_selector("#authError:not([hidden])", timeout=8000)
        msg = pg.inner_text("#authError")
        ok("huella" in msg and not authenticated(pg), f"with the device removed it does not sign in and explains why: «{msg}»")
        pg.screenshot(path=f"{OUT}/passkey-login-error-{theme}.png")
        ok(not errs, f"no JavaScript errors: {errs}")
        ctx.close()
    b.close()

print("Screenshots in", OUT)
print("FAILURES:", fails if fails else "none")
sys.exit(1 if fails else 0)
