"""Statistics (Más → Estadísticas): the cards are drawn with the demo data, the period changes and nothing overflows.
    tools/e2e/demo_server.sh start && python tools/e2e/stats.py [screenshot_dir]
"""
import os, sys, tempfile
from playwright.sync_api import sync_playwright

BASE = os.environ.get("EVBRO_URL", "http://127.0.0.1:8099")
OUT = sys.argv[1] if len(sys.argv) > 1 else tempfile.mkdtemp(prefix="evbro-e2e-")
fails = []


def ok(cond, msg):
    print(("  ✔ " if cond else "  ✘ ") + msg)
    if not cond:
        fails.append(msg)


with sync_playwright() as p:
    b = p.chromium.launch()
    for theme in ("dark", "light"):
        ctx = b.new_context(viewport={"width": 390, "height": 844}, is_mobile=True, has_touch=True, locale="es-ES", service_workers="block")
        ctx.request.post(f"{BASE}/api/auth/login", headers={"Origin": BASE}, data={"username": "juan", "password": "una-contraseña-larga-1"})
        ctx.add_init_script(f"localStorage.setItem('evbro_theme','{theme}')")
        pg = ctx.new_page(); errs = []
        pg.on("pageerror", lambda e: errs.append(str(e)))
        pg.goto(BASE); pg.wait_for_load_state("load"); pg.wait_for_timeout(1200)
        print("==", theme)
        pg.evaluate("switchTab('stats')"); pg.wait_for_timeout(1500)
        ok(pg.locator("#tabStats canvas").count() == 10, "ten charts")
        drawn = pg.eval_on_selector_all("#tabStats canvas", "els => els.filter(c => c.width > 0 && c.height > 0).length")
        ok(drawn == 10, f"every chart has been drawn ({drawn})")
        ok("Km" in pg.text_content("#stTotals") and "--" not in pg.text_content("#stTotals").split("Km")[1][:12], f"the summary has kilometres ({pg.text_content('#stTotals')[:60]!r})")
        pg.screenshot(path=f"{OUT}/stats-{theme}.png", full_page=True)
        pg.click("#tabStats [data-period='all']"); pg.wait_for_timeout(1000)
        ok("is-active" in pg.get_attribute("#tabStats [data-period='all']", "class"), "the «Todo» period is marked")
        ok(pg.evaluate("document.documentElement.scrollWidth <= window.innerWidth"), "no horizontal overflow")
        ok(not errs, f"no JS errors {errs}")
        ctx.close()
    b.close()
print("FAILURES:", fails or "none")
sys.exit(1 if fails else 0)
