"""Mantenimiento: interruptor Oficial/Personalizado, fecha de entrega, registrar escalón y tareas propias. Ver tools/e2e/README.md."""
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
        pg.on("dialog", lambda d: (errs.append("diálogo del navegador: " + d.message), d.dismiss()))
        pg.goto(BASE)
        pg.wait_for_load_state("networkidle")
        pg.wait_for_timeout(1200)
        vin = pg.evaluate("currentVin")
        tag = theme
        pg.evaluate("switchTab('maintenance')")
        pg.wait_for_timeout(900)
        txt = lambda s: pg.text_content(s).strip()
        ok(pg.get_attribute("#maintModeOfficial", "aria-selected") == "true" and pg.is_visible("#maintOfficialView") and pg.is_hidden("#maintCustomView"),
           f"{tag}: arranca en modo Oficial con su vista")
        ok("Revisión de 15.000 km" in txt("#maintNextTitle"), f"{tag}: próxima revisión = {txt('#maintNextTitle')}")
        ok(pg.evaluate("document.getElementById('maintBaselineCard').classList.contains('needs-setup')"), f"{tag}: pide la fecha de entrega")
        ok(pg.is_hidden("#maintMeterDateBox"), f"{tag}: sin fecha de entrega no hay medidor de fecha")
        pg.screenshot(path=f"{OUT}/maint-official-{theme}.png", full_page=True)

        # fecha de entrega: se indica en la ficha del coche (Ajustes, Coche)
        pg.click("#maintBaselineCard >> text=Cambiar en la ficha del coche")
        pg.wait_for_timeout(500)
        pg.evaluate("foldVehicleSections(true)")
        ok(pg.is_visible("#vpDeliveryDate"), f"{tag}: el botón lleva a la ficha del coche")
        pg.fill("#vpDeliveryDate", "2025-03-31")
        pg.click("#vpSave")
        pg.wait_for_timeout(900)
        pg.evaluate("switchTab('maintenance')")
        pg.wait_for_timeout(900)
        ok(pg.is_visible("#maintMeterDateBox") and "31 de marzo de 2026" in txt("#maintMeterDateSub"), f"{tag}: con la fecha aparece el medidor y vence el 31/03/2026 ({txt('#maintMeterDateSub')})")
        ok(not pg.evaluate("document.getElementById('maintBaselineCard').classList.contains('needs-setup')"), f"{tag}: la tarjeta de fecha ya no se marca como pendiente")

        # registrar el escalón de 15.000
        pg.click("#btnRegisterRevision")
        pg.wait_for_timeout(400)
        opts = pg.eval_on_selector_all("#regRevInterval option", "e=>e.map(o=>o.value)")
        ok(opts[0] == "15000" and len(opts) == 6, f"{tag}: el formulario ofrece los 6 escalones pendientes, el primero preseleccionado ({opts})")
        pg.fill("#regRevCost", "125.5")
        pg.click("#registerOfficialRevisionModal >> text=Guardar")
        pg.wait_for_timeout(900)
        ok("Revisión de 30.000 km" in txt("#maintNextTitle"), f"{tag}: tras registrarla, el plan pasa a 30.000 km ({txt('#maintNextTitle')})")
        steps = pg.eval_on_selector_all("#maintSteps .maint-step", "e=>e.map(x=>x.className)")
        ok("done" in steps[0] and "next" in steps[1], f"{tag}: escalones: 15k hecho, 30k siguiente ({steps[:2]})")
        pg.click("#btnRegisterRevision")
        pg.wait_for_timeout(300)
        opts2 = pg.eval_on_selector_all("#regRevInterval option", "e=>e.map(o=>o.value)")
        ok("15000" not in opts2, f"{tag}: el escalón ya hecho no se puede volver a registrar ({opts2})")
        pg.click("#registerOfficialRevisionModal >> text=Cancelar")
        pg.evaluate("document.querySelector('.maint-matrix-details').open = true")
        pg.wait_for_timeout(200)
        pg.screenshot(path=f"{OUT}/maint-official-after-{theme}.png", full_page=True)

        # historial común
        pg.wait_for_timeout(300)
        rows = pg.eval_on_selector_all("#maintLogsTableBody tr", "e=>e.map(r=>r.innerText)")
        ok(any("Revisión oficial EBRO" in r for r in rows), f"{tag}: la revisión queda en el historial común")

        # modo personalizado
        pg.click("#maintModeCustom")
        pg.wait_for_timeout(800)
        ok(pg.is_visible("#maintCustomView") and pg.is_hidden("#maintOfficialView"), f"{tag}: el interruptor muestra la vista personalizada")
        n0 = pg.eval_on_selector_all("#maintenanceGrid .maintenance-card", "e=>e.length")
        chips = pg.eval_on_selector_all("#maintSuggestions .maint-chip", "e=>e.map(x=>x.textContent.trim())")
        ok(len(chips) > 0, f"{tag}: sugeridas disponibles ({len(chips)})")
        pg.screenshot(path=f"{OUT}/maint-custom-{theme}.png", full_page=True)
        first = chips[0]
        pg.click("#maintSuggestions .maint-chip >> nth=0")
        pg.wait_for_timeout(300)
        ok(pg.input_value("#itemMaintName") == first, f"{tag}: la sugerida rellena el formulario ({first})")
        pg.click("#itemMaintModal >> text=Guardar")
        pg.wait_for_timeout(800)
        n1 = pg.eval_on_selector_all("#maintenanceGrid .maintenance-card", "e=>e.length")
        chips2 = pg.eval_on_selector_all("#maintSuggestions .maint-chip", "e=>e.map(x=>x.textContent.trim())")
        ok(n1 == n0 + 1 and first not in chips2, f"{tag}: la tarea se añade ({n0}→{n1}) y deja de sugerirse")
        # editar el intervalo
        pg.click("#maintenanceGrid .maintenance-card:last-child >> [aria-label='Editar']")
        pg.wait_for_timeout(300)
        ok(pg.input_value("#itemMaintId") != "" and pg.input_value("#itemMaintName") == first, f"{tag}: «Editar» abre el formulario con la tarea")
        pg.fill("#itemMaintKm", "5000")
        pg.click("#itemMaintModal >> text=Guardar")
        pg.wait_for_timeout(800)
        ok("cada 5000 km" in pg.inner_text("#maintenanceGrid .maintenance-card:last-child"), f"{tag}: el nuevo intervalo se refleja en la tarjeta")
        # hecho
        pg.click("#maintenanceGrid .maintenance-card:last-child >> text=Hecho")
        pg.wait_for_timeout(300)
        ok(pg.is_visible("#completeMaintModal"), f"{tag}: «Hecho» abre el registro de la tarea")
        pg.click("#completeMaintModal >> text=Cancelar")
        # borrar
        pg.click("#maintenanceGrid .maintenance-card:last-child >> [aria-label='Eliminar']")
        pg.wait_for_timeout(800)
        n2 = pg.eval_on_selector_all("#maintenanceGrid .maintenance-card", "e=>e.length")
        ok(n2 == n0, f"{tag}: eliminar la tarea ({n1}→{n2})")
        # volver a oficial conserva el estado
        pg.click("#maintModeOfficial")
        pg.wait_for_timeout(600)
        ok("Revisión de 30.000 km" in txt("#maintNextTitle"), f"{tag}: al volver a Oficial el plan sigue donde estaba")
        ok(pg.is_visible("#maintModeSwitch"), f"{tag}: un coche EBRO tiene el interruptor Oficial/Personalizado")

        # el plan oficial es de EBRO: con otra marca solo queda el personalizado (y la ITV)
        pg.evaluate("openVehicleSettings()")
        pg.wait_for_timeout(400)
        pg.select_option("#vpBrand", "Toyota")
        pg.fill("#vpModelText", "Corolla")
        pg.click("#vpSave")
        pg.wait_for_timeout(900)
        pg.evaluate("switchTab('maintenance')")
        pg.wait_for_timeout(900)
        ok(pg.is_hidden("#maintModeSwitch") and pg.is_hidden("#maintOfficialView") and pg.is_visible("#maintCustomView"),
           f"{tag}: con otra marca no hay interruptor ni vista oficial")
        ok(pg.is_visible("#maintItvCard") and "propias tareas" in txt("#maintIntro"), f"{tag}: la ITV sigue y el texto habla del plan propio")
        st = pg.evaluate(f"""fetch('/api/vehicles/{vin}/maintenance/plan', {{method: 'PUT', headers: {{'Content-Type': 'application/json'}},
          body: JSON.stringify({{mode: 'official'}})}}).then(r => r.status)""")
        ok(st == 409, f"{tag}: el servidor no deja pasar a oficial con otra marca ({st})")
        pg.screenshot(path=f"{OUT}/maint-other-brand-{theme}.png", full_page=True)
        pg.evaluate("openVehicleSettings()")
        pg.wait_for_timeout(400)
        pg.select_option("#vpBrand", "EBRO")
        pg.click("#vpSave")
        pg.wait_for_timeout(900)
        pg.evaluate("switchTab('maintenance')")
        pg.wait_for_timeout(900)
        ok(pg.is_visible("#maintModeSwitch") and pg.is_visible("#maintOfficialView"), f"{tag}: al volver a EBRO vuelve el plan oficial")
        over = pg.evaluate("document.documentElement.scrollWidth - document.documentElement.clientWidth")
        ok(over <= 0, f"{tag}: sin desbordamiento horizontal ({over} px)")
        ok(not errs, f"{tag}: sin errores JS {errs[:2]}")
        ctx.close()
        # el servidor de demo se reinicia entre temas para partir del mismo estado
        if theme == "dark":
            import subprocess
            subprocess.run([os.path.join(os.path.dirname(__file__), "demo_server.sh"), "start"], check=True, capture_output=True)
    b.close()
print("FALLOS:", fails if fails else "ninguno")
sys.exit(1 if fails else 0)
