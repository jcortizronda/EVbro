"""Browser test (Playwright): Ajustes → Cuenta → «Borrar la cuenta». The dialog asks for the username typed by hand and for the
password; a wrong password deletes nothing; the real deletion ends on the sign-in screen and the account no longer opens.
The dialog also carries an optional survey (why the account is deleted): what is answered reaches the admin console, with
the username.

DESTRUCTIVE for the demo server: it deletes the demo manager, so it starts its own demo server and stops it at the end (run it
last, or on its own). See tools/e2e/README.md."""
import os
import subprocess
import sys
import tempfile

from playwright.sync_api import sync_playwright

BASE = os.environ.get("EVBRO_URL", "http://127.0.0.1:8099")
CONSOLE = os.environ.get("EVBRO_ADMIN_URL", "http://127.0.0.1:8098")
ADMIN, ADMIN_PW = "admin", "contraseña-del-admin-1"
USER = os.environ.get("EVBRO_USER", "juan")
PASSWORD = os.environ.get("EVBRO_PASSWORD", "una-contraseña-larga-1")
OUT = sys.argv[1] if len(sys.argv) > 1 else tempfile.mkdtemp(prefix="evbro-e2e-")
DEMO = os.path.join(os.path.dirname(os.path.abspath(__file__)), "demo_server.sh")
fails = []


def ok(cond, msg):
    print(("  ✔ " if cond else "  ✘ ") + msg)
    if not cond:
        fails.append(msg)


def open_card(pg):
    pg.evaluate("switchTab('settings'); switchSettingsSubTab('account')")
    pg.wait_for_timeout(400)
    pg.evaluate("document.getElementById('accountDeleteCard').open = true")
    pg.locator("#accountDeleteCard").scroll_into_view_if_needed()


