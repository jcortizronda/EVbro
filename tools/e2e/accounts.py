"""Linked accounts: create an account, give it a car with a level and check what it sees and what it does not. See tools/e2e/README.md."""
import os
import sys
import tempfile
from playwright.sync_api import sync_playwright

BASE = os.environ.get("EVBRO_URL", "http://127.0.0.1:8099")
USER = os.environ.get("EVBRO_USER", "juan")
PASSWORD = os.environ.get("EVBRO_PASSWORD", "una-contraseña-larga-1")
OUT = sys.argv[1] if len(sys.argv) > 1 else tempfile.mkdtemp(prefix="evbro-e2e-")
MEMBER, MEMBER_PW = "lucia", "contraseña-de-lucia-1"
fails = []


def ok(c, m):
    print(("  ✔ " if c else "  ✘ ") + m)
    if not c:
        fails.append(m)


def open_page(b, user, password, theme):
    ctx = b.new_context(viewport={"width": 390, "height": 844}, is_mobile=True, has_touch=True, locale="es-ES")
    r = ctx.request.post(f"{BASE}/api/auth/login", headers={"Origin": BASE}, data={"username": user, "password": password})
    assert r.ok, r.text()
    ctx.add_init_script(f"localStorage.setItem('evbro_theme','{theme}')")
    ctx.add_init_script("new MutationObserver(() => { const b = document.querySelector('#appDialog.open #appDialogOk'); if (b) b.click(); }).observe(document, { attributes: true, subtree: true, attributeFilter: ['class'] })")   # the app's own confirm dialog is accepted
    pg = ctx.new_page()
    errs = []
    pg.on("pageerror", lambda e: errs.append(str(e)))
    pg.on("dialog", lambda d: (errs.append("browser dialog: " + d.message), d.dismiss()))
    pg.goto(BASE)
    pg.wait_for_load_state("load")
    pg.wait_for_timeout(2500)
    return ctx, pg, errs


def visible_count(pg, selector):
    return pg.evaluate("(s) => [...document.querySelectorAll(s)].filter(e => e.offsetParent !== null).length", selector)


