"""Admin console (another port, desktop interface): sign-in, managers, invitations, limits, the admin not seeing the
cars, the two separate doors and the server settings. See tools/e2e/README.md."""
import os
import sys
import tempfile
from playwright.sync_api import sync_playwright

BASE = os.environ.get("EVBRO_URL", "http://127.0.0.1:8099")
CONSOLE = os.environ.get("EVBRO_ADMIN_URL", "http://127.0.0.1:8098")
ADMIN, ADMIN_PW = "admin", "contraseña-del-admin-1"
OUT = sys.argv[1] if len(sys.argv) > 1 else tempfile.mkdtemp(prefix="evbro-e2e-")
MGR, MGR_PW = "maria", "contraseña-de-maria-1"
fails = []


def ok(c, m):
    print(("  ✔ " if c else "  ✘ ") + m)
    if not c:
        fails.append(m)


OBSERVER = """new MutationObserver(() => {
  const dlg = document.getElementById('dlgAsk');
  if (!dlg || !dlg.open || dlg.dataset.answered === '1') return;
  dlg.dataset.answered = '1';
  const field = document.getElementById('askField');
  if (!field.hidden) document.getElementById('askInput').value = (window.__answers || []).shift() || '';
  setTimeout(() => { document.getElementById('askOk').click(); delete dlg.dataset.answered; }, 20);
}).observe(document, { attributes: true, subtree: true, attributeFilter: ['open'] })"""


def answers_for(pg, queue):
    """The console's own dialog (ask()) is accepted; a text field takes the next value of `window.__answers`. A browser dialog is a failure."""
    pg.add_init_script(OBSERVER)
    pg.on("dialog", lambda d: (queue.append("browser dialog: " + d.message), d.dismiss()))


