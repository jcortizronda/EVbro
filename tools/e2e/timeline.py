"""Timeline and search (Más → Cronología): the list mixes trips, charges, workshop visits and more of the demo car; the chips filter;
the search box finds the workshop of an intervention and tapping a result leads to Mantenimiento.
    tools/e2e/demo_server.sh start && python tools/e2e/timeline.py [screenshot_dir]
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
        ok(len(kinds) >= 5 and {"drive", "charge", "maintenance"} <= set(kinds), f"the timeline mixes several kinds ({sorted(set(kinds))})")
        ok(pg.locator("#tlList .hist-month").count() >= 1, "there are month separators")
        ok(pg.locator("#tlChips .ex-chip").count() == 8 and pg.locator("#tlChips .ex-chip.is-active").inner_text() == "Todo", "eight kind chips, «Todo» active")
        pg.screenshot(path=f"{OUT}/cronologia-{theme}.png", full_page=True)
        pg.click("#tlChips .ex-chip[data-kind='maintenance']"); pg.wait_for_timeout(800)
        only = pg.eval_on_selector_all("#tlList .tl-row", "els => els.map(e => e.className)")
        ok(only and all("tl-kind-maintenance" in c for c in only), f"the «Taller» chip leaves only interventions ({len(only)})")
        pg.click("#tlChips .ex-chip[data-kind='']"); pg.wait_for_timeout(600)
        pg.fill("#tlSearch", "neumáticos"); pg.wait_for_timeout(900)
        hits = pg.eval_on_selector_all("#tlList .tl-row .tl-main strong", "els => els.map(e => e.textContent)")
        ok(any("neum" in h.lower() for h in hits) and "Resultados de" in pg.text_content("#tlTitle"), f"the search finds the tyre workshop ({hits})")
        pg.screenshot(path=f"{OUT}/busqueda-{theme}.png", full_page=True)
        pg.click("#tlList .tl-row.tl-kind-maintenance >> nth=0"); pg.wait_for_timeout(600)
        ok(pg.evaluate("document.getElementById('tabMaintenance').classList.contains('active')"), "tapping a workshop result opens Mantenimiento")
        pg.evaluate("switchTab('timeline')"); pg.wait_for_timeout(500)
        pg.click("#tabTimeline .tl-clear"); pg.wait_for_timeout(900)
        ok(pg.input_value("#tlSearch") == "" and pg.text_content("#tlTitle").strip() == "Cronología" and pg.locator("#tlList .tl-row").count() >= 5, "clearing the search goes back to the timeline")
        ok(pg.evaluate("document.documentElement.scrollWidth <= window.innerWidth"), "no horizontal overflow")
        ok(not errs, f"no JS errors {errs}")
        ctx.close()
    b.close()
print("FAILURES:", fails or "none")
sys.exit(1 if fails else 0)
