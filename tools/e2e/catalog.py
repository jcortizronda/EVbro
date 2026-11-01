"""Shared catalogue: propose a part from the car sheet, approve it in the console and see it suggested when adding another. See tools/e2e/README.md."""
import os
import sys
import tempfile
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

    # tidy up: the test part goes away (the approved entry stays: it is public knowledge now)
    ctx.request.delete(f"{BASE}/api/parts/{part['id']}", headers={"Origin": BASE})
    ok(not errors, f"no JS errors {errors}")
    admin.close()
    b.close()
print("FAILURES:", fails if fails else "none")
sys.exit(1 if fails else 0)
