"""Home tab map: expand on tap, centre on the car and «Ir a» (navigation). See tools/e2e/README.md."""
import json
import os
import sys
import tempfile
from playwright.sync_api import sync_playwright

BASE = os.environ.get("EVBRO_URL", "http://127.0.0.1:8099")
USER = os.environ.get("EVBRO_USER", "juan")
PASSWORD = os.environ.get("EVBRO_PASSWORD", "una-contraseña-larga-1")
OUT = sys.argv[1] if len(sys.argv) > 1 else tempfile.mkdtemp(prefix="evbro-e2e-")
SAMPLE = json.load(open(os.path.join(os.path.dirname(__file__), "fixtures", "telemetry_sample.json")))
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
        pg = ctx.new_page()
        errs = []
        pg.on("pageerror", lambda e: errs.append(str(e)))
        pg.goto(BASE)
        pg.wait_for_load_state("networkidle")
        pg.wait_for_timeout(800)
        # capture window.open to check the navigation URL
        pg.evaluate("window.__opened = []")
        pg.evaluate("() => { window.open = (u, t, f) => { window.__opened.push(u); return null; }; }")  # (a function: Playwright does not run it)
        tag = theme
        ok(pg.is_disabled("#btnMapGo") and pg.is_disabled("#btnMapCenter"), f"{tag}: without a position, «Centrar» and «Ir a» are disabled")
        pg.evaluate("t => { currentVin='LSJA8888888888888'; applyTelemetry(t); }", SAMPLE)
        pg.wait_for_timeout(600)
        ok(not pg.is_disabled("#btnMapGo") and not pg.is_disabled("#btnMapCenter"), f"{tag}: with a position they are enabled")
        dragging = pg.evaluate("liveMap.dragging.enabled()")
        ok(dragging is False, f"{tag}: collapsed, the map does not drag (the page can scroll)")
        pg.screenshot(path=f"{OUT}/map-collapsed-{theme}.png")

        # tap the map → expanded (scroll down to it first: it sits below the fold)
        def tap_map():
            pg.locator("#liveMap").scroll_into_view_if_needed()
            pg.wait_for_timeout(200)
            box = pg.locator("#liveMap").bounding_box()
            pg.mouse.click(box["x"] + box["width"] / 2, box["y"] + 40)

        tap_map()
        pg.wait_for_timeout(500)
        r = pg.evaluate("(() => { const c=document.getElementById('liveMapCard').getBoundingClientRect(); return [Math.round(c.left),Math.round(c.top),Math.round(c.width),Math.round(c.height),innerWidth,innerHeight]; })()")
        ok(pg.evaluate("mapExpanded") and r[0] == 0 and r[1] == 0 and r[2] == r[4] and r[3] == r[5], f"{tag}: a tap expands the map to full screen {r}")
        ok(pg.evaluate("liveMap.dragging.enabled()") is True, f"{tag}: expanded, the map can be moved")
        ok(pg.is_visible("#btnMapClose"), f"{tag}: the close button shows up")
        sz = pg.evaluate("[liveMap.getSize().x, liveMap.getSize().y]")
        ok(sz[0] == 390 and sz[1] > 500, f"{tag}: Leaflet recalculates the size ({sz})")
        pg.screenshot(path=f"{OUT}/map-expanded-{theme}.png")

        # move the map → «Centrar» is highlighted and goes back to the car
        pg.evaluate("liveMap.panBy([160, 140], {animate: false}); mapUserMoved = true; updateMapButtons();")
        pg.wait_for_timeout(300)
        ok(pg.evaluate("document.getElementById('btnMapCenter').classList.contains('is-off-center')"), f"{tag}: «Centrar» is highlighted when the car leaves the centre")
        # a new reading must NOT re-centre if the user moved the map
        c0 = pg.evaluate("[liveMap.getCenter().lat, liveMap.getCenter().lng]")
        t2 = dict(SAMPLE); t2["latitude"] = SAMPLE["latitude"] + 0.001
        pg.evaluate("t => applyTelemetry(t)", t2)
        pg.wait_for_timeout(300)
        c1 = pg.evaluate("[liveMap.getCenter().lat, liveMap.getCenter().lng]")
        ok(c0 == c1, f"{tag}: a new reading does not drag the map if you moved it")
        pg.click("#btnMapCenter")
        pg.wait_for_timeout(700)
        d = pg.evaluate("(() => { const p=liveMap.latLngToContainerPoint(vehicleLatLng), c=liveMap.getSize().divideBy(2); return Math.round(p.distanceTo(c)); })()")
        ok(d <= 3, f"{tag}: «Centrar» puts the car in the centre ({d} px)")
        ok(not pg.evaluate("document.getElementById('btnMapCenter').classList.contains('is-off-center')"), f"{tag}: «Centrar» is no longer highlighted")

        # Ir a
        pg.click("#btnMapGo")
        opened = pg.evaluate("window.__opened")
        ok(len(opened) == 1 and "google.com/maps/dir/?api=1&destination=" in opened[0] and f"{SAMPLE['latitude'] + 0.001}" in opened[0],
           f"{tag}: «Ir a» opens navigation to the car's coordinates {opened}")

        # close (button), then open again and close with «back»
        pg.click("#btnMapClose")
        pg.wait_for_timeout(400)
        ok(not pg.evaluate("mapExpanded") and not pg.evaluate("document.body.classList.contains('map-open')"), f"{tag}: ✕ closes the expanded map")
        tap_map()
        pg.wait_for_timeout(400)
        pg.go_back()
        pg.wait_for_timeout(500)
        ok(not pg.evaluate("mapExpanded") and pg.url.rstrip("/") == BASE.rstrip("/"), f"{tag}: the phone's «back» button closes the map without leaving the page")
        over = pg.evaluate("document.documentElement.scrollWidth - document.documentElement.clientWidth")
        ok(over <= 0, f"{tag}: no horizontal overflow ({over} px)")
        ok(not errs, f"{tag}: no JS errors {errs[:2]}")
        ctx.close()
    b.close()
print("FAILURES:", fails if fails else "none")
sys.exit(1 if fails else 0)
