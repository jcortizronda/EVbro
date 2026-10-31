"""Mapa de la portada: ampliar al tocar, centrar en el coche e «Ir a» (navegación). Ver tools/e2e/README.md."""
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
        # capturar window.open para comprobar la URL de navegación
        pg.evaluate("window.__opened = []")
        pg.evaluate("() => { window.open = (u, t, f) => { window.__opened.push(u); return null; }; }")  # (una función: Playwright no la ejecuta)
        tag = theme
        ok(pg.is_disabled("#btnMapGo") and pg.is_disabled("#btnMapCenter"), f"{tag}: sin posición, «Centrar» e «Ir a» están desactivados")
        pg.evaluate("t => { currentVin='LSJA8888888888888'; applyTelemetry(t); }", SAMPLE)
        pg.wait_for_timeout(600)
        ok(not pg.is_disabled("#btnMapGo") and not pg.is_disabled("#btnMapCenter"), f"{tag}: con posición se activan")
        dragging = pg.evaluate("liveMap.dragging.enabled()")
        ok(dragging is False, f"{tag}: plegado, el mapa no arrastra (la página se puede desplazar)")
        pg.screenshot(path=f"{OUT}/map-collapsed-{theme}.png")

        # tocar el mapa → ampliado (primero se baja hasta él: queda por debajo del pliegue)
        def tap_map():
            pg.locator("#liveMap").scroll_into_view_if_needed()
            pg.wait_for_timeout(200)
            box = pg.locator("#liveMap").bounding_box()
            pg.mouse.click(box["x"] + box["width"] / 2, box["y"] + 40)

        tap_map()
        pg.wait_for_timeout(500)
        r = pg.evaluate("(() => { const c=document.getElementById('liveMapCard').getBoundingClientRect(); return [Math.round(c.left),Math.round(c.top),Math.round(c.width),Math.round(c.height),innerWidth,innerHeight]; })()")
        ok(pg.evaluate("mapExpanded") and r[0] == 0 and r[1] == 0 and r[2] == r[4] and r[3] == r[5], f"{tag}: un toque amplía el mapa a pantalla completa {r}")
        ok(pg.evaluate("liveMap.dragging.enabled()") is True, f"{tag}: ampliado, el mapa se puede mover")
        ok(pg.is_visible("#btnMapClose"), f"{tag}: aparece el botón de cerrar")
        sz = pg.evaluate("[liveMap.getSize().x, liveMap.getSize().y]")
        ok(sz[0] == 390 and sz[1] > 500, f"{tag}: Leaflet recalcula el tamaño ({sz})")
        pg.screenshot(path=f"{OUT}/map-expanded-{theme}.png")

        # mover el mapa → «Centrar» se resalta y devuelve al coche
        pg.evaluate("liveMap.panBy([160, 140], {animate: false}); mapUserMoved = true; updateMapButtons();")
        pg.wait_for_timeout(300)
        ok(pg.evaluate("document.getElementById('btnMapCenter').classList.contains('is-off-center')"), f"{tag}: «Centrar» se resalta cuando el coche se sale del centro")
        # una lectura nueva NO debe recentrar si el usuario movió el mapa
        c0 = pg.evaluate("[liveMap.getCenter().lat, liveMap.getCenter().lng]")
        t2 = dict(SAMPLE); t2["latitude"] = SAMPLE["latitude"] + 0.001
        pg.evaluate("t => applyTelemetry(t)", t2)
        pg.wait_for_timeout(300)
        c1 = pg.evaluate("[liveMap.getCenter().lat, liveMap.getCenter().lng]")
        ok(c0 == c1, f"{tag}: una lectura nueva no arrastra el mapa si lo has movido")
        pg.click("#btnMapCenter")
        pg.wait_for_timeout(700)
        d = pg.evaluate("(() => { const p=liveMap.latLngToContainerPoint(vehicleLatLng), c=liveMap.getSize().divideBy(2); return Math.round(p.distanceTo(c)); })()")
        ok(d <= 3, f"{tag}: «Centrar» deja el coche en el centro ({d} px)")
        ok(not pg.evaluate("document.getElementById('btnMapCenter').classList.contains('is-off-center')"), f"{tag}: «Centrar» deja de resaltarse")

        # Ir a
        pg.click("#btnMapGo")
        opened = pg.evaluate("window.__opened")
        ok(len(opened) == 1 and "google.com/maps/dir/?api=1&destination=" in opened[0] and f"{SAMPLE['latitude'] + 0.001}" in opened[0],
           f"{tag}: «Ir a» abre la navegación hacia las coordenadas del coche {opened}")

        # cerrar (botón) y volver a abrir y cerrar con «atrás»
        pg.click("#btnMapClose")
        pg.wait_for_timeout(400)
        ok(not pg.evaluate("mapExpanded") and not pg.evaluate("document.body.classList.contains('map-open')"), f"{tag}: ✕ cierra el mapa ampliado")
        tap_map()
        pg.wait_for_timeout(400)
        pg.go_back()
        pg.wait_for_timeout(500)
        ok(not pg.evaluate("mapExpanded") and pg.url.rstrip("/") == BASE.rstrip("/"), f"{tag}: el botón «atrás» del móvil cierra el mapa sin salir de la página")
        over = pg.evaluate("document.documentElement.scrollWidth - document.documentElement.clientWidth")
        ok(over <= 0, f"{tag}: sin desbordamiento horizontal ({over} px)")
        ok(not errs, f"{tag}: sin errores JS {errs[:2]}")
        ctx.close()
    b.close()
print("FALLOS:", fails if fails else "ninguno")
sys.exit(1 if fails else 0)
