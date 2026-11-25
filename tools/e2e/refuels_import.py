"""Browser test (Playwright): refuels written down by the user. Más → Repostajes → «Añadir repostaje» (litres, price per litre and
total: any two give the third; optional hour) and «Importar» (an Excel book: review of the columns and of every row, import, and
undoing it). See tools/e2e/README.md."""
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
    for row in (["Fecha", "Litros", "Importe", "Km", "Gasolinera", "Lleno"],
                [datetime.datetime(2025, 9, 19), 38.4, 61.0, 12450, "Gasolinera Norte", "Sí"],
                ["08/10/2025", 41.2, "66,30 €", "13.210", "Gasolinera Sur", "Sí"],
                ["20/10/2025", 15, 24, None, None, "no"],
                ["31/02/2025", 30, 48, None, None, None],                 # no such day
                [None, 30, 48, None, None, None]):                        # no day
        wb.active.append(row)
    buffer = io.BytesIO()
    wb.save(buffer)
    return buffer.getvalue()


def rows(pg):
    return pg.locator("#rfList .costs-row-btn").all_inner_texts()


def overflow(pg):
    return pg.evaluate("document.documentElement.scrollWidth - document.documentElement.clientWidth")


with sync_playwright() as p:
    browser = p.chromium.launch()
    for theme in ("dark", "light"):
        ctx = browser.new_context(viewport={"width": 390, "height": 844}, is_mobile=True, has_touch=True, locale="es-ES", service_workers="block")
        assert ctx.request.post(f"{BASE}/api/auth/login", headers={"Origin": BASE}, data={"username": USER, "password": PASSWORD}).ok
        ctx.add_init_script(f"localStorage.setItem('evbro_theme','{theme}')")
        pg = ctx.new_page()
        errs = []
        pg.on("pageerror", lambda e, errs=errs: errs.append(str(e)))
        pg.goto(BASE)
        pg.wait_for_load_state("networkidle")
        pg.wait_for_timeout(1200)
        pg.evaluate("switchTab('refuels')")
        pg.wait_for_timeout(900)
        before = len(rows(pg))
        ok(pg.is_visible("#rfListCard >> text=Añadir repostaje") and pg.is_visible("#rfListCard .rf-actions >> text=Importar"),
           f"{theme}: the list offers «Añadir repostaje» and «Importar»")
        pg.locator("#rfListCard").scroll_into_view_if_needed()
        pg.screenshot(path=f"{OUT}/refuels-head-{theme}.png")

        # ── 1. by the total and the price: the litres are worked out; with an hour
        pg.click("#rfListCard >> text=Añadir repostaje")
        pg.wait_for_selector("#refuelModal.open")
        pg.click("#rfSave")
        ok(pg.is_visible("#rfError") and "litros" in pg.inner_text("#rfError"), f"{theme}: without litres (or total and price) it says so")
        pg.fill("#rfTotal", "60")
        pg.fill("#rfPrice", "1.5")
        ok(pg.input_value("#rfLiters") == "40" and "is-auto" in pg.get_attribute("#rfLiters", "class"), f"{theme}: the total and the price give the litres ({pg.input_value('#rfLiters')!r})")
        pg.fill("#rfDate", "2025-11-02")
        pg.fill("#rfHour", "08:45")
        pg.fill("#rfStation", "Prueba E2E")
        pg.screenshot(path=f"{OUT}/refuel-form-{theme}.png")
        ok(overflow(pg) <= 0, f"{theme}: the form does not overflow the phone")
        pg.click("#rfSave")
        pg.wait_for_selector("#refuelModal:not(.open)", state="attached")
        pg.wait_for_timeout(900)
        mine = [t for t in rows(pg) if "Prueba E2E" in t]
        ok(len(mine) == 1 and "40,00 l" in mine[0] and "60,00 €" in mine[0], f"{theme}: it is in the list with 40 l and 60 € ({mine})")
        pg.locator("#rfList .costs-row-btn", has_text="Prueba E2E").click()
        pg.wait_for_selector("#refuelModal.open")
        ok(pg.input_value("#rfHour") == "08:45" and pg.input_value("#rfDate") == "2025-11-02" and pg.input_value("#rfTotal") == "60"
           and pg.input_value("#rfPrice") == "1.5", f"{theme}: editing brings back the day, the hour, the total and the price")
        pg.click("#refuelModal >> text=Cancelar")

        # ── 2. a spreadsheet
        pg.click("#rfListCard .rf-actions >> text=Importar")
        pg.wait_for_selector("#refuelImportModal.open")
        pg.wait_for_timeout(300)
        pg.screenshot(path=f"{OUT}/refuel-import-1-{theme}.png")
        ok("/refuels/import/template" in pg.get_attribute("#riTemplate", "href"), f"{theme}: step 1 offers the example book")
        pg.set_input_files("#riFile", {"name": "mis-repostajes.xlsx", "mimeType": "application/octet-stream", "buffer": book()})
        pg.wait_for_selector("#riStep2:not([hidden])")
        pg.wait_for_timeout(300)
        mapped = {f: pg.locator(f"#riMap-{f} option:checked").inner_text() for f in ("date", "liters", "total", "odometer", "station", "full", "price")}
        ok(mapped == {"date": "«Fecha»", "liters": "«Litros»", "total": "«Importe»", "odometer": "«Km»", "station": "«Gasolinera»", "full": "«Lleno»", "price": "No está"},
           f"{theme}: the columns are understood from their titles ({mapped})")
        counts = pg.inner_text("#riCounts")
        ok("Listos: 3" in counts and "Para revisar: 2" in counts, f"{theme}: it says what is ready and what is not ({counts!r})")
        ok(pg.inner_text("#riImport") == "Importar 3 repostajes", f"{theme}: the button says how many will be imported")
        ok(overflow(pg) <= 0, f"{theme}: the review does not overflow the phone")
        pg.screenshot(path=f"{OUT}/refuel-import-2-{theme}.png")
        pg.select_option("#riMap-date", value="")
        pg.wait_for_timeout(700)
        ok(pg.is_disabled("#riImport") and "fecha" in pg.inner_text("#riError2"), f"{theme}: without a column for the date nothing can be imported")
        pg.select_option("#riMap-date", index=1)
        pg.wait_for_timeout(700)
        n = len(rows(pg))
        pg.click("#riImport")
        pg.wait_for_selector("#riStep3:not([hidden])")
        ok(pg.inner_text("#riDoneCount") == "3" and "litros" in pg.inner_text("#riDoneText"), f"{theme}: step 3 says what came in ({pg.inner_text('#riDoneText')!r})")
        pg.screenshot(path=f"{OUT}/refuel-import-3-{theme}.png")
        pg.click("#refuelImportModal button:has-text('Hecho')")
        pg.wait_for_timeout(1000)
        ok(len(rows(pg)) == n + 3, f"{theme}: the three refuels are in the list ({len(rows(pg))} rows)")

        # ── 3. the same file again brings nothing; undoing an import takes it away
        pg.click("#rfListCard .rf-actions >> text=Importar")
        pg.set_input_files("#riFile", {"name": "mis-repostajes.xlsx", "mimeType": "application/octet-stream", "buffer": book()})
        pg.wait_for_selector("#riStep2:not([hidden])")
        ok("Ya estaban: 3" in pg.inner_text("#riCounts") and pg.is_disabled("#riImport"), f"{theme}: the same file again does not double them ({pg.inner_text('#riCounts')!r})")
        pg.click("#refuelImportModal button:has-text('Atrás')")
        pg.click("#refuelImportModal button:has-text('Cancelar')")

        # ── clean up for the next theme: delete what this one added (the import is undone through the API by its batch: here, by hand)
        vin = pg.evaluate("currentVin")
        for item in ctx.request.get(f"{BASE}/api/vehicles/{vin}/refuels?limit=500").json():
            if not item["detected"] and (item["station"] in ("Prueba E2E", "Gasolinera Norte", "Gasolinera Sur") or item["liters"] == 15):
                ctx.request.delete(f"{BASE}/api/refuels/{item['id']}", headers={"Origin": BASE})
        pg.evaluate("loadRefuels()")
        pg.wait_for_timeout(800)
        ok(len(rows(pg)) == before, f"{theme}: back to where it was ({len(rows(pg))} rows)")
        ok(not errs, f"{theme}: no JS errors {errs}")
        ctx.close()
    browser.close()
print("FAILURES: " + ("; ".join(fails) if fails else "none"))
sys.exit(1 if fails else 0)
