"""Shared catalogue: propose a part from the car sheet, approve it in the console and see it suggested when adding another. See tools/e2e/README.md."""
import os
import sys
import tempfile
import time
from playwright.sync_api import sync_playwright

BASE = os.environ.get("EVBRO_URL", "http://127.0.0.1:8099")
CONSOLE = os.environ.get("EVBRO_ADMIN_URL", "http://127.0.0.1:8098")
USER = os.environ.get("EVBRO_USER", "juan")
PASSWORD = os.environ.get("EVBRO_PASSWORD", "una-contraseña-larga-1")
ADMIN, ADMIN_PW = "admin", "contraseña-del-admin-1"
OUT = sys.argv[1] if len(sys.argv) > 1 else tempfile.mkdtemp(prefix="evbro-e2e-")
fails = []


def ok(c, m):
    print(("  ✔ " if c else "  ✘ ") + m)
    if not c:
        fails.append(m)


with sync_playwright() as p:
    b = p.chromium.launch()
    ctx = b.new_context(viewport={"width": 390, "height": 844}, is_mobile=True, has_touch=True, locale="es-ES", bypass_csp=True)
    ctx.request.post(f"{BASE}/api/auth/login", headers={"Origin": BASE}, data={"username": USER, "password": PASSWORD})
    pg = ctx.new_page()
    errors = []
    pg.on("pageerror", lambda e: errors.append(str(e)))
    pg.goto(BASE)
    pg.wait_for_load_state("networkidle")
    pg.wait_for_timeout(800)
    vin = pg.evaluate("currentVin")
    txt = lambda sel: pg.inner_text(sel)

    # the car needs a model; a part with brand and reference is what gets proposed
    ctx.request.put(f"{BASE}/api/vehicles/{vin}", headers={"Origin": BASE}, data={"model": "S900 PHEV"})
    part = ctx.request.post(f"{BASE}/api/vehicles/{vin}/parts", headers={"Origin": BASE},
                            data={"category": "brake_pads", "position": "front", "name": "Pastillas delanteras", "brand": "Brembo", "reference": "P 85 020", "spec": "Delanteras"}).json()
    pg.evaluate("openVehicleSettings(); setVehiclePane('parts')")
    pg.wait_for_timeout(1200)
    pg.evaluate(f"openPartModal({part['id']})")
    pg.wait_for_timeout(600)
    ok(pg.is_visible("#partPropose"), "a part with brand and part number offers «Proponer al catálogo»")
    pg.click("#partPropose")
    try:
        pg.wait_for_function("document.getElementById('partsGroups').textContent.includes('Propuesta enviada')", timeout=6000)
    except Exception:
        pass
    raw = lambda sel: pg.evaluate(f"document.querySelector('{sel}').textContent")      # innerText applies the CSS uppercase of the tag
    ok("Propuesta enviada" in raw("#partsGroups"), "the part is marked as proposed")

    # the administrator approves it in the console
    admin = b.new_context()
    r = admin.request.post(f"{CONSOLE}/api/auth/login", headers={"Origin": CONSOLE}, data={"username": ADMIN, "password": ADMIN_PW})
    ok(r.ok, f"the admin gets into the console ({r.status})")
    queue = admin.request.get(f"{CONSOLE}/api/admin/catalog").json()
    mine = [q for q in queue if q.get("reference") == "P 85 020" and q["status"] == "pending"]
    ok(len(mine) == 1 and mine[0]["proposed_by"] == USER, "the proposal is in the queue with who proposed it")
    ok(admin.request.post(f"{CONSOLE}/api/admin/catalog/{mine[0]['id']}/approve", headers={"Origin": CONSOLE}).ok, "it is approved")
    cpg = admin.new_page()
    cpg.goto(CONSOLE)
    cpg.wait_for_timeout(800)
    cpg.click("text=Catálogo")
    cpg.select_option("#catStatus", "approved")
    cpg.wait_for_timeout(800)
    ok("Brembo" in cpg.inner_text("#tblCatalog") and "Aprobada" in cpg.inner_text("#tblCatalog"), "the console lists it as approved")
    cpg.screenshot(path=f"{OUT}/catalog-console.png", full_page=True)

    pg.evaluate("loadParts()")
    try:
        pg.wait_for_function("document.getElementById('partsGroups').textContent.includes('En el catálogo')", timeout=6000)
    except Exception:
        pass
    ok("En el catálogo" in raw("#partsGroups"), "in the sheet it becomes «En el catálogo»")
    pg.evaluate("openPartModal(null, 'brake_pads')")
    pg.wait_for_timeout(1200)
    ok(pg.is_visible("#partCatalogSuggest") and "Brembo" in txt("#partCatalogSuggest"), "adding another identical part suggests the catalogue one")
    pg.click("#partCatalogSuggest .maint-chip >> nth=0")
    pg.wait_for_timeout(300)
    ok(pg.input_value('#partModalFields [data-f="brand"]') == "Brembo" and pg.input_value('#partModalFields [data-f="reference"]') == "P 85 020", "tapping it fills in brand and part number")
    pg.screenshot(path=f"{OUT}/catalog-suggest.png", full_page=True)
    pg.click("#partModal >> text=Cancelar")

    # proposing straight from «Añadir pieza»: one button saves and proposes
    stamp = str(int(time.time()))
    pg.evaluate("openPartModal(null, 'cabin_filter')")
    pg.wait_for_timeout(800)
    ok(pg.is_visible("#partPropose") and pg.inner_text("#partPropose") == "Guardar y proponer", "adding a part already offers «Guardar y proponer»")
    pg.fill('#partModalFields [data-f="brand"]', "Mann")
    pg.fill('#partModalFields [data-f="reference"]', f"CUK-{stamp}")
    pg.click("#partPropose")
    pg.wait_for_timeout(1500)
    queue = admin.request.get(f"{CONSOLE}/api/admin/catalog").json()
    fresh = [q for q in queue if q.get("reference") == f"CUK-{stamp}"]
    ok(len(fresh) == 1 and fresh[0]["status"] == "pending", "the new part is saved and its proposal is in the queue")
    added = [p_ for p_ in ctx.request.get(f"{BASE}/api/vehicles/{vin}/parts").json() if p_.get("reference") == f"CUK-{stamp}"]
    ok(len(added) == 1 and added[0]["catalog_id"] == (fresh[0]["id"] if fresh else None), "the saved part points at its proposal")

    # the admin adds a part from the console: one row of fields per car
    cpg.select_option("#catStatus", "approved")
    cpg.click("#btnCatalogAdd")
    cpg.wait_for_timeout(500)
    ok(cpg.is_visible("#catCategoryWrap") and cpg.locator("#catFits .fit-row").count() == 1, "«Añadir pieza» opens with the part type and one car row")
    cpg.select_option("#catCategory", "wiper_blades")
    ok(cpg.is_visible("#catPositionWrap"), "wiper blades ask for the position")
    cpg.select_option("#catPosition", "front")
    row = cpg.locator("#catFits .fit-row").nth(0)
    row.locator('[data-f="model"]').fill("S900 PHEV")
    row.locator('[data-f="years"]').fill("2024-2026")
    row.locator('[data-f="oem_reference"]').fill(f"EB-{stamp}")
    cpg.click("#btnFitAdd")
    row2 = cpg.locator("#catFits .fit-row").nth(1)
    ok(row2.locator('[data-f="make"]').input_value() == "Ebro" and row2.locator('[data-f="years"]').input_value() == "2024-2026", "a new car row copies make and years from the one above")
    row2.locator('[data-f="model"]').fill("S800")
    cpg.fill("#catBrand", "Bosch")
    cpg.fill("#catRef", f"AR-{stamp}")
    cpg.screenshot(path=f"{OUT}/catalog-console-add.png")
    cpg.click("#formCatalog button[type=submit]")
    cpg.wait_for_timeout(1200)
    ok(not cpg.is_visible("#dlgCatalog"), "the dialog closes after saving")
    approved = [q for q in admin.request.get(f"{CONSOLE}/api/admin/catalog?status=approved").json() if q.get("reference") == f"AR-{stamp}"]
    ok(len(approved) == 1 and [f["model"] for f in approved[0]["fitments"]] == ["S900 PHEV", "S800"] and approved[0]["fitments"][0]["oem_reference"] == f"EB-{stamp}"
       and approved[0]["position"] == "front", "it is in the catalogue, approved, with both cars and the maker's number")
    ok(cpg.locator("#tblCatalog .fit-line").count() >= 2, "the table shows each car on its own line")
    # editing it keeps every field of every car
    cpg.locator("#tblCatalog tr", has_text=f"AR-{stamp}").locator("text=Editar").click()
    cpg.wait_for_timeout(400)
    rows = cpg.locator("#catFits .fit-row")
    vals = [[rows.nth(i).locator(f'[data-f="{k}"]').input_value() for k in ("make", "model", "years", "oem_reference")] for i in range(rows.count())]
    ok(vals == [["Ebro", "S900 PHEV", "2024-2026", f"EB-{stamp}"], ["Ebro", "S800", "2024-2026", ""]], f"editing shows every field of every car {vals}")
    cpg.screenshot(path=f"{OUT}/catalog-console-edit.png")
    cpg.set_viewport_size({"width": 390, "height": 844})
    cpg.wait_for_timeout(300)
    over = cpg.evaluate("(() => { const d = document.getElementById('dlgCatalog'); return d.scrollWidth > d.clientWidth + 1; })()")
    ok(not over, "the dialog fits a phone without sideways scrolling")
    cpg.screenshot(path=f"{OUT}/catalog-console-edit-mobile.png")
    cpg.click("#btnCatCancel")

    # tidy up: the test parts go away (the approved entries stay: they are public knowledge now)
    ctx.request.delete(f"{BASE}/api/parts/{part['id']}", headers={"Origin": BASE})
    for p_ in added:
        ctx.request.delete(f"{BASE}/api/parts/{p_['id']}", headers={"Origin": BASE})
    for q in fresh:
        admin.request.post(f"{CONSOLE}/api/admin/catalog/{q['id']}/reject", headers={"Origin": CONSOLE}, data={"reason": "e2e"})
    ok(not errors, f"no JS errors {errors}")
    admin.close()
    b.close()
print("FAILURES:", fails if fails else "none")
sys.exit(1 if fails else 0)