with sync_playwright() as p:
    b = p.chromium.launch()
    for theme in ("dark", "light"):
        tag = theme
        ctx, pg, errs = open_page(b, USER, PASSWORD, theme)
        vin = pg.evaluate("currentVin")
        ok(pg.evaluate("document.body.dataset.level") == "owner" and pg.evaluate("window.accountInfo.role") == "manager", f"{tag}: the manager owns her car")

        # Ajustes → Compartir (linked accounts)
        pg.evaluate("switchTab('settings'); switchSettingsSubTab('accounts')")
        pg.wait_for_timeout(700)
        ok(pg.is_visible("#settingsSubTabAccounts"), f"{tag}: the Compartir tab shows up")
        ok(pg.text_content("#btnSettingsSubAccount").strip() == "Cuenta" and pg.text_content("#btnSettingsSubAccounts").strip() == "Compartir", f"{tag}: the Cuenta and Compartir tabs are not mixed up")
        ok(pg.locator("#memReadOnly").count() == 0, f"{tag}: there is no «solo lectura» in the linked accounts any more")
        pg.click("text=Añadir cuenta")
        pg.fill("#memUser", MEMBER)
        pg.fill("#memPass", MEMBER_PW)
        pg.click("#memberModal >> text=Crear cuenta")
        pg.wait_for_timeout(900)
        ok(MEMBER in pg.text_content("#accMembers"), f"{tag}: the linked account shows up")
        ok(pg.locator("#accMembers select").count() == 1, f"{tag}: there is a permission picker per car")
        pg.screenshot(path=f"{OUT}/accounts-{theme}.png", full_page=True)

        # Ajustes → Coche: the car list picks the car for the whole app
        pg.evaluate("switchTab('vehicle')")
        pg.wait_for_timeout(600)
        ok("sin límite" in pg.text_content("#accCarsLimit") and "1 coche " in pg.text_content("#accCarsLimit"), f"{tag}: the demo manager has no car limit ({pg.text_content('#accCarsLimit').strip()})")
        pg.click("text=Añadir coche sin cuenta Ebro")
        pg.fill("#manName", "Mi otro Ebro")
        ok(pg.locator("#manBrand option").count() > 30 and pg.input_value("#manBrand") == "EBRO" and pg.is_visible("#manModel") and not pg.is_visible("#manModelText"),
           f"{tag}: adding a car without an Ebro account asks for the brand (EBRO by default, with the EBRO model list)")
        pg.select_option("#manBrand", "Toyota")
        ok(not pg.is_visible("#manModel") and pg.is_visible("#manModelText"), f"{tag}: with another brand the model is typed by hand")
        pg.fill("#manModelText", "Corolla")
        pg.click("#manualVehicleModal >> button:has-text('Añadir')")
        pg.wait_for_timeout(1300)
        ok("Mi otro Ebro" in pg.text_content("#accCars") and "Sin cuenta Ebro" in pg.text_content("#accCars"), f"{tag}: the manual car is added")
        new_vin = pg.evaluate("currentVin")
        ok(new_vin != vin and pg.input_value("#vpName") == "Mi otro Ebro", f"{tag}: the new car stays chosen and the sheet is its own")
        ok(pg.input_value("#vpBrand") == "Toyota" and pg.input_value("#vpModelText") == "Corolla", f"{tag}: the sheet carries the chosen brand and model")
        ok("Mi otro Ebro" in pg.text_content(".car-pick.is-current"), f"{tag}: the list marks the chosen car")
        ok(pg.input_value("#vehicleSelector") == new_vin, f"{tag}: the header picker changes too")
        pg.screenshot(path=f"{OUT}/cars-{theme}.png", full_page=True)
        pg.click(".car-pick:has-text('Ebro Demo')")
        pg.wait_for_timeout(900)
        ok(pg.evaluate("currentVin") == vin and pg.input_value("#vpName") == "Ebro Demo", f"{tag}: tapping another car switches the sheet to it")
        pg.reload()
        pg.wait_for_load_state("load")
        pg.wait_for_timeout(2500)
        ok(pg.evaluate("currentVin") == vin, f"{tag}: the chosen car is remembered on reload")
        pg.evaluate("switchTab('settings'); switchSettingsSubTab('accounts')")
        pg.wait_for_timeout(800)
        ok(pg.locator("#accMembers select").count() == 2, f"{tag}: the new car can already be shared")

        def set_level(level):
            pg.select_option("#accMembers select >> nth=0", level)
            pg.wait_for_timeout(700)

        results = {}
        for level in ("view", "edit", "control"):
            set_level(level)
            mctx, mpg, merrs = open_page(b, MEMBER, MEMBER_PW, theme)
            lv = mpg.evaluate("document.body.dataset.level")
            cars = mpg.evaluate("allVehiclesList.map(v => v.access_level)")
            results[level] = {
                "level": lv, "cars": cars,
                "climate": visible_count(mpg, "#mobTabClimate"), "lock": visible_count(mpg, "[data-on-click*='promptCommand']"),
                "chargeCtl": visible_count(mpg, "#chargeControl"),
                "edit_btn": 0, "sheet": visible_count(mpg, "#btnSettingsSubVehicle"), "accounts": visible_count(mpg, "#btnSettingsSubAccounts"),
                "polling": visible_count(mpg, "#btnSettingsSubPolling"),
            }
            mpg.evaluate("switchTab('expenses')")
            mpg.wait_for_timeout(700)
            results[level]["edit_btn"] = visible_count(mpg, "#tabExpenses [data-on-click*='openExpenseModal']")
            if level == "view":
                mpg.screenshot(path=f"{OUT}/member-view-{theme}.png")
            ok(not merrs, f"{tag}/{level}: no JS errors {merrs[:2]}")
            mctx.close()
        v, e, c = results["view"], results["edit"], results["control"]
        ok(v["level"] == "view" and v["cars"] == ["view"], f"{tag}: the account with «Ver» only sees its car ({v['cars']})")
        ok(v["climate"] == 0 and v["lock"] == 0 and v["chargeCtl"] == 0, f"{tag}: with «Ver» there are no orders (Confort {v['climate']}, orders {v['lock']}, charge {v['chargeCtl']})")
        ok(v["edit_btn"] == 0, f"{tag}: with «Ver» expenses cannot be added")
        ok(v["sheet"] == 0 and v["accounts"] == 0 and v["polling"] == 0, f"{tag}: a member does not see the sheet, the accounts or the polling")
        ok(e["level"] == "edit" and e["edit_btn"] >= 1 and e["climate"] == 0 and e["lock"] == 0, f"{tag}: with «Editar» it can log expenses but not control")
        ok(c["level"] == "control" and c["climate"] == 1 and c["lock"] >= 1 and c["edit_btn"] >= 1 and c["sheet"] == 0, f"{tag}: with «Controlar» it sees the orders but not the sheet")

        # the member cannot do it through the API either, even bypassing the interface
        mctx, mpg, _ = open_page(b, MEMBER, MEMBER_PW, theme)
        codes = mpg.evaluate("""async () => {
          const vin = currentVin;
          const post = (u, b) => fetch(u, {method:'POST', headers:{'Content-Type':'application/json'}, body: JSON.stringify(b)}).then(r => r.status);
          const put = (u, b) => fetch(u, {method:'PUT', headers:{'Content-Type':'application/json'}, body: JSON.stringify(b)}).then(r => r.status);
          return [await put('/api/vehicles/' + vin, {name: 'x'}), await post('/api/members', {username: 'otra', password: 'abcdefghij12345'}),
                  await fetch('/api/admin/users').then(r => r.status), await fetch('/api/vehicles/LSJA8888888888888/latest').then(r => r.status)];
        }""")
        ok(codes[:3] == [403, 403, 404], f"{tag}: through the API the member still cannot touch the sheet, create accounts or administer: the admin routes do not even exist in the app ({codes})")
        mctx.close()

        # remove the access: the member stops seeing the car
        pg.select_option("#accMembers select >> nth=0", "")
        pg.wait_for_timeout(700)
        mctx, mpg, _ = open_page(b, MEMBER, MEMBER_PW, theme)
        ok(mpg.evaluate("allVehiclesList.length") == 0, f"{tag}: without a link the account sees no car")
        mctx.close()
        pg.evaluate("switchTab('settings'); switchSettingsSubTab('accounts')")
        pg.wait_for_timeout(400)
        over = pg.evaluate("document.documentElement.scrollWidth - document.documentElement.clientWidth")
        ok(over <= 0, f"{tag}: Cuentas without horizontal overflow ({over} px)")
        ok(not errs, f"{tag}: no JS errors {errs[:2]}")
        ctx.close()
        if theme == "dark":
            import subprocess
            subprocess.run([os.path.join(os.path.dirname(__file__), "demo_server.sh"), "start"], check=True, capture_output=True)
    b.close()
print("FAILURES:", fails if fails else "none")
sys.exit(1 if fails else 0)
