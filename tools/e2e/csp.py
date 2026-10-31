"""Prueba de navegador (Playwright) contra un servidor de demostración: ver tools/e2e/README.md.

Recorre todas las pestañas (móvil y escritorio) y falla si el navegador registra alguna violación de la `Content-Security-Policy`
de la app (un recurso externo sin autorizar, un `eval`, etc.). Los demás scripts abren el navegador con `bypass_csp=True` porque
`wait_for_function` evalúa texto en la página; este es el único que comprueba la política de verdad.
"""
import os
import sys
from playwright.sync_api import sync_playwright

BASE = os.environ.get("EVBRO_URL", "http://127.0.0.1:8099")
USER = os.environ.get("EVBRO_USER", "juan")
PASSWORD = os.environ.get("EVBRO_PASSWORD", "una-contraseña-larga-1")
TABS = ["overview", "charges", "climate", "drives", "more", "maintenance", "geofences", "settings"]
SETTINGS = ["account", "polling", "notifs", "theme", "system"]
fails = []


def ok(c, m):
    print(("  ✔ " if c else "  ✘ ") + m)
    if not c:
        fails.append(m)


with sync_playwright() as p:
    b = p.chromium.launch()
    for name, touch, (w, h) in (("móvil 390", True, (390, 844)), ("escritorio 1280", False, (1280, 900))):
        violations, errors = [], []
        ctx = b.new_context(viewport={"width": w, "height": h}, is_mobile=touch, has_touch=touch, locale="es-ES")
        ctx.request.post(f"{BASE}/api/auth/login", headers={"Origin": BASE}, data={"username": USER, "password": PASSWORD})
        pg = ctx.new_page()
        pg.on("console", lambda m: violations.append(m.text) if "Content Security Policy" in m.text or "Refused to" in m.text else None)
        pg.on("pageerror", lambda e: errors.append(str(e)))
        resp = pg.goto(BASE)
        csp = resp.headers.get("content-security-policy", "")
        ok("frame-ancestors 'none'" in csp and "default-src 'self'" in csp, f"{name}: la página llega con su Content-Security-Policy")
        style_src = next((d for d in csp.split(";") if d.strip().startswith("style-src")), "")
        ok("unsafe-inline" not in style_src and "unsafe-inline" not in csp, f"{name}: la política no admite estilos ni scripts en línea")
        pg.wait_for_load_state("networkidle")
        pg.wait_for_timeout(800)
        for t in TABS:
            pg.evaluate(f"switchTab('{t}')")
            pg.wait_for_timeout(600)
            if t == "settings":
                for s in SETTINGS:
                    pg.evaluate(f"typeof switchSettingsSubTab === 'function' && switchSettingsSubTab('{s}')")
                    pg.wait_for_timeout(300)
            if t == "charges":
                for sub in ("history", "control"):
                    pg.evaluate(f"typeof switchChargesSubTab === 'function' && switchChargesSubTab('{sub}')")
                    pg.wait_for_timeout(300)
        # the live map (Leaflet + tiles) and the geofence modal (search box, map)
        pg.evaluate("switchTab('overview')"); pg.wait_for_timeout(400)
        pg.evaluate("typeof expandLiveMap === 'function' && expandLiveMap()"); pg.wait_for_timeout(600)
        pg.keyboard.press("Escape")
        pg.evaluate("switchTab('geofences')"); pg.wait_for_timeout(300)
        pg.evaluate("typeof openNewGeofenceModal === 'function' && openNewGeofenceModal()"); pg.wait_for_timeout(700)
        pg.keyboard.press("Escape")
        pg.wait_for_timeout(500)
        ok(not violations, f"{name}: sin violaciones de CSP {[v[:160] for v in violations[:3]]}")
        ok(not errors, f"{name}: sin errores de JavaScript {errors[:3]}")
        ctx.close()
    b.close()
print("FALLOS:", fails or "ninguno")
sys.exit(1 if fails else 0)
