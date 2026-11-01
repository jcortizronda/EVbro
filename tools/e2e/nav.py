"""Navigation by device: touch → bottom dock; wide mouse → sidebar. See tools/e2e/README.md."""
import os
import sys
import tempfile
from playwright.sync_api import sync_playwright

BASE = os.environ.get("EVBRO_URL", "http://127.0.0.1:8099")
USER = os.environ.get("EVBRO_USER", "juan")
PASSWORD = os.environ.get("EVBRO_PASSWORD", "una-contraseña-larga-1")
OUT = sys.argv[1] if len(sys.argv) > 1 else tempfile.mkdtemp(prefix="evbro-e2e-")
fails = []


def ok(c, m):
    print(("  ✔ " if c else "  ✘ ") + m)
    if not c:
        fails.append(m)


# (name, width, height, touch, expected dock, expected sidebar)
CASES = [
    ("móvil 390 (táctil)", 390, 844, True, True, False),
    ("móvil en «sitio de escritorio» 980 (táctil)", 980, 1800, True, True, False),
    ("tablet 820 (táctil)", 820, 1180, True, True, False),
    ("portátil 1024 (ratón)", 1024, 800, False, False, True),
    ("escritorio 1440 (ratón)", 1440, 900, False, False, True),
]
with sync_playwright() as p:
    b = p.chromium.launch()
    for name, w, h, touch, dock, top in CASES:
        ctx = b.new_context(viewport={"width": w, "height": h}, is_mobile=touch, has_touch=touch, locale="es-ES")
        ctx.request.post(f"{BASE}/api/auth/login", headers={"Origin": BASE}, data={"username": USER, "password": PASSWORD})
        pg = ctx.new_page()
        pg.goto(BASE)
        pg.wait_for_load_state("networkidle")
        pg.wait_for_timeout(800)
        coarse = pg.evaluate("matchMedia('(hover: none) and (pointer: coarse)').matches")
        vis = lambda sel: pg.evaluate(f"(() => {{ const e=document.querySelector('{sel}'); return !!e && getComputedStyle(e).display!=='none' && e.getBoundingClientRect().height>0; }})()")
        ok(coarse == touch, f"{name}: detected as {'touch' if coarse else 'mouse'}")
        ok(vis(".native-bottom-dock") == dock, f"{name}: bottom dock {'visible' if dock else 'hidden'}")
        ok(vis(".desktop-sidebar") == top, f"{name}: sidebar {'visible' if top else 'hidden'}")
        ok(not vis(".navbar-subnav-strip") and not vis(".desktop-nav-links"), f"{name}: no top tab bar")
        over = pg.evaluate("document.documentElement.scrollWidth - document.documentElement.clientWidth")
        ok(over <= 0, f"{name}: no horizontal overflow ({over} px)")
        if dock:
            box = pg.evaluate("(() => { const d=document.querySelector('.native-bottom-dock').getBoundingClientRect(); return [Math.round(d.bottom), innerHeight]; })()")
            ok(abs(box[0] - box[1]) <= 1, f"{name}: the dock sits on the bottom edge ({box})")
        pg.screenshot(path=f"{OUT}/nav-{w}-{'touch' if touch else 'mouse'}.png")
        ctx.close()
    meta = b.new_context().new_page()
    meta.goto(BASE)
    content = meta.get_attribute('meta[name="viewport"]', "content")
    ok("user-scalable=no" in content and "maximum-scale=1" in content, f"viewport without zoom: {content}")
    b.close()
print("FAILURES:", fails if fails else "none")
sys.exit(1 if fails else 0)
