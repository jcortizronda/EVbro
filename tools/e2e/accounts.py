"""Cuentas asociadas: crear una cuenta, darle un coche con un nivel y ver qué ve y qué no. Ver tools/e2e/README.md."""
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
    pg.on("dialog", lambda d: (errs.append("diálogo del navegador: " + d.message), d.dismiss()))
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
        ok(pg.evaluate("document.body.dataset.level") == "owner" and pg.evaluate("window.accountInfo.role") == "manager", f"{tag}: la gestora es propietaria de su coche")

        # Ajustes → Compartir (cuentas asociadas)
        pg.evaluate("switchTab('settings'); switchSettingsSubTab('accounts')")
        pg.wait_for_timeout(700)
        ok(pg.is_visible("#settingsSubTabAccounts"), f"{tag}: aparece la pestaña Compartir")
        ok(pg.text_content("#btnSettingsSubAccount").strip() == "Cuenta" and pg.text_content("#btnSettingsSubAccounts").strip() == "Compartir", f"{tag}: las pestañas Cuenta y Compartir no se confunden")
        ok(pg.locator("#memReadOnly").count() == 0, f"{tag}: ya no hay «solo lectura» en las cuentas asociadas")
        pg.click("text=Añadir cuenta")
        pg.fill("#memUser", MEMBER)
        pg.fill("#memPass", MEMBER_PW)
        pg.click("#memberModal >> text=Crear cuenta")
        pg.wait_for_timeout(900)
        ok(MEMBER in pg.text_content("#accMembers"), f"{tag}: la cuenta asociada aparece")
        ok(pg.locator("#accMembers select").count() == 1, f"{tag}: hay un selector de permiso por coche")
        pg.screenshot(path=f"{OUT}/accounts-{theme}.png", full_page=True)

        # Ajustes → Coche: la lista de coches elige el coche de toda la aplicación
        pg.evaluate("switchTab('vehicle')")
        pg.wait_for_timeout(600)
        ok("sin límite" in pg.text_content("#accCarsLimit") and "1 coche " in pg.text_content("#accCarsLimit"), f"{tag}: la gestora de la demo no tiene límite de coches ({pg.text_content('#accCarsLimit').strip()})")
        pg.click("text=Añadir coche sin cuenta Ebro")
        pg.fill("#manName", "Mi otro Ebro")
        ok(pg.locator("#manBrand option").count() > 30 and pg.input_value("#manBrand") == "EBRO" and pg.is_visible("#manModel") and not pg.is_visible("#manModelText"),
           f"{tag}: el alta sin cuenta Ebro pide la marca (EBRO por defecto, con la lista de modelos de EBRO)")
        pg.select_option("#manBrand", "Toyota")
        ok(not pg.is_visible("#manModel") and pg.is_visible("#manModelText"), f"{tag}: con otra marca el modelo se escribe a mano")
        pg.fill("#manModelText", "Corolla")
        pg.click("#manualVehicleModal >> button:has-text('Añadir')")
        pg.wait_for_timeout(1300)
        ok("Mi otro Ebro" in pg.text_content("#accCars") and "Sin cuenta Ebro" in pg.text_content("#accCars"), f"{tag}: el coche manual se añade")
        new_vin = pg.evaluate("currentVin")
        ok(new_vin != vin and pg.input_value("#vpName") == "Mi otro Ebro", f"{tag}: el coche nuevo queda elegido y la ficha es la suya")
        ok(pg.input_value("#vpBrand") == "Toyota" and pg.input_value("#vpModelText") == "Corolla", f"{tag}: la ficha trae la marca y el modelo elegidos")
        ok("Mi otro Ebro" in pg.text_content(".car-pick.is-current"), f"{tag}: la lista marca el coche elegido")
        ok(pg.input_value("#vehicleSelector") == new_vin, f"{tag}: el selector de la cabecera también cambia")
        pg.screenshot(path=f"{OUT}/cars-{theme}.png", full_page=True)
        pg.click(".car-pick:has-text('Ebro Demo')")
        pg.wait_for_timeout(900)
        ok(pg.evaluate("currentVin") == vin and pg.input_value("#vpName") == "Ebro Demo", f"{tag}: al tocar otro coche, la ficha cambia a ese")
        pg.reload()
        pg.wait_for_load_state("load")
        pg.wait_for_timeout(2500)
        ok(pg.evaluate("currentVin") == vin, f"{tag}: el coche elegido se recuerda al recargar")
        pg.evaluate("switchTab('settings'); switchSettingsSubTab('accounts')")
        pg.wait_for_timeout(800)
        ok(pg.locator("#accMembers select").count() == 2, f"{tag}: el coche nuevo ya se puede compartir")

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
            ok(not merrs, f"{tag}/{level}: sin errores JS {merrs[:2]}")
            mctx.close()
        v, e, c = results["view"], results["edit"], results["control"]
        ok(v["level"] == "view" and v["cars"] == ["view"], f"{tag}: la cuenta con «Ver» solo ve su coche ({v['cars']})")
        ok(v["climate"] == 0 and v["lock"] == 0 and v["chargeCtl"] == 0, f"{tag}: con «Ver» no hay órdenes (Confort {v['climate']}, órdenes {v['lock']}, carga {v['chargeCtl']})")
        ok(v["edit_btn"] == 0, f"{tag}: con «Ver» no se puede añadir gastos")
        ok(v["sheet"] == 0 and v["accounts"] == 0 and v["polling"] == 0, f"{tag}: un miembro no ve la ficha, las cuentas ni el sondeo")
        ok(e["level"] == "edit" and e["edit_btn"] >= 1 and e["climate"] == 0 and e["lock"] == 0, f"{tag}: con «Editar» puede anotar gastos pero no controlar")
        ok(c["level"] == "control" and c["climate"] == 1 and c["lock"] >= 1 and c["edit_btn"] >= 1 and c["sheet"] == 0, f"{tag}: con «Controlar» ve las órdenes pero no la ficha")

        # el miembro tampoco puede por la API aunque salte la interfaz
        mctx, mpg, _ = open_page(b, MEMBER, MEMBER_PW, theme)
        codes = mpg.evaluate("""async () => {
          const vin = currentVin;
          const post = (u, b) => fetch(u, {method:'POST', headers:{'Content-Type':'application/json'}, body: JSON.stringify(b)}).then(r => r.status);
          const put = (u, b) => fetch(u, {method:'PUT', headers:{'Content-Type':'application/json'}, body: JSON.stringify(b)}).then(r => r.status);
          return [await put('/api/vehicles/' + vin, {name: 'x'}), await post('/api/members', {username: 'otra', password: 'abcdefghij12345'}),
                  await fetch('/api/admin/users').then(r => r.status), await fetch('/api/vehicles/LSJA8888888888888/latest').then(r => r.status)];
        }""")
        ok(codes[:3] == [403, 403, 404], f"{tag}: por la API el miembro sigue sin poder tocar la ficha, crear cuentas ni administrar: las rutas de administración ni existen en la app ({codes})")
        mctx.close()

        # quitar el acceso: el miembro deja de ver el coche
        pg.select_option("#accMembers select >> nth=0", "")
        pg.wait_for_timeout(700)
        mctx, mpg, _ = open_page(b, MEMBER, MEMBER_PW, theme)
        ok(mpg.evaluate("allVehiclesList.length") == 0, f"{tag}: sin enlace la cuenta no ve ningún coche")
        mctx.close()
        pg.evaluate("switchTab('settings'); switchSettingsSubTab('accounts')")
        pg.wait_for_timeout(400)
        over = pg.evaluate("document.documentElement.scrollWidth - document.documentElement.clientWidth")
        ok(over <= 0, f"{tag}: Cuentas sin desbordamiento horizontal ({over} px)")
        ok(not errs, f"{tag}: sin errores JS {errs[:2]}")
        ctx.close()
        if theme == "dark":
            import subprocess
            subprocess.run([os.path.join(os.path.dirname(__file__), "demo_server.sh"), "start"], check=True, capture_output=True)
    b.close()
print("FALLOS:", fails if fails else "ninguno")
sys.exit(1 if fails else 0)
