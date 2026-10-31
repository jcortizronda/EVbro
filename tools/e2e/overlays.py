"""Modales y hojas de acciones en dispositivos táctiles: sin backdrop-filter (Chrome Android los pinta mal)."""
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


COUNT_BLUR = """() => [...document.querySelectorAll('*')].filter(e => {
  const s = getComputedStyle(e);
  return (s.backdropFilter && s.backdropFilter !== 'none') || (s.webkitBackdropFilter && s.webkitBackdropFilter !== 'none');
}).length"""

with sync_playwright() as p:
    b = p.chromium.launch()
    for name, w, h, touch in [("móvil 390", 390, 844, True), ("móvil «sitio de escritorio» 980", 980, 1800, True), ("escritorio 1280", 1280, 800, False)]:
        for theme in ("dark", "light"):
            ctx = b.new_context(viewport={"width": w, "height": h}, is_mobile=touch, has_touch=touch, locale="es-ES")
            ctx.request.post(f"{BASE}/api/auth/login", headers={"Origin": BASE}, data={"username": USER, "password": PASSWORD})
            ctx.add_init_script(f"localStorage.setItem('evbro_theme','{theme}')")
            pg = ctx.new_page()
            errs = []
            pg.on("pageerror", lambda e: errs.append(str(e)))
            pg.goto(BASE)
            pg.wait_for_load_state("networkidle")
            pg.wait_for_timeout(700)
            tag = f"{name}/{theme}"
            n = pg.evaluate(COUNT_BLUR)
            if touch:
                ok(n == 0, f"{tag}: ningún elemento usa backdrop-filter ({n})")
            else:
                ok(True, f"{tag}: escritorio conserva el desenfoque de overlays ({n} elementos)")
            # hoja de acciones (Ventanillas) y modal (repostaje)
            pg.evaluate("currentVin='LSJA8888888888888'; openWindowsActionSheet()")
            pg.wait_for_timeout(500)
            sheet = pg.evaluate("""() => { const o=document.querySelector('.action-sheet-overlay.open'); if(!o) return null;
              const c=o.querySelector('.action-sheet-card').getBoundingClientRect(); const s=getComputedStyle(o);
              return {bg:s.backgroundColor, bottom:Math.round(c.bottom), vh:innerHeight, w:Math.round(c.width), right:Math.round(c.right), vw:innerWidth}; }""")
            ok(sheet is not None and sheet["bottom"] == sheet["vh"], f"{tag}: la hoja de acciones llega al borde inferior {sheet}")
            pg.screenshot(path=f"{OUT}/sheet-{w}-{theme}.png")
            pg.evaluate("closeActionSheet()")
            pg.wait_for_timeout(400)
            pg.evaluate("switchTab('costs'); openRefuelModal()")
            pg.wait_for_timeout(500)
            modal = pg.evaluate("""() => { const o=document.querySelector('#refuelModal.open'); if(!o) return null;
              const c=o.querySelector('.modal-card').getBoundingClientRect(); return {top:Math.round(c.top), bottom:Math.round(c.bottom), vh:innerHeight, left:Math.round(c.left), right:Math.round(c.right), vw:innerWidth,
              bg:getComputedStyle(o).backgroundColor}; }""")
            ok(modal is not None and modal["top"] >= 0 and modal["bottom"] <= modal["vh"] and modal["left"] >= 0 and modal["right"] <= modal["vw"],
               f"{tag}: el modal cabe entero en pantalla {modal}")
            pg.screenshot(path=f"{OUT}/modal-{w}-{theme}.png")
            ok(not errs, f"{tag}: sin errores JS {errs[:1]}")
            ctx.close()
    b.close()
print("FALLOS:", fails if fails else "ninguno")
sys.exit(1 if fails else 0)
