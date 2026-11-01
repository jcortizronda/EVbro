"""Maintenance: Oficial/Personalizado switch, delivery date, logging a step and custom tasks. See tools/e2e/README.md."""
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
        vin = pg.evaluate("currentVin")
        tag = theme
        pg.evaluate("switchTab('maintenance')")
        pg.wait_for_timeout(900)
        txt = lambda s: pg.text_content(s).strip()
        ok(pg.get_attribute("#maintModeOfficial", "aria-selected") == "true" and pg.is_visible("#maintOfficialView") and pg.is_hidden("#maintCustomView"),
           f"{tag}: starts in Oficial mode with its view")
        ok("Revisión de 15.000 km" in txt("#maintNextTitle"), f"{tag}: next service = {txt('#maintNextTitle')}")
        ok(pg.evaluate("document.getElementById('maintBaselineCard').classList.contains('needs-setup')"), f"{tag}: asks for the delivery date")
        ok(pg.is_hidden("#maintMeterDateBox"), f"{tag}: without a delivery date there is no date meter")
        pg.screenshot(path=f"{OUT}/maint-official-{theme}.png", full_page=True)

        # delivery date: set in the car sheet (Ajustes, Coche)
        pg.click("#maintBaselineCard >> text=Cambiar en la ficha del coche")
        pg.wait_for_timeout(500)
        pg.evaluate("foldVehicleSections(true)")
        ok(pg.is_visible("#vpDeliveryDate"), f"{tag}: the button leads to the car sheet")
        pg.fill("#vpDeliveryDate", "2025-03-31")
        pg.click("#vpSave")
        pg.wait_for_timeout(900)
        pg.evaluate("switchTab('maintenance')")
        pg.wait_for_timeout(900)
        ok(pg.is_visible("#maintMeterDateBox") and "31 de marzo de 2026" in txt("#maintMeterDateSub"), f"{tag}: with the date the meter shows up and it is due on 31/03/2026 ({txt('#maintMeterDateSub')})")
        ok(not pg.evaluate("document.getElementById('maintBaselineCard').classList.contains('needs-setup')"), f"{tag}: the date card is no longer marked as pending")

        # log the 15.000 step
        pg.click("#btnRegisterRevision")
        pg.wait_for_timeout(400)
        opts = pg.eval_on_selector_all("#regRevInterval option", "e=>e.map(o=>o.value)")
        ok(opts[0] == "15000" and len(opts) == 6, f"{tag}: the form offers the 6 pending steps, the first one preselected ({opts})")
        pg.fill("#regRevCost", "125.5")
        pg.click("#registerOfficialRevisionModal >> text=Guardar")
        pg.wait_for_timeout(900)
        ok("Revisión de 30.000 km" in txt("#maintNextTitle"), f"{tag}: after logging it, the plan moves on to 30.000 km ({txt('#maintNextTitle')})")
        steps = pg.eval_on_selector_all("#maintSteps .maint-step", "e=>e.map(x=>x.className)")
        ok("done" in steps[0] and "next" in steps[1], f"{tag}: steps: 15k done, 30k next ({steps[:2]})")
        pg.click("#btnRegisterRevision")
        pg.wait_for_timeout(300)
        opts2 = pg.eval_on_selector_all("#regRevInterval option", "e=>e.map(o=>o.value)")
        ok("15000" not in opts2, f"{tag}: a step already done cannot be logged again ({opts2})")
        pg.click("#registerOfficialRevisionModal >> text=Cancelar")
        pg.evaluate("document.querySelector('.maint-matrix-details').open = true")
        pg.wait_for_timeout(200)
        pg.screenshot(path=f"{OUT}/maint-official-after-{theme}.png", full_page=True)

        # shared history
        pg.wait_for_timeout(300)
        rows = pg.eval_on_selector_all("#maintLogsTableBody tr", "e=>e.map(r=>r.innerText)")
        ok(any("Revisión oficial EBRO" in r for r in rows), f"{tag}: the service stays in the shared history")

        # custom mode
        pg.click("#maintModeCustom")
        pg.wait_for_timeout(800)
        ok(pg.is_visible("#maintCustomView") and pg.is_hidden("#maintOfficialView"), f"{tag}: the switch shows the custom view")
        n0 = pg.eval_on_selector_all("#maintenanceGrid .maintenance-card", "e=>e.length")
        chips = pg.eval_on_selector_all("#maintSuggestions .maint-chip", "e=>e.map(x=>x.textContent.trim())")
        ok(len(chips) > 0, f"{tag}: suggestions available ({len(chips)})")
        pg.screenshot(path=f"{OUT}/maint-custom-{theme}.png", full_page=True)
        first = chips[0]
        pg.click("#maintSuggestions .maint-chip >> nth=0")
        pg.wait_for_timeout(300)
        ok(pg.input_value("#itemMaintName") == first, f"{tag}: the suggested one fills the form ({first})")
        pg.click("#itemMaintModal >> text=Guardar")
        pg.wait_for_timeout(800)
        n1 = pg.eval_on_selector_all("#maintenanceGrid .maintenance-card", "e=>e.length")
        chips2 = pg.eval_on_selector_all("#maintSuggestions .maint-chip", "e=>e.map(x=>x.textContent.trim())")
        ok(n1 == n0 + 1 and first not in chips2, f"{tag}: the task is added ({n0}→{n1}) and is no longer suggested")
        # edit the interval
        pg.click("#maintenanceGrid .maintenance-card:last-child >> [aria-label='Editar']")
        pg.wait_for_timeout(300)
        ok(pg.input_value("#itemMaintId") != "" and pg.input_value("#itemMaintName") == first, f"{tag}: «Editar» opens the form with the task")
        pg.fill("#itemMaintKm", "5000")
        pg.click("#itemMaintModal >> text=Guardar")
        pg.wait_for_timeout(800)
        ok("cada 5000 km" in pg.inner_text("#maintenanceGrid .maintenance-card:last-child"), f"{tag}: the new interval shows on the card")
        # done
        pg.click("#maintenanceGrid .maintenance-card:last-child >> text=Hecho")
        pg.wait_for_timeout(300)
        ok(pg.is_visible("#completeMaintModal"), f"{tag}: «Hecho» opens the task's log form")
        pg.click("#completeMaintModal >> text=Cancelar")
        # delete
        pg.click("#maintenanceGrid .maintenance-card:last-child >> [aria-label='Eliminar']")
        pg.wait_for_timeout(800)
        n2 = pg.eval_on_selector_all("#maintenanceGrid .maintenance-card", "e=>e.length")
        ok(n2 == n0, f"{tag}: delete the task ({n1}→{n2})")
        # going back to official keeps the state
        pg.click("#maintModeOfficial")
        pg.wait_for_timeout(600)
        ok("Revisión de 30.000 km" in txt("#maintNextTitle"), f"{tag}: back in Oficial the plan is where it was")
        ok(pg.is_visible("#maintModeSwitch"), f"{tag}: an EBRO car has the Oficial/Personalizado switch")

        # the official plan is EBRO's: with another brand only the custom one remains (and the ITV)
        pg.evaluate("openVehicleSettings()")
        pg.wait_for_timeout(400)
        pg.select_option("#vpBrand", "Toyota")
        pg.fill("#vpModelText", "Corolla")
        pg.click("#vpSave")
        pg.wait_for_timeout(900)
        pg.evaluate("switchTab('maintenance')")
        pg.wait_for_timeout(900)
        ok(pg.is_hidden("#maintModeSwitch") and pg.is_hidden("#maintOfficialView") and pg.is_visible("#maintCustomView"),
           f"{tag}: with another brand there is no switch and no official view")
        ok(pg.is_visible("#maintItvCard") and "propias tareas" in txt("#maintIntro"), f"{tag}: the ITV stays and the text speaks of the custom plan")
        st = pg.evaluate(f"""fetch('/api/vehicles/{vin}/maintenance/plan', {{method: 'PUT', headers: {{'Content-Type': 'application/json'}},
          body: JSON.stringify({{mode: 'official'}})}}).then(r => r.status)""")
        ok(st == 409, f"{tag}: the server does not allow switching to official with another brand ({st})")
        pg.screenshot(path=f"{OUT}/maint-other-brand-{theme}.png", full_page=True)
        pg.evaluate("openVehicleSettings()")
        pg.wait_for_timeout(400)
        pg.select_option("#vpBrand", "EBRO")
        pg.click("#vpSave")
        pg.wait_for_timeout(900)
        pg.evaluate("switchTab('maintenance')")
        pg.wait_for_timeout(900)
        ok(pg.is_visible("#maintModeSwitch") and pg.is_visible("#maintOfficialView"), f"{tag}: back to EBRO the official plan returns")
        over = pg.evaluate("document.documentElement.scrollWidth - document.documentElement.clientWidth")
        ok(over <= 0, f"{tag}: no horizontal overflow ({over} px)")
        ok(not errs, f"{tag}: no JS errors {errs[:2]}")
        ctx.close()
        # the demo server restarts between themes to start from the same state
        if theme == "dark":
            import subprocess
            subprocess.run([os.path.join(os.path.dirname(__file__), "demo_server.sh"), "start"], check=True, capture_output=True)
    b.close()
print("FAILURES:", fails if fails else "none")
sys.exit(1 if fails else 0)
