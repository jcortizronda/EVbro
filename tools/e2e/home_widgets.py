"""Inicio made of widgets: hide, move (arrows and drag), add the new ones, keep the layout on the server and reset. See tools/e2e/README.md."""
import os
import sys
import tempfile
from playwright.sync_api import sync_playwright

BASE = os.environ.get("EVBRO_URL", "http://127.0.0.1:8099")
USER = os.environ.get("EVBRO_USER", "juan")
PASSWORD = os.environ.get("EVBRO_PASSWORD", "una-contraseña-larga-1")
OUT = sys.argv[1] if len(sys.argv) > 1 else tempfile.mkdtemp(prefix="evbro-e2e-")
NEW = ["service", "last_trip", "last_charge", "month_cost", "charge_plan", "climate", "consumption"]
fails = []


def ok(c, m):
    print(("  ✔ " if c else "  ✘ ") + m)
    if not c:
        fails.append(m)


def shown(pg):
    return pg.evaluate("[...document.querySelectorAll('#homeWidgets > .home-widget')].filter((e) => !e.hidden).map((e) => e.dataset.widget)")


with sync_playwright() as p:
    b = p.chromium.launch()
    ctx = b.new_context(viewport={"width": 390, "height": 844}, is_mobile=True, has_touch=True, locale="es-ES", bypass_csp=True)
    ctx.request.post(f"{BASE}/api/auth/login", headers={"Origin": BASE}, data={"username": USER, "password": PASSWORD})
    ctx.request.delete(f"{BASE}/api/account/home-layout", headers={"Origin": BASE})          # start from the default Inicio
    pg = ctx.new_page()
    errors = []
    pg.on("pageerror", lambda e: errors.append(str(e)))
    pg.add_init_script("try { localStorage.removeItem('evbro_home_layout'); } catch (e) {}")
    pg.goto(BASE)
    pg.wait_for_load_state("networkidle")
    pg.wait_for_timeout(1000)
    default = shown(pg)
    ok(default == ["range", "car", "actions", "kpis", "charging", "map", "tpms", "sensors"], f"the default Inicio has the usual blocks in order {default}")
    ok(pg.is_visible("#homeCustomize") and not pg.is_visible("#homeEditBar"), "«Personalizar inicio» is offered and the edit bar is hidden")

    # edit mode: every widget gets its bar, and its content does not react
    pg.click("#homeCustomize")
    pg.wait_for_timeout(300)
    ok(pg.is_visible("#homeEditBar") and pg.locator("#homeWidgets .hw-tools").count() == len(default), "editing shows a bar on every widget")
    ok(pg.evaluate("getComputedStyle(document.querySelector('#btnLockAction')).pointerEvents") == "none", "while editing, the order buttons cannot be pressed")
    ok(pg.locator('[data-widget="range"] .hw-tools [data-on-click*="-1"]').is_disabled(), "the first widget cannot go up")

    # arrows
    pg.click('[data-widget="map"] .hw-tools [data-on-click*="-1"]')
    pg.wait_for_timeout(200)
    ok(shown(pg).index("map") == 4, "«Subir» moves the map one place up")
    pg.click('[data-widget="tpms"] .hw-remove')
    pg.wait_for_timeout(200)
    ok("tpms" not in shown(pg) and pg.locator("#tpmsFL").count() == 1, "removing hides the tyres widget (its fields stay in the page)")

    # add the new widgets
    for key in NEW:
        pg.click("#homeEditBar >> text=Añadir widget")
        pg.wait_for_timeout(200)
        pg.click(f"#homeAddList [data-on-click=\"HomeWidgets.add('{key}')\"]")
        pg.wait_for_timeout(250)
    pg.click("#homeEditBar >> text=Añadir widget")
    pg.wait_for_timeout(200)
    ok(pg.locator("#homeAddList .hw-add-item").count() == 1 and "Presión de neumáticos" in pg.inner_text("#homeAddList"), "only the removed widget is left to add")
    pg.click("#homeAddModal >> text=Cerrar")
    ok(shown(pg)[-len(NEW):] == NEW, "the new widgets are added at the end, in order")

    # drag: the last trip to the top, with the mouse on its handle
    def drag(key, target):
        h = pg.locator(f'[data-widget="{key}"] .hw-drag')
        h.scroll_into_view_if_needed()
        box = h.bounding_box()
        pg.mouse.move(box["x"] + box["width"] / 2, box["y"] + box["height"] / 2)
        pg.mouse.down()
        for _ in range(120):                        # hold it near the top: the page scrolls up by itself
            pg.mouse.move(box["x"] + box["width"] / 2, 3)
            pg.wait_for_timeout(25)
            if pg.evaluate("window.scrollY") == 0:
                break
        tbox = pg.locator(f'[data-widget="{target}"]').bounding_box()
        pg.mouse.move(box["x"] + box["width"] / 2, tbox["y"] + 5, steps=8)
        pg.wait_for_timeout(150)
        pg.mouse.up()
        pg.wait_for_timeout(300)
    drag("last_trip", "range")
    ok(shown(pg)[0] == "last_trip", f"dragging puts the last trip first {shown(pg)[:3]}")
    ok(pg.locator(".hw-placeholder").count() == 0 and pg.locator(".hw-dragging").count() == 0, "nothing is left behind after dropping")
    pg.screenshot(path=f"{OUT}/home-editing.png", full_page=True)

    pg.click("#homeEditBar >> text=Hecho")
    pg.wait_for_timeout(1500)
    ok(pg.locator("#homeWidgets .hw-tools").count() == 0 and pg.is_visible("#homeCustomize"), "«Hecho» leaves edit mode")
    bodies = {k: pg.evaluate(f"document.querySelector('[data-widget=\"{k}\"] [data-hw-body]').textContent") for k in NEW}   # innerText applies CSS uppercase
    bad = {k: v for k, v in bodies.items() if "Cargando" in v or "No se pudo" in v or not v.strip()}
    ok(not bad, f"every new widget shows its data {bad or ''}")
    ok("km" in bodies["last_trip"] and "kWh" in bodies["last_charge"] and "€" in bodies["month_cost"], "trip, charge and spending show their figures")
    ok("Revisión" in bodies["service"] or "ITV" in bodies["service"], f"maintenance shows the next service {bodies['service'][:60]!r}")
    ok("Dentro" in bodies["climate"] and "Fuera" in bodies["climate"], "climate shows the temperatures")
    pg.screenshot(path=f"{OUT}/home-widgets.png", full_page=True)

    # tapping a card opens its tab
    pg.click('[data-widget="last_trip"] .hw-head')
    pg.wait_for_timeout(500)
    ok(pg.evaluate("window.currentTabName") == "drives", "the last trip card opens Viajes")
    pg.evaluate("switchTab('overview')")
    pg.wait_for_timeout(500)

    # the layout lives on the server: another device (no local copy) sees the same
    saved = ctx.request.get(f"{BASE}/api/account/home-layout").json()["layout"]["widgets"]
    ok(saved == shown(pg), "the server keeps the same order")
    other = b.new_context(viewport={"width": 1280, "height": 900}, locale="es-ES", bypass_csp=True)
    other.request.post(f"{BASE}/api/auth/login", headers={"Origin": BASE}, data={"username": USER, "password": PASSWORD})
    op = other.new_page()
    op.on("pageerror", lambda e: errors.append(str(e)))
    op.goto(BASE)
    op.wait_for_load_state("networkidle")
    op.wait_for_timeout(1500)
    ok(shown(op) == saved, "another device shows the same Inicio")
    mx = op.locator('[data-widget="map"]').bounding_box()
    lx = op.locator('[data-widget="last_trip"]').bounding_box()
    ok(mx and lx and mx["x"] > lx["x"] + lx["width"] - 1, "on a computer the map sits on its own column on the right")
    op.screenshot(path=f"{OUT}/home-desktop.png", full_page=True)
    other.close()

    # phone width: nothing overflows
    pg.set_viewport_size({"width": 360, "height": 780})
    pg.wait_for_timeout(300)
    pg.click("#homeCustomize")
    pg.wait_for_timeout(300)
    over = pg.evaluate("document.documentElement.scrollWidth > document.documentElement.clientWidth + 1")
    ok(not over, "at 360 px nothing overflows sideways (also while editing)")
    pg.screenshot(path=f"{OUT}/home-editing-360.png")

    # reset
    pg.click("#homeEditBar >> text=Restablecer")
    pg.wait_for_timeout(300)
    pg.click("#appDialogOk")
    pg.wait_for_timeout(800)
    ok(shown(pg) == default, "«Restablecer» brings the usual Inicio back")
    ok(ctx.request.get(f"{BASE}/api/account/home-layout").json()["layout"] is None, "and the server forgets the custom layout")
    pg.click("#homeEditBar >> text=Hecho")
    ok(not errors, f"no JS errors {errors}")
    b.close()
print("FAILURES:", fails if fails else "none")
sys.exit(1 if fails else 0)
