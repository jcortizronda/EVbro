"""Browser test (Playwright): charges written down by the user. Carga → Historial de cargas → «Añadir carga» (only the kWh are
required; a geofence fills in its price; the charge can be corrected and deleted) and «Importar» (an Excel book: review of the
columns and of every row, import, and undoing it). See tools/e2e/README.md."""
import datetime
import io
import os
import sys
import tempfile

from openpyxl import Workbook
from playwright.sync_api import sync_playwright

BASE = os.environ.get("EVBRO_URL", "http://127.0.0.1:8099")
USER = os.environ.get("EVBRO_USER", "juan")
PASSWORD = os.environ.get("EVBRO_PASSWORD", "una-contraseña-larga-1")
OUT = sys.argv[1] if len(sys.argv) > 1 else tempfile.mkdtemp(prefix="evbro-e2e-")
fails = []


def ok(cond, msg):
    print(("  ✔ " if cond else "  ✘ ") + msg)
    if not cond:
        fails.append(msg)


def book() -> bytes:
    wb = Workbook()
    for row in (["Día", "Energía (kWh)", "Importe", "Sitio"],
                [datetime.datetime(2026, 9, 19), 22.4, 11.2, "Cargador rápido de la autovía"],
                ["07/09/2026", "18,8 kWh", "2,54 €", "Supermercado"],
                [None, 9.6, 0, "Trabajo"],
                ["31/02/2026", 12, 1.62, "Supermercado"],
                ["01/07/2026", None, 3, "Supermercado"]):
        wb.active.append(row)
    buffer = io.BytesIO()
    wb.save(buffer)
    return buffer.getvalue()


def rows(pg):
    return pg.locator("#chargesList .hist-row summary").all_inner_texts()