subprocess.run([DEMO, "start"], check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
try:
    with sync_playwright() as p:
        browser = p.chromium.launch()
        # 1. The dialog, in both themes, without deleting anything
        for theme in ("dark", "light"):
            ctx = browser.new_context(viewport={"width": 390, "height": 844}, is_mobile=True, has_touch=True, locale="es-ES", service_workers="block")
            assert ctx.request.post(f"{BASE}/api/auth/login", headers={"Origin": BASE}, data={"username": USER, "password": PASSWORD}).ok
            ctx.add_init_script(f"localStorage.setItem('evbro_theme','{theme}')")
            pg = ctx.new_page()
            errs = []
            pg.on("pageerror", lambda e: errs.append(str(e)))
            pg.goto(BASE)
            pg.wait_for_load_state("networkidle")
            pg.wait_for_timeout(1200)
            open_card(pg)
            ok(pg.is_visible("#accountDeleteCard >> text=Borrar mi cuenta"), f"{theme}: the manager sees «Borrar mi cuenta» in Ajustes → Cuenta")
            pg.screenshot(path=f"{OUT}/account-delete-card-{theme}.png")
            pg.click("#accountDeleteCard >> text=Borrar mi cuenta")
            pg.wait_for_timeout(300)
            ok(pg.is_visible("#accountDeleteModal.open") and f"«{USER}»" in pg.inner_text("#adDeleteConfirmLabel"), f"{theme}: the dialog asks to type the username")
            ok(pg.is_disabled("#adDeleteSubmit"), f"{theme}: «Borrar todo» starts disabled")
            ok(pg.locator("input[name='adReason']").count() == 10 and pg.is_visible("#adDeleteComment") and "opcional" in pg.inner_text(".ad-survey legend"),
               f"{theme}: the dialog offers the optional survey (reasons and a comment)")
            pg.screenshot(path=f"{OUT}/account-delete-survey-{theme}.png")
            pg.fill("#adDeleteConfirm", "borrar")
            pg.fill("#adDeletePassword", PASSWORD)
            ok(pg.is_disabled("#adDeleteSubmit"), f"{theme}: another word does not enable it")
            pg.fill("#adDeleteConfirm", USER.upper())
            ok(pg.is_enabled("#adDeleteSubmit"), f"{theme}: the username (capitals or not) and a password enable it")
            pg.screenshot(path=f"{OUT}/account-delete-{theme}.png")
            box = pg.evaluate("""() => { const c = document.querySelector('#accountDeleteModal .modal-card').getBoundingClientRect();
                return { left: c.left, right: c.right, bottom: c.bottom, w: innerWidth, h: innerHeight }; }""")
            ok(box["left"] >= 0 and box["right"] <= box["w"] + 0.5, f"{theme}: the dialog fits the width of the phone ({box})")
            over = pg.evaluate("document.documentElement.scrollWidth - document.documentElement.clientWidth")
            ok(over <= 0, f"{theme}: no horizontal overflow ({over} px)")
            pg.fill("#adDeletePassword", "no-es-la-contraseña")
            pg.click("#adDeleteSubmit")
            pg.wait_for_timeout(900)
            ok(pg.is_visible("#adDeleteError") and "contraseña" in pg.inner_text("#adDeleteError").lower() and pg.is_visible("#accountDeleteModal.open"),
               f"{theme}: a wrong password says so and deletes nothing ({pg.inner_text('#adDeleteError')!r})")
            pg.click("#accountDeleteModal >> text=Cancelar")
            ok(pg.is_hidden("#accountDeleteModal.open") and pg.input_value("#adDeletePassword") == "", f"{theme}: Cancelar closes it and forgets the password typed")
            ok(ctx.request.get(f"{BASE}/api/vehicles").ok, f"{theme}: the account is still there")
            ok(not errs, f"{theme}: no JS errors {errs}")
            ctx.close()

        # 2. The real thing
        ctx = browser.new_context(viewport={"width": 390, "height": 844}, is_mobile=True, has_touch=True, locale="es-ES", service_workers="block")
        assert ctx.request.post(f"{BASE}/api/auth/login", headers={"Origin": BASE}, data={"username": USER, "password": PASSWORD}).ok
        pg = ctx.new_page()
        errs = []
        pg.on("pageerror", lambda e: errs.append(str(e)))
        pg.goto(BASE)
        pg.wait_for_load_state("networkidle")
        pg.wait_for_timeout(1200)
        pg.evaluate("localStorage.setItem('evbro_vehicle', 'X'); localStorage.setItem('evbro_theme', 'dark')")
        open_card(pg)
        pg.click("#accountDeleteCard >> text=Borrar mi cuenta")
        pg.check("input[name='adReason'][value='not_working']")
        pg.check("input[name='adReason'][value='other']")
        pg.fill("#adDeleteComment", "No leía bien mi coche.\n<b>esto no es HTML</b>")
        pg.fill("#adDeleteConfirm", USER)
        pg.fill("#adDeletePassword", PASSWORD)
        pg.click("#adDeleteSubmit")
        pg.wait_for_load_state("load")
        pg.wait_for_timeout(2500)
        ok(pg.is_visible("#authForm"), "deleted: the page comes back on the sign-in screen")
        kept = pg.evaluate("[localStorage.getItem('evbro_vehicle'), localStorage.getItem('evbro_theme')]")
        ok(kept == [None, "dark"], f"deleted: this browser forgets the account's choices and keeps the theme ({kept})")
        ok(ctx.request.get(f"{BASE}/api/vehicles").status == 401, "deleted: the old session opens nothing")
        r = ctx.request.post(f"{BASE}/api/auth/login", headers={"Origin": BASE}, data={"username": USER, "password": PASSWORD})
        ok(r.status == 401, f"deleted: the username and password no longer sign in ({r.status})")
        pg.screenshot(path=f"{OUT}/account-deleted.png")
        ok(not errs, f"deleted: no JS errors {errs}")
        ctx.close()

        # 3. The administrator reads the answer in the console, with the username
        con = browser.new_context(viewport={"width": 1280, "height": 900}, locale="es-ES")
        cp = con.new_page()
        cerrs = []
        cp.on("pageerror", lambda e: cerrs.append(str(e)))
        cp.goto(CONSOLE)
        cp.wait_for_selector("#viewAuth:not([hidden])")
        cp.fill("#authUser", ADMIN)
        cp.fill("#authPass", ADMIN_PW)
        cp.click("#authSubmit")
        cp.wait_for_selector("#tblExit tbody tr")
        row = cp.inner_text("#tblExit tbody tr")
        ok("No funciona bien con mi coche" in row and "Otro motivo" in row and "<b>esto no es HTML</b>" in row and USER in row,
           f"console: the answer is there, as text and with the username ({row!r})")
        ok("No funciona bien con mi coche" in cp.inner_text("#exitReasons"), "console: the reasons are counted")
        cp.locator("#tblExit").scroll_into_view_if_needed()
        cp.screenshot(path=f"{OUT}/account-delete-console.png", full_page=True)
        ok(not cerrs, f"console: no JS errors {cerrs}")
        con.close()
        browser.close()
finally:
    subprocess.run([DEMO, "stop"], check=False, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
print("FAILURES: " + ("; ".join(fails) if fails else "none"))
sys.exit(1 if fails else 0)
