"""Energía and Confort made of widgets, and the icon's shortcuts (manifest «shortcuts», `/?go=…`). See tools/e2e/README.md."""
import os
import sys
import tempfile

from playwright.sync_api import sync_playwright

BASE = os.environ.get("EVBRO_URL", "http://127.0.0.1:8099")
USER = os.environ.get("EVBRO_USER", "juan")
PASSWORD = os.environ.get("EVBRO_PASSWORD", "una-contraseña-larga-1")
OUT = sys.argv[1] if len(sys.argv) > 1 else tempfile.mkdtemp(prefix="evbro-e2e-")
os.makedirs(OUT, exist_ok=True)
fails = []


def ok(c, m):
    print(("  ✔ " if c else "  ✘ ") + m)
    if not c:
        fails.append(m)


def dialog_open(pg):
    return pg.evaluate("document.getElementById('appDialog').classList.contains('open')")


def order(pg, box):
    return pg.eval_on_selector_all(f"#{box} > .home-widget:not([hidden])", "els => els.map(e => e.dataset.widget)")


with sync_playwright() as p:
    b = p.chromium.launch()
    ctx = b.new_context(viewport={"width": 390, "height": 844}, is_mobile=True, has_touch=True, locale="es-ES", service_workers="block")
    ctx.request.post(f"{BASE}/api/auth/login", headers={"Origin": BASE}, data={"username": USER, "password": PASSWORD})
    for page in ("charges", "climate"):
        ctx.request.delete(f"{BASE}/api/account/home-layout?page={page}", headers={"Origin": BASE})
    pg = ctx.new_page()
    errors = []
    pg.on("pageerror", lambda e: errors.append(str(e)))
    pg.goto(BASE)
    pg.wait_for_load_state("networkidle")
    pg.wait_for_timeout(800)

    # ── Energía ──
    pg.evaluate("switchTab('charges')")
    pg.wait_for_timeout(600)
    ok(order(pg, "energyWidgets") == ["energy", "kpis", "control", "schedule", "history"], f"Energía has its usual blocks in order {order(pg, 'energyWidgets')}")
    ok(pg.is_visible("#energyCustomize") and not pg.is_visible("#energyEditBar"), "«Personalizar energía» is offered")
    parents = pg.evaluate("['energyCustomize', 'comfortCustomize'].map(i => document.getElementById(i).parentElement.className)")
    ok(parents == ["native-app-container", "native-app-container"], f"the «Personalizar» buttons sit after the widgets, not inside them {parents}")
    pg.click("#energyCustomize")
    pg.wait_for_timeout(300)
    ok(pg.is_visible("#energyEditBar") and pg.locator("#energyWidgets .hw-tools").count() == 5, "editing shows a bar on every block")
    pg.click('#energyWidgets [data-widget="history"] .hw-tool[data-on-click*="-1"]')
    pg.wait_for_timeout(200)
    ok(order(pg, "energyWidgets")[3] == "history", "«Subir» moves the history up")
    pg.click('#energyWidgets [data-widget="kpis"] .hw-remove')
    pg.wait_for_timeout(200)
    ok("kpis" not in order(pg, "energyWidgets") and pg.query_selector("#energyPowerKw") is not None, "removing hides the figures (their fields stay in the page)")
    pg.click("#energyEditBar >> text=Añadir widget")
    pg.wait_for_timeout(300)
    items = pg.eval_on_selector_all("#homeAddList .hw-add-item strong", "els => els.map(e => e.textContent.trim())")
    ok(items[:1] == ["Cifras de energía"] and "Última carga" in items and "Gasto del mes" in items and "Autonomía" not in items,
       f"Energía offers its own blocks and a few cards, not Inicio's {items}")
    pg.click("#homeAddList >> text=Última carga")
    pg.wait_for_timeout(1200)
    ok(order(pg, "energyWidgets")[-1] == "last_charge", "the last charge card is added at the end")
    ok("kWh" in pg.text_content('#energyWidgets [data-widget="last_charge"]'), "and shows its figures")
    # drag the last charge to the top
    handle = pg.locator('#energyWidgets [data-widget="last_charge"] .hw-drag')
    handle.scroll_into_view_if_needed()
    hb = handle.bounding_box()
    pg.mouse.move(hb["x"] + hb["width"] / 2, hb["y"] + hb["height"] / 2)
    pg.mouse.down()
    for y in range(int(hb["y"]), 10, -40):
        pg.mouse.move(hb["x"] + 10, y)
        pg.wait_for_timeout(20)
    for _ in range(150):                                   # held at the top edge, the page scrolls up by itself
        pg.mouse.move(hb["x"] + 10, 10)
        pg.wait_for_timeout(30)
        if pg.locator('#energyWidgets [data-widget="energy"]').bounding_box()["y"] > 40:
            break
    first = pg.locator('#energyWidgets [data-widget="energy"]').bounding_box()
    pg.mouse.move(hb["x"] + 10, first["y"] + 5)
    pg.wait_for_timeout(100)
    pg.mouse.up()
    pg.wait_for_timeout(700)
    ok(order(pg, "energyWidgets")[0] == "last_charge", f"dragging puts the last charge first {order(pg, 'energyWidgets')}")
    pg.screenshot(path=f"{OUT}/energia-editando.png")
    pg.click("#energyEditBar >> text=Hecho")
    pg.wait_for_timeout(600)
    saved = ctx.request.get(f"{BASE}/api/account/home-layout").json()["layout"]
    ok(saved and saved.get("pages", {}).get("charges") == order(pg, "energyWidgets"), f"the server keeps Energía's order {saved}")
    over = pg.evaluate("document.documentElement.scrollWidth > document.documentElement.clientWidth + 1")
    ok(not over, "at 390 px Energía does not overflow sideways")
    pg.screenshot(path=f"{OUT}/energia.png", full_page=True)

    # ── Confort ──
    pg.evaluate("switchTab('climate')")
    pg.wait_for_timeout(500)
    ok(not pg.is_visible("#energyEditBar"), "leaving Energía closes its edit bar")
    ok(order(pg, "comfortWidgets") == ["temperature", "defrost", "seats", "schedule"], f"Confort has its usual blocks {order(pg, 'comfortWidgets')}")
    pg.click("#comfortCustomize")
    pg.wait_for_timeout(300)
    pg.click('#comfortWidgets [data-widget="schedule"] .hw-tool[data-on-click*="-1"]')
    pg.click('#comfortWidgets [data-widget="defrost"] .hw-remove')
    pg.wait_for_timeout(200)
    ok(order(pg, "comfortWidgets") == ["temperature", "schedule", "seats"], f"Confort reorders and removes {order(pg, 'comfortWidgets')}")
    pg.screenshot(path=f"{OUT}/confort-editando.png")
    pg.click("#comfortEditBar >> text=Hecho")
    pg.wait_for_timeout(600)

    # another device sees both pages the same
    other = b.new_context(viewport={"width": 1440, "height": 900}, locale="es-ES", service_workers="block")
    other.request.post(f"{BASE}/api/auth/login", headers={"Origin": BASE}, data={"username": USER, "password": PASSWORD})
    o = other.new_page()
    o.goto(BASE)
    o.wait_for_load_state("networkidle")
    o.evaluate("switchTab('charges')")
    o.wait_for_timeout(900)
    ok(order(o, "energyWidgets") == order(pg, "energyWidgets"), "another device shows the same Energía")
    hist = o.locator('#energyWidgets [data-widget="history"]').bounding_box()
    ctl = o.locator('#energyWidgets [data-widget="control"]').bounding_box()
    ok(hist and ctl and hist["x"] > ctl["x"] + 100, "on a computer the history sits on its own column on the right")
    o.screenshot(path=f"{OUT}/energia-escritorio.png")
    o.evaluate("switchTab('climate')")
    o.wait_for_timeout(600)
    ok(order(o, "comfortWidgets") == ["temperature", "schedule", "seats"], "and the same Confort")
    o.screenshot(path=f"{OUT}/confort-escritorio.png")
    other.close()

    # Restablecer, page by page: Inicio's layout is not touched
    ctx.request.put(f"{BASE}/api/account/home-layout", headers={"Origin": BASE}, data={"widgets": ["map", "range"]})
    pg.evaluate("switchTab('charges')")
    pg.wait_for_timeout(300)
    pg.click("#energyCustomize")
    pg.click("#energyEditBar >> text=Restablecer")
    pg.click("#appDialogOk")
    pg.wait_for_timeout(700)
    ok(order(pg, "energyWidgets") == ["energy", "kpis", "control", "schedule", "history"], "«Restablecer» brings the usual Energía back")
    saved = ctx.request.get(f"{BASE}/api/account/home-layout").json()["layout"]
    ok(saved == {"widgets": ["map", "range"], "pages": {"climate": ["temperature", "schedule", "seats"]}}, f"only Energía is reset on the server {saved}")
    pg.click("#energyEditBar >> text=Hecho")
    ctx.request.delete(f"{BASE}/api/account/home-layout", headers={"Origin": BASE})
    ctx.request.delete(f"{BASE}/api/account/home-layout?page=climate", headers={"Origin": BASE})

    # ── shortcuts ──
    manifest = ctx.request.get(f"{BASE}/manifest.json").json()
    urls = [s["url"] for s in manifest.get("shortcuts", [])]
    ok(urls == ["/?go=lock", "/?go=climate_on", "/?go=charges", "/?go=drives"], f"the manifest offers the four shortcuts {urls}")
    ok(all(ctx.request.get(BASE + s["icons"][0]["src"]).ok for s in manifest["shortcuts"]), "and their icons are served")
    sent = []
    pg.route("**/api/vehicles/*/commands**", lambda r: (sent.append(r.request.url), r.fulfill(status=200, content_type="application/json", body='{"status":"ok","message":"ok"}')))
    pg.route("**/api/commands/**", lambda r: (sent.append(r.request.url), r.fulfill(status=200, content_type="application/json", body='{"status":"ok","message":"ok"}')) if r.request.method == "POST" else r.continue_())
    pg.goto(f"{BASE}/?go=charges")
    pg.wait_for_load_state("networkidle")
    pg.wait_for_timeout(900)
    ok(pg.is_visible("#tabCharges") and "go=" not in pg.url, f"«Energía» opens Energía and the address is cleaned ({pg.url})")
    pg.goto(f"{BASE}/?go=drives")
    pg.wait_for_load_state("networkidle")
    pg.wait_for_timeout(900)
    ok(pg.is_visible("#tabDrives"), "«Viajes» opens Viajes")
    pg.goto(f"{BASE}/?go=lock")
    pg.wait_for_load_state("networkidle")
    pg.wait_for_timeout(900)
    dlg = dialog_open(pg) and pg.text_content("#appDialogMessage")
    ok(bool(dlg) and "¿Cerrar «" in dlg and pg.is_visible("#tabOverview"), f"«Cerrar el coche» opens Inicio and asks first ({dlg and dlg.strip()[:60]})")
    ok(not sent, "nothing is sent before confirming")
    pg.screenshot(path=f"{OUT}/atajo-cerrar.png")
    pg.click("#appDialogCancel")
    pg.wait_for_timeout(400)
    ok(not sent, "cancelling sends nothing")
    pg.goto(f"{BASE}/?go=climate_on")
    pg.wait_for_load_state("networkidle")
    pg.wait_for_timeout(900)
    ok(pg.is_visible("#tabClimate") and dialog_open(pg) and "clima" in (pg.text_content("#appDialogMessage") or ""), "«Encender el clima» opens Confort and asks first")
    pg.click("#appDialogOk")
    pg.wait_for_timeout(1500)
    ok(len(sent) == 1, f"confirming sends the order once ({sent})")
    pg.reload()
    pg.wait_for_load_state("networkidle")
    pg.wait_for_timeout(900)
    ok(not dialog_open(pg), "reloading does not repeat the shortcut")

    ok(not errors, f"no JS errors {errors[:2]}")
    b.close()
print("FAILURES:", fails if fails else "none")
sys.exit(1 if fails else 0)
