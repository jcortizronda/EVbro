"""Cronología y búsqueda (Más → Cronología): la lista mezcla viajes, cargas, taller y demás del coche de la demo; los chips filtran;
el buscador encuentra el taller de una intervención y tocar un resultado lleva a Mantenimiento.
    tools/e2e/demo_server.sh start && python tools/e2e/timeline.py [carpeta_capturas]
"""
import os, sys, tempfile
from playwright.sync_api import sync_playwright

BASE = os.environ.get("EVBRO_URL", "http://127.0.0.1:8099")
OUT = sys.argv[1] if len(sys.argv) > 1 else tempfile.mkdtemp(prefix="evbro-e2e-")
USER, PASSWORD = "juan", "una-contraseña-larga-1"
fails = []


def ok(cond, msg):
    print(("  ✔ " if cond else "  ✘ ") + msg)
    if not cond:
        fails.append(msg)


with sync_playwright() as p:
    b = p.chromium.launch()
    for theme in ("dark", "light"):
        ctx = b.new_context(viewport={"width": 390, "height": 844}, is_mobile=True, has_touch=True, locale="es-ES", service_workers="block")
        ctx.request.post(f"{BASE}/api/auth/login", headers={"Origin": BASE}, data={"username": USER, "password": PASSWORD})
        ctx.add_init_script(f"localStorage.setItem('evbro_theme','{theme}')")
        pg = ctx.new_page(); errs = []
        pg.on("pageerror", lambda e: errs.append(str(e)))
        pg.goto(BASE); pg.wait_for_load_state("networkidle"); pg.wait_for_timeout(900)
        print("==", theme)
        pg.evaluate("switchTab('timeline')"); pg.wait_for_timeout(1200)
        kinds = pg.eval_on_selector_all("#tlList .tl-row", "els => els.map(e => [...e.classList].find(c => c.startsWith('tl-kind-')).slice(8))")
        ok(len(kinds) >= 5 and {"drive", "charge", "maintenance"} <= set(kinds), f"la cronología mezcla varios tipos ({sorted(set(kinds))})")
        ok(pg.locator("#tlList .hist-month").count() >= 1, "hay separadores de mes")
        ok(pg.locator("#tlChips .ex-chip").count() == 8 and pg.locator("#tlChips .ex-chip.is-active").inner_text() == "Todo", "ocho chips de tipo, «Todo» activo")
        pg.screenshot(path=f"{OUT}/cronologia-{theme}.png", full_page=True)
        pg.click("#tlChips .ex-chip[data-kind='maintenance']"); pg.wait_for_timeout(800)
        only = pg.eval_on_selector_all("#tlList .tl-row", "els => els.map(e => e.className)")
        ok(only and all("tl-kind-maintenance" in c for c in only), f"el chip «Taller» deja solo intervenciones ({len(only)})")
        pg.click("#tlChips .ex-chip[data-kind='']"); pg.wait_for_timeout(600)
        pg.fill("#tlSearch", "neumáticos"); pg.wait_for_timeout(900)
        hits = pg.eval_on_selector_all("#tlList .tl-row .tl-main strong", "els => els.map(e => e.textContent)")
        ok(any("neum" in h.lower() for h in hits) and "Resultados de" in pg.text_content("#tlTitle"), f"la búsqueda encuentra el taller de neumáticos ({hits})")
        pg.screenshot(path=f"{OUT}/busqueda-{theme}.png", full_page=True)
        pg.click("#tlList .tl-row.tl-kind-maintenance >> nth=0"); pg.wait_for_timeout(600)
        ok(pg.evaluate("document.getElementById('tabMaintenance').classList.contains('active')"), "tocar un resultado de taller abre Mantenimiento")
        pg.evaluate("switchTab('timeline')"); pg.wait_for_timeout(500)
        pg.click("#tabTimeline .tl-clear"); pg.wait_for_timeout(900)
        ok(pg.input_value("#tlSearch") == "" and pg.text_content("#tlTitle").strip() == "Cronología" and pg.locator("#tlList .tl-row").count() >= 5, "borrar la búsqueda vuelve a la cronología")
        ok(pg.evaluate("document.documentElement.scrollWidth <= window.innerWidth"), "sin desbordamiento horizontal")
        ok(not errs, f"sin errores JS {errs}")
        ctx.close()
    b.close()
print("FALLOS:", fails or "ninguno")
sys.exit(1 if fails else 0)