with sync_playwright() as p:
    browser = p.chromium.launch()
    for theme in ("dark", "light"):
        ctx = browser.new_context(viewport={"width": 390, "height": 844}, is_mobile=True, has_touch=True, locale="es-ES", service_workers="block")
        assert ctx.request.post(f"{BASE}/api/auth/login", headers={"Origin": BASE}, data={"username": USER, "password": PASSWORD}).ok
        ctx.add_init_script(f"localStorage.setItem('evbro_theme','{theme}')")
        ctx.add_init_script("localStorage.setItem('evbro_fold_charges','1');localStorage.setItem('evbro_fold_refuels','1')")   # the histories live in folds that start closed: open here
        pg = ctx.new_page()
        errs = []
        pg.on("pageerror", lambda e: errs.append(str(e)))
        pg.goto(BASE)
        pg.wait_for_load_state("networkidle")
        pg.wait_for_timeout(1200)
        pg.evaluate("switchTab('charges')")
        pg.wait_for_selector("#chargesList .hist-row, #chargesList .hist-empty")
        before = len(rows(pg))
        ok(pg.is_visible(".mc-actions >> text=Añadir carga") and pg.is_visible(".mc-actions >> text=Importar"), f"{theme}: the history offers «Añadir carga» and «Importar»")

        # ── 1. a charge with nothing but its kWh
        pg.click(".mc-actions >> text=Añadir carga")
        pg.wait_for_selector("#chargeManualModal.open")
        pg.wait_for_timeout(300)
        pg.screenshot(path=f"{OUT}/manual-form-{theme}.png")
        over = pg.evaluate("document.documentElement.scrollWidth - document.documentElement.clientWidth")
        ok(over <= 0, f"{theme}: the form does not overflow the phone ({over} px)")
        pg.click("#mcSave")
        ok(pg.is_visible("#mcError") and "kWh" in pg.inner_text("#mcError"), f"{theme}: without kWh it says so and saves nothing")
        pg.fill("#mcKwh", "9.6")
        pg.click("#mcSave")
        pg.wait_for_selector("#chargeManualModal:not(.open)", state="attached")
        pg.wait_for_function("n => document.querySelectorAll('#chargesList .hist-row').length === n", arg=before + 1, timeout=8000)
        texts = rows(pg)
        ok(len(texts) == before + 1 and any("Manual" in t and "Sin fecha" in t and "9,6 kWh" in t for t in texts), f"{theme}: the charge is in the history, «Manual» and «Sin fecha» ({[t for t in texts if 'Manual' in t]})")

        # ── 2. a full one: a geofence fills in its price and the total follows; a total typed afterwards reworks the price
        pg.click(".mc-actions >> text=Añadir carga")
        pg.wait_for_selector("#chargeManualModal.open")
        pg.fill("#mcKwh", "20")
        pg.fill("#mcDay", "2026-09-08")
        pg.fill("#mcHour", "23:10")
        pg.fill("#mcBattery", "72")
        geofences = pg.locator("#mcPlace optgroup >> nth=0").locator("option").all_inner_texts()
        ok(len(geofences) >= 1, f"{theme}: «Dónde» lists the geofences ({geofences})")
        pg.select_option("#mcPlace", index=1)
        pg.wait_for_timeout(200)
        price, total = pg.input_value("#mcPrice"), pg.input_value("#mcTotal")
        ok(price != "" and total != "" and "is-auto" in pg.get_attribute("#mcTotal", "class"),
           f"{theme}: the geofence fills in its price and the total is worked out ({price!r}, {total!r})")
        how = pg.inner_text("#mcHow")
        ok("Lado del cargador" in how and "eficiencia" in how, f"{theme}: the note says it goes by the charger and gives the efficiency ({how!r})")
        pg.select_option("#mcPlace", value="__other__")
        ok(pg.is_visible("#mcPlaceText"), f"{theme}: «Otro sitio…» opens a field for the name")
        pg.fill("#mcPlaceText", "Supermercado")
        pg.fill("#mcTotal", "5")
        ok(pg.input_value("#mcPrice") == "0.25" and pg.input_value("#mcKwh") == "20" and "is-auto" in pg.get_attribute("#mcPrice", "class"),
           f"{theme}: typing the total reworks the price, not the kWh ({pg.input_value('#mcPrice')!r})")
        pg.click("#mcSave")
        pg.wait_for_selector("#chargeManualModal:not(.open)", state="attached")
        pg.wait_for_timeout(900)
        texts = rows(pg)
        ok(any("Supermercado" in t and "+72 %" in t and "5,00 €" in t and "+20,0 kWh" in t for t in texts),
           f"{theme}: it shows its day, its place, the battery gained and its cost ({[t for t in texts if 'Supermercado' in t]})")

        # ── 2b. the battery side: only the % and the price
        pg.click(".mc-actions >> text=Añadir carga")
        pg.wait_for_selector("#chargeManualModal.open")
        pg.fill("#mcBattery", "50")
        pg.fill("#mcPrice", "0.2")
        how = pg.inner_text("#mcHow")
        ok("Lado de la batería" in how and "Coste" in how, f"{theme}: with the % and the price alone, the note says it goes by the battery ({how!r})")
        pg.click("#mcSave")
        pg.wait_for_selector("#chargeManualModal:not(.open)", state="attached")
        pg.wait_for_timeout(900)
        ok(any("≈ +" in t and "+50 %" in t for t in rows(pg)), f"{theme}: a battery-side charge shows its energy as an estimate")

        # ── 3. correcting and deleting it
        row = pg.locator("#chargesList .hist-row", has_text="Supermercado").first
        row.locator("summary").click()
        row.locator("text=Editar").click()
        pg.wait_for_selector("#chargeManualModal.open")
        pg.wait_for_timeout(300)
        ok(pg.input_value("#mcKwh") == "20" and pg.input_value("#mcDay") == "2026-09-08" and pg.input_value("#mcPlaceText") == "Supermercado"
           and pg.input_value("#mcTotal") == "5", f"{theme}: editing opens the form with what was typed")
        pg.fill("#mcKwh", "21.5")
        pg.click("#mcSave")
        pg.wait_for_timeout(900)
        ok(any("21,5 kWh" in t for t in rows(pg)), f"{theme}: the history shows the kWh typed (the charger's), not the battery estimate")
        row = pg.locator("#chargesList .hist-row", has_text="Supermercado").first
        row.locator("summary").click()
        facts = row.locator(".hist-facts").inner_text().lower()
        ok("del cargador" in facts and "21,5 kwh" in facts and "eficiencia" in facts and "estimada" in facts,
           f"{theme}: with the % the detail adds the battery estimate and the efficiency ({facts!r})")
        detected = pg.locator("#chargesList .hist-row", has_not_text="Manual").first
        detected.locator("summary").click()
        ok(detected.locator("text=Editar").count() == 0 and detected.locator("text=Ver detalle y gráfica").count() == 1, f"{theme}: a charge the car reported has no «Editar» or «Borrar»")

        # ── 4. a spreadsheet
        pg.click(".mc-actions >> text=Importar")
        pg.wait_for_selector("#chargeImportModal.open")
        pg.wait_for_timeout(300)
        pg.screenshot(path=f"{OUT}/import-1-{theme}.png")
        ok("/charges/import/template" in pg.get_attribute("#ciTemplate", "href"), f"{theme}: step 1 offers the example book")
        pg.set_input_files("#ciFile", {"name": "mis-cargas.xlsx", "mimeType": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", "buffer": book()})
        pg.wait_for_selector("#ciStep2:not([hidden])")
        pg.wait_for_timeout(300)
        mapped = {f: pg.locator(f"#ciMap-{f} option:checked").inner_text() for f in ("kwh", "date", "total", "place", "time")}
        ok(mapped == {"kwh": "«Energía (kWh)»", "date": "«Día»", "total": "«Importe»", "place": "«Sitio»", "time": "No está"},
           f"{theme}: the columns are understood from their titles ({mapped})")
        counts = pg.inner_text("#ciCounts")
        # the row of 8 September is the charge just typed (21.5 against 18.8 kWh: not the same); two rows cannot be charges
        ok("Listas: 3" in counts and "Para revisar: 2" in counts, f"{theme}: it says what is ready and what is not ({counts!r})")
        first = pg.locator("#ciRows .ci-row").first.inner_text()
        ok("Revisar" in first and ("no se entiende" in first or "faltan" in first), f"{theme}: what will not come in is listed first, with its reason ({first!r})")
        ok(pg.inner_text("#ciImport") == "Importar 3 cargas", f"{theme}: the button says how many will be imported")
        over = pg.evaluate("document.documentElement.scrollWidth - document.documentElement.clientWidth")
        ok(over <= 0, f"{theme}: the review does not overflow the phone ({over} px)")
        pg.screenshot(path=f"{OUT}/import-2-{theme}.png")
        pg.select_option("#ciMap-kwh", value="")
        pg.wait_for_timeout(700)
        ok(pg.is_disabled("#ciImport") and "kWh" in pg.inner_text("#ciError2"), f"{theme}: without a column for the kWh nothing can be imported")
        pg.select_option("#ciMap-kwh", index=2)
        pg.wait_for_timeout(700)
        n = len(rows(pg))
        pg.click("#ciImport")
        pg.wait_for_selector("#ciStep3:not([hidden])")
        ok(pg.inner_text("#ciDoneCount") == "3" and "kWh" in pg.inner_text("#ciDoneText"), f"{theme}: step 3 says what came in ({pg.inner_text('#ciDoneText')!r})")
        pg.screenshot(path=f"{OUT}/import-3-{theme}.png")
        pg.click("#chargeImportModal button:has-text('Hecho')")
        pg.wait_for_timeout(1000)
        ok(len(rows(pg)) == n + 3, f"{theme}: the three charges are in the history ({len(rows(pg))} rows)")
        pg.locator("#chargesList").scroll_into_view_if_needed()
        pg.screenshot(path=f"{OUT}/manual-history-{theme}.png")

        # ── 5. the same file again brings only the row without a day; undoing takes it away
        pg.click(".mc-actions >> text=Importar")
        pg.set_input_files("#ciFile", {"name": "mis-cargas.xlsx", "mimeType": "application/octet-stream", "buffer": book()})
        pg.wait_for_selector("#ciStep2:not([hidden])")
        ok("Listas: 1" in pg.inner_text("#ciCounts") and "Ya estaban: 2" in pg.inner_text("#ciCounts") and pg.inner_text("#ciImport") == "Importar 1 carga", f"{theme}: the same file again does not double the charges ({pg.inner_text('#ciCounts')!r})")
        pg.click("#ciImport")
        pg.wait_for_selector("#ciStep3:not([hidden])")
        pg.click("#chargeImportModal button:has-text('Deshacer')")
        pg.click("#appDialogOk")
        pg.wait_for_timeout(1200)
        ok(len(rows(pg)) == n + 3 and not pg.is_visible("#chargeImportModal.open"), f"{theme}: undoing takes that import away and nothing else ({len(rows(pg))} rows)")

        # ── clean up for the next theme: delete what this one added
        for item in ctx.request.get(f"{BASE}/api/vehicles/{pg.evaluate('currentVin')}/charges?limit=200").json():
            if item["manual"]:
                ctx.request.delete(f"{BASE}/api/charges/{item['id']}", headers={"Origin": BASE})
        ok(not errs, f"{theme}: no JS errors {errs}")
        ctx.close()
    browser.close()
print("FAILURES: " + ("; ".join(fails) if fails else "none"))
sys.exit(1 if fails else 0)
