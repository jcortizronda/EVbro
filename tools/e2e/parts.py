"""Consumables and part numbers: car sheet, search and the parts of interventions. See tools/e2e/README.md."""
import os
import sys
import tempfile
from playwright.sync_api import sync_playwright

BASE = os.environ.get("EVBRO_URL", "http://127.0.0.1:8099")
USER = os.environ.get("EVBRO_USER", "juan")
PASSWORD = os.environ.get("EVBRO_PASSWORD", "una-contraseña-larga-1")
OUT = sys.argv[1] if len(sys.argv) > 1 else tempfile.mkdtemp(prefix="evbro-e2e-")
fails = []


def ok(c, m):
    print(("  ✔ " if c else "  ✘ ") + m)
    if not c:
        fails.append(m)


with sync_playwright() as p:
    b = p.chromium.launch()
    for theme in ("dark", "light"):
        ctx = b.new_context(viewport={"width": 390, "height": 844}, is_mobile=True, has_touch=True, locale="es-ES")
        ctx.request.post(f"{BASE}/api/auth/login", headers={"Origin": BASE}, data={"username": USER, "password": PASSWORD})
        ctx.add_init_script(f"localStorage.setItem('evbro_theme','{theme}')")
        ctx.add_init_script("new MutationObserver(() => { const b = document.querySelector('#appDialog.open #appDialogOk'); if (b) b.click(); }).observe(document, { attributes: true, subtree: true, attributeFilter: ['class'] })")   # the app's own confirm dialog is accepted
        pg = ctx.new_page()
        errs = []
        pg.on("pageerror", lambda e: errs.append(str(e)))
        pg.on("dialog", lambda d: (errs.append("browser dialog: " + d.message), d.dismiss()))
        pg.goto(BASE)
        pg.wait_for_load_state("networkidle")
        pg.wait_for_timeout(1200)
        tag = theme
        txt = lambda s: pg.text_content(s).strip()

        # car sheet: Consumibles tab
        pg.evaluate("openVehicleSettings()")
        pg.wait_for_timeout(500)
        ok(pg.is_visible("#vpPaneData") and pg.is_hidden("#vpPaneParts"), f"{tag}: the sheet starts on Datos")
        pg.click("button[data-pane='parts']")
        pg.wait_for_timeout(700)
        ok(pg.is_visible("#vpPaneParts") and pg.is_hidden("#vpPaneData"), f"{tag}: the switch shows Consumibles")
        ok(pg.locator("#partsSuggest .maint-chip").count() >= 10, f"{tag}: categories still to record are suggested ({pg.locator('#partsSuggest .maint-chip').count()})")

        # add a part from a suggestion
        pg.click("#partsSuggest .maint-chip:has-text('Aceite de motor')")
        pg.wait_for_timeout(400)
        ok(pg.is_visible("#partModal") and pg.input_value("#partModalFields [data-f=category]") == "engine_oil", f"{tag}: the suggestion opens the form with its category")
        pg.fill("#partModalFields [data-f=brand]", "Castrol")
        pg.fill("#partModalFields [data-f=reference]", "15B7A2")
        pg.fill("#partModalFields [data-f=spec]", "0W-20")
        pg.fill("#partModalFields [data-f=quantity]", "4.2")
        pg.click("#partModal >> text=Guardar")
        pg.wait_for_timeout(900)
        ok("Castrol" in txt("#partsGroups") and "15B7A2" in txt("#partsGroups"), f"{tag}: the part shows up grouped with its part number")
        ok("Aceite de motor" not in txt("#partsSuggest"), f"{tag}: the category is no longer suggested")

        # search
        pg.fill("#partsSearch", "15b7")
        pg.wait_for_timeout(200)
        ok(pg.locator(".part-item").count() == 1, f"{tag}: the search finds by part number")
        pg.fill("#partsSearch", "zzzz")
        pg.wait_for_timeout(200)
        ok("No hay nada" in txt("#partsGroups"), f"{tag}: with no matches it says so")
        pg.fill("#partsSearch", "")

        # intervention with tyres: they are logged in the history and update the sheet
        pg.evaluate("switchTab('maintenance')")
        pg.wait_for_timeout(800)
        pg.evaluate("showMaintEventModal()")
        pg.wait_for_timeout(500)
        pg.click("#evChips .maint-chip:has-text('Neumáticos nuevos')")
        pg.wait_for_timeout(300)
        ok(pg.locator("#evPartsBox [data-part-row]").count() == 1 and pg.input_value("#evPartsBox [data-f=category]") == "tyres", f"{tag}: the suggested intervention opens a tyre row")
        ok(pg.is_visible("#evPartsBox [data-f=dot]"), f"{tag}: the tyres ask for the manufacturing date (DOT)")
        pg.select_option("#evPartsBox [data-f=position]", "front")
        pg.fill("#evPartsBox [data-f=brand]", "Michelin")
        pg.fill("#evPartsBox [data-f=spec]", "235/55 R19")
        pg.select_option("#evPartsBox [data-f=season]", "summer")
        pg.fill("#evPartsBox [data-f=dot]", "1224")
        pg.fill("#evCost", "280")
        pg.screenshot(path=f"{OUT}/parts-event-{theme}.png", full_page=True)
        pg.click("#maintEventModal >> text=Guardar")
        pg.wait_for_timeout(1000)
        hist = pg.eval_on_selector_all("#maintLogsTableBody tr", "e=>e.map(r=>r.innerText)")
        ok(any("Michelin" in r and "DOT 1224" in r for r in hist), f"{tag}: the history shows the fitted parts")
        parts = pg.evaluate("fetch('/api/vehicles/'+currentVin+'/parts').then(r=>r.json())")
        ok(any(x["category"] == "tyres" and "dot" not in x["attrs"] and x["position"] == "front" for x in parts), f"{tag}: the sheet is updated with the tyres, without the DOT")
        pg.evaluate("openPartModal(null, 'tyres')"); pg.wait_for_timeout(400)
        ok(pg.is_visible("#partModalFields [data-f=season]") and not pg.is_visible("#partModalFields [data-f=dot]"), f"{tag}: the sheet form asks for the tyre type but not the DOT")
        pg.evaluate("closePartModal()")

        # an invalid DOT is not sent
        pg.evaluate("showMaintEventModal()")
        pg.wait_for_timeout(400)
        pg.fill("#evTitle", "Prueba")
        pg.click("#evPartsBox >> text=Añadir pieza")
        pg.select_option("#evPartsBox [data-f=category]", "tyres")
        pg.select_option("#evPartsBox [data-f=position]", "rear")
        pg.fill("#evPartsBox [data-f=dot]", "ab")
        pg.click("#maintEventModal >> text=Guardar")
        pg.wait_for_timeout(700)
        ok(pg.is_visible("#maintEventModal"), f"{tag}: with an invalid DOT the form does not close")
        pg.click("#maintEventModal >> text=Cancelar")

        # view of the sheet with parts
        pg.evaluate("openVehicleSettings(); setVehiclePane('parts')")
        pg.wait_for_timeout(800)
        pg.screenshot(path=f"{OUT}/parts-sheet-{theme}.png", full_page=True)
        over = pg.evaluate("document.documentElement.scrollWidth - document.documentElement.clientWidth")
        ok(over <= 0, f"{tag}: no horizontal overflow ({over} px)")
        ok(not errs, f"{tag}: no JS errors {errs[:2]}")
        ctx.close()
        if theme == "dark":
            import subprocess
            subprocess.run([os.path.join(os.path.dirname(__file__), "demo_server.sh"), "start"], check=True, capture_output=True)
    b.close()
print("FAILURES:", fails if fails else "none")
sys.exit(1 if fails else 0)