with sync_playwright() as p:
    b = p.chromium.launch()
    for theme in ("dark", "light"):
        tag = theme
        ctx = b.new_context(viewport={"width": 1280, "height": 800}, color_scheme=theme)
        pg = ctx.new_page()
        errs, queue = [], []
        pg.on("pageerror", lambda e: errs.append(str(e)))
        pg.on("console", lambda m: errs.append(m.text) if m.type == "error" and "Failed to load resource" not in m.text else None)
        answers_for(pg, queue)

        # sign-in
        pg.goto(CONSOLE)
        pg.wait_for_selector("#viewAuth:not([hidden])")
        ok(pg.text_content("#authTitle").strip() == "Iniciar sesión", f"{tag}: the console asks to sign in (the admin already exists)")
        ok(pg.is_hidden("#rowCode"), f"{tag}: it does not ask for the setup code if there is already an admin")
        pg.fill("#authUser", ADMIN)
        pg.fill("#authPass", "x" * 12)
        pg.click("#authSubmit")
        pg.wait_for_timeout(600)
        ok(pg.is_visible("#authError"), f"{tag}: a wrong password is rejected")
        pg.fill("#authPass", ADMIN_PW)
        pg.click("#authSubmit")
        pg.wait_for_selector("#viewApp:not([hidden])")
        ok(pg.text_content("#whoName").strip() == ADMIN, f"{tag}: the admin gets into the console")
        pg.screenshot(path=f"{OUT}/admin-managers-{theme}.png")

        # the demo manager (juan) already exists: their count is visible but not their car
        pg.wait_for_selector("#tblManagers tbody tr")
        rows = pg.eval_on_selector_all("#tblManagers tbody tr", "e=>e.map(r=>r.innerText)")
        ok(any("juan" in r and "1 /" in r for r in rows), f"{tag}: the admin sees the demo manager with their number of cars")
        ok("Ebro Demo" not in pg.content() and "LSJA8888888888888" not in pg.content(), f"{tag}: but neither the name nor the VIN of their car")

        # create a manager
        pg.click("#btnNewManager")
        pg.fill("#mgrUser", MGR)
        pg.fill("#mgrPass", MGR_PW)
        pg.fill("#mgrVeh", "1")
        pg.fill("#mgrSto", "50")
        pg.click("#formManager button[type=submit]")
        pg.wait_for_timeout(900)
        rows = pg.eval_on_selector_all("#tblManagers tbody tr", "e=>e.map(r=>r.innerText)")
        ok(any(MGR in r and "0 / 1" in r for r in rows), f"{tag}: the manager is created with a limit of 1 car")
        heads = pg.eval_on_selector_all("#tblManagers thead th", "e=>e.map(x=>x.textContent)")
        ok("Espacio" in heads, f"{tag}: the managers table has the «Espacio» column")
        ok(any(MGR in r and "0 B / 50 MB" in r for r in rows), f"{tag}: the new manager's storage is «0 B / 50 MB»")
        ok(any("juan" in r and "/ 50 MB" in r for r in rows), f"{tag}: the demo manager has the default storage (50 MB)")

        # the sign-out button is not stuck to the change-password one
        gap = pg.evaluate("""() => { const a = document.getElementById('btnPassword').getBoundingClientRect(), b = document.getElementById('btnLogout').getBoundingClientRect(); return b.left - a.right; }""")
        ok(gap >= 24, f"{tag}: «Cerrar sesión» stays apart from «Cambiar contraseña» ({gap:.0f} px)")

        # edit limits in a dialog (cars, linked accounts, expiry, read only)
        pg.click(f"#tblManagers tr:has-text('{MGR}') >> text=Límites")
        pg.wait_for_selector("#dlgLimits[open]")
        ok(pg.input_value("#limVeh") == "1" and pg.input_value("#limMem") == "5", f"{tag}: the limits dialog starts with the current values")
        ok(pg.input_value("#limSto") == "50", f"{tag}: the dialog has the «Espacio (MB)» field with its value")
        pg.fill("#limSto", "-1")
        pg.fill("#limVeh", "3")
        pg.fill("#limMem", "-1")
        pg.fill("#limExp", "2030-01-01")
        pg.screenshot(path=f"{OUT}/admin-limits-{theme}.png")
        pg.click("#formLimits button[type=submit]")
        pg.wait_for_timeout(900)
        row = pg.text_content(f"#tblManagers tr:has-text('{MGR}')")
        ok("0 / 3" in row and "0 / ∞" in row and "2030" in row, f"{tag}: the limits and the expiry change ({' '.join(row.split())[:90]})")
        ok("sin límite" in row, f"{tag}: the storage becomes «sin límite»")

        # the manager gets into the app; the admin does NOT
        actx = b.new_context(viewport={"width": 390, "height": 844}, is_mobile=True)
        app = actx.new_page()
        app.goto(BASE)
        app.wait_for_selector("#authOverlay:not([hidden])")
        app.fill("#authUsername", ADMIN)
        app.fill("#authPassword", ADMIN_PW)
        app.click("#authSubmit")
        app.wait_for_timeout(800)
        ok(app.is_visible("#authError"), f"{tag}: the admin cannot get into the app")
        app.fill("#authUsername", MGR)
        app.fill("#authPassword", MGR_PW)
        app.click("#authSubmit")
        app.wait_for_load_state("load")
        app.wait_for_timeout(2500)
        ok(app.evaluate("window.accountInfo.role") == "manager" and app.evaluate("window.accountInfo.vehicleLimit") == 3, f"{tag}: the manager gets into the app with her limits")
        # adds a manual car: the admin only sees the count
        app.evaluate("fetch('/api/vehicles',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({name:'Coche secreto de María'})})")
        app.wait_for_timeout(700)
        pg.click("#btnLogout")
        pg.wait_for_selector("#viewAuth:not([hidden])")
        pg.fill("#authUser", ADMIN)
        pg.fill("#authPass", ADMIN_PW)
        pg.click("#authSubmit")
        pg.wait_for_selector("#viewApp:not([hidden])")
        pg.wait_for_timeout(600)
        row = pg.text_content(f"#tblManagers tr:has-text('{MGR}')")
        ok("1 / 3" in row and "Coche secreto" not in pg.content(), f"{tag}: the admin sees «1 / 3» cars of the manager, not her name")

        # statistics
        pg.click("[data-tab=stats]")
        pg.wait_for_timeout(900)
        kpis = pg.text_content("#kpiMain")
        ok("Cuentas de usuario" in kpis and "Coches" in kpis and "Base de datos" in kpis, f"{tag}: the Estadísticas tab shows the totals")
        ok(pg.locator("#chartGrowth rect").count() == 8, f"{tag}: the sign-ups per week chart has 8 bars")
        usage = pg.text_content("#tblUsage")
        ok(MGR in usage and "juan" in usage, f"{tag}: the usage per manager lists the managers")
        ok("Ebro Demo" not in pg.content() and "Coche secreto" not in pg.content() and "LSJA8888888888888" not in pg.content(), f"{tag}: the statistics show no car")
        pg.screenshot(path=f"{OUT}/admin-stats-{theme}.png", full_page=True)

        # invitations
        pg.click("[data-tab=invites]")
        pg.fill("#invNote", "Beto (beta)")
        pg.fill("#invSto", "25")
        pg.click("#formInvite button[type=submit]")
        pg.wait_for_timeout(900)
        token = pg.text_content("#tokenValue").strip()
        ok(token.startswith("ebm_") and pg.is_visible("#tokenBox"), f"{tag}: the code is shown once")
        ok("Beto (beta)" in pg.text_content("#tblInvites") and "Activa" in pg.text_content("#tblInvites"), f"{tag}: the invitation shows as active")
        # signs up from a clean context (the manager's session stays open in another one)
        rctx = b.new_context(viewport={"width": 390, "height": 844}, is_mobile=True)
        rp = rctx.new_page()
        rp.goto(BASE)
        rp.wait_for_selector("#authOverlay:not([hidden])")
        rp.click("#authSwitch")
        rp.fill("#authInviteCode", token)
        rp.fill("#authUsername", "beto")
        rp.fill("#authPassword", "contraseña-de-beto-1")
        rp.fill("#authPassword2", "contraseña-de-beto-1")
        rp.click("#authSubmit")
        rp.wait_for_timeout(400)
        ok(rp.is_visible("#authError") and "casilla" in rp.inner_text("#authError"), f"{tag}: without accepting the data notice the account is not created")
        rp.check("#authConsent")
        rp.click("#authSubmit")
        rp.wait_for_load_state("load")
        rp.wait_for_timeout(2500)
        ok(rp.evaluate("window.accountInfo.role") == "manager", f"{tag}: Beto signs up in the app with the code")
        beto_storage = rp.evaluate("fetch('/api/account/storage').then(r => r.json())")
        ok(beto_storage == {"used_bytes": 0, "limit_mb": 25}, f"{tag}: Beto starts with the invitation's storage ({beto_storage})")
        pg.reload()
        pg.wait_for_selector("#viewApp:not([hidden])")
        pg.click("[data-tab=invites]")
        pg.wait_for_timeout(500)
        ok("Usada" in pg.text_content("#tblInvites") and "beto" in pg.text_content("#tblInvites"), f"{tag}: the invitation is marked as used by beto")
        pg.screenshot(path=f"{OUT}/admin-invites-{theme}.png")

        # server
        pg.click("[data-tab=server]")
        pg.wait_for_timeout(500)
        ok(pg.input_value("#retSnap") == "365" and pg.input_value("#polParked") != "", f"{tag}: the Servidor tab loads retention and polling")
        pg.fill("#retSnap", "200")
        pg.click("#formRetention button[type=submit]")
        pg.wait_for_timeout(800)
        pg.reload()
        pg.wait_for_selector("#viewApp:not([hidden])")
        pg.click("[data-tab=server]")
        pg.wait_for_timeout(500)
        ok(pg.input_value("#retSnap") == "200", f"{tag}: the retention is saved")
        pg.screenshot(path=f"{OUT}/admin-server-{theme}.png")

        # delete María's space (prompt: confirm with the name)
        pg.click("[data-tab=managers]")
        pg.evaluate(f"window.__answers = ['{MGR}']")
        pg.click(f"#tblManagers tr:has-text('{MGR}') >> text=Borrar espacio")
        pg.wait_for_timeout(1000)
        ok(MGR not in pg.text_content("#tblManagers"), f"{tag}: the manager's space is deleted")
        ok(app.evaluate("fetch('/api/vehicles').then(r=>r.status)") == 401, f"{tag}: and her app session stops working")

        # headers and door isolation
        r = ctx.request.get(CONSOLE + "/")
        ok("default-src 'self'" in r.headers.get("content-security-policy", ""), f"{tag}: the console carries a strict CSP")
        ok(ctx.request.get(BASE + "/api/admin/users").status in (401, 404), f"{tag}: the admin routes are not on the app's port")
        errs.extend(queue)                                    # a browser dialog (alert/confirm/prompt) is a failure
        ok(not errs, f"{tag}: no JS or CSP errors {errs[:2]}")
        for c in (ctx, actx, rctx):
            c.close()
        if theme == "dark":
            import subprocess
            subprocess.run([os.path.join(os.path.dirname(__file__), "demo_server.sh"), "start"], check=True, capture_output=True)
    b.close()
print("FAILURES:", fails if fails else "none")
sys.exit(1 if fails else 0)
