"""The phone's bottom bar chosen by each account: Inicio and Más fixed, up to four sections in between. See tools/e2e/README.md."""
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


def bar(pg):
    return pg.eval_on_selector_all(".native-bottom-dock .dock-item", "els => els.filter(e => getComputedStyle(e).display !== 'none').map(e => e.id)")


def login(ctx):
    ctx.request.post(f"{BASE}/api/auth/login", headers={"Origin": BASE}, data={"username": USER, "password": PASSWORD})


with sync_playwright() as p:
    b = p.chromium.launch()
    for theme in ("dark", "light"):
        ctx = b.new_context(viewport={"width": 360, "height": 780}, is_mobile=True, has_touch=True, locale="es-ES", service_workers="block")
        login(ctx)
        ctx.request.delete(f"{BASE}/api/account/home-layout?page=dock", headers={"Origin": BASE})
        ctx.add_init_script(f"localStorage.setItem('evbro_theme','{theme}'); localStorage.removeItem('evbro_dock')")
        pg = ctx.new_page()
        errors = []
        pg.on("pageerror", lambda e, errors=errors: errors.append(str(e)))
        pg.goto(BASE)
        pg.wait_for_load_state("networkidle")
        pg.wait_for_timeout(800)
        print("==", theme)
        ok(bar(pg) == ["mobTabOverview", "mobTabCharges", "mobTabClimate", "mobTabMore"], f"the usual bar: Inicio, Carga, Confort, Más {bar(pg)}")

        pg.evaluate("switchTab('more')")
        pg.wait_for_timeout(300)
        pg.locator("#dockCustomize").scroll_into_view_if_needed()
        ok(pg.is_visible("#dockCustomize"), "Más offers «Personalizar barra inferior»")
        pg.click("#dockCustomize")
        pg.wait_for_timeout(300)
        ok(pg.locator("#dockChosen .dock-edit-row").count() == 4 and pg.locator("#dockChosen .is-fixed").count() == 2, "the editor lists Inicio and Más as fixed, and the two sections")
        pg.click('#dockFree [data-dock="drives"]')
        pg.click('#dockFree [data-dock="maintenance"]')
        pg.wait_for_timeout(200)
        ok(bar(pg) == ["mobTabOverview", "mobTabCharges", "mobTabClimate", "mobTabDrives", "mobTabMaintenance", "mobTabMore"], f"two sections added: six icons {bar(pg)}")
        ok(pg.is_visible("#dockFull") and pg.locator("#dockFree .dock-edit-add:not([disabled])").count() == 0, "with six icons nothing else can be added, and it says why")
        pg.screenshot(path=f"{OUT}/barra-editor-{theme}.png")
        pg.click('#dockChosen [data-dock="climate"] .hw-remove')
        pg.click('#dockChosen [data-dock="maintenance"] [data-on-click*="-1"]')
        pg.wait_for_timeout(200)
        ok(bar(pg) == ["mobTabOverview", "mobTabCharges", "mobTabMaintenance", "mobTabDrives", "mobTabMore"], f"remove and reorder {bar(pg)}")
        pg.click('#dockFree [data-dock="sensors"]')
        pg.click("#dockModal >> text=Hecho")
        pg.wait_for_timeout(600)

        # what is in the bar is not repeated in Más; its row partner takes the whole width
        pg.evaluate("switchTab('more')")
        pg.wait_for_timeout(300)
        shown = lambda sel: pg.is_visible(f'#tabMore .native-card-btn[data-on-click="{sel}"]')
        ok(not shown("switchTab('drives')") and not shown("switchTab('maintenance')") and not shown("openAllSensorsModal()"),
           "Viajes, Mantenimiento and Sensores left Más")
        ok(shown("switchTab('stats')") and shown("switchTab('geofences')") and shown("switchTab('costs')"), "the rest of Más stays")
        row_hidden = pg.evaluate("(() => { const b = document.querySelector(`#tabMore [data-on-click=\"switchTab('drives')\"]`); return getComputedStyle(b.parentElement).display === 'none'; })()")
        ok(row_hidden, "the Viajes/Mantenimiento row, now empty, is gone")
        alone = pg.evaluate("""(() => {
          const ex = document.querySelector(`#tabMore [data-on-click="switchTab('expenses')"]`);
          const co = document.querySelector(`#tabMore [data-on-click="switchTab('costs')"]`);
          ex.classList.add('in-dock');
          const r = co.getBoundingClientRect().width, g = co.parentElement.getBoundingClientRect().width;
          ex.classList.remove('in-dock');
          return Math.abs(r - g) < 2;
        })()""")
        ok(alone, "a button left alone in its row takes the whole width")
        pg.screenshot(path=f"{OUT}/mas-sin-repetidos-{theme}.png", full_page=True)

        # the icons work like the Más ones, and the right one lights up
        pg.click("#mobTabDrives")
        pg.wait_for_timeout(400)
        ok(pg.is_visible("#tabDrives") and "active" in pg.get_attribute("#mobTabDrives", "class") and "active" not in pg.get_attribute("#mobTabMore", "class"),
           "Viajes opens from the bar and lights its own icon, not Más")
        pg.evaluate("switchTab('costs')")
        pg.wait_for_timeout(200)
        ok("active" in pg.get_attribute("#mobTabMore", "class"), "a section not in the bar still lights Más")
        pg.click("#mobDockSensors")
        pg.wait_for_timeout(400)
        ok(pg.evaluate("document.getElementById('allSensorsModal').classList.contains('open')"), "Sensores opens its window")
        pg.evaluate("closeAllSensorsModal()")
        over = pg.evaluate("(() => { const d = document.querySelector('.native-bottom-dock'); return d.scrollWidth > d.clientWidth + 1 || document.documentElement.scrollWidth > document.documentElement.clientWidth + 1; })()")
        ok(not over, "at 360 px six icons fit without overflowing")
        pg.screenshot(path=f"{OUT}/barra-6-{theme}.png")

        saved = ctx.request.get(f"{BASE}/api/account/home-layout").json()["layout"]
        ok(saved and saved["pages"]["dock"] == ["charges", "maintenance", "drives", "sensors"], f"the server keeps the bar {saved}")
        other = b.new_context(viewport={"width": 390, "height": 844}, is_mobile=True, has_touch=True, locale="es-ES", service_workers="block")
        login(other)
        o = other.new_page()
        o.goto(BASE)
        o.wait_for_load_state("networkidle")
        o.wait_for_timeout(800)
        ok(bar(o) == ["mobTabOverview", "mobTabCharges", "mobTabMaintenance", "mobTabDrives", "mobDockSensors", "mobTabMore"], f"another phone shows the same bar {bar(o)}")
        other.close()

        pg.evaluate("Dock.open()")
        pg.click("#dockModal >> text=Restablecer")
        pg.wait_for_timeout(500)
        ok(bar(pg) == ["mobTabOverview", "mobTabCharges", "mobTabClimate", "mobTabMore"], "«Restablecer» brings the usual bar back")
        ok(pg.locator("#tabMore .native-card-btn.in-dock").count() == 0, "and Más gets every section back")
        ok(ctx.request.get(f"{BASE}/api/account/home-layout").json()["layout"] is None, "and the server forgets it")
        pg.click("#dockModal >> text=Hecho")
        ok(not errors, f"no JS errors {errors[:2]}")
        ctx.close()

    # a computer has no bottom bar, so no button to customise it
    desk = b.new_context(viewport={"width": 1366, "height": 900}, locale="es-ES", service_workers="block")
    login(desk)
    d = desk.new_page()
    d.goto(BASE)
    d.wait_for_load_state("networkidle")
    d.evaluate("switchTab('more')")
    d.wait_for_timeout(300)
    ok(not d.is_visible("#dockCustomize") and not d.is_visible(".native-bottom-dock"), "on a computer neither the bar nor its button")
    desk.close()
    b.close()
print("FAILURES:", fails if fails else "none")
sys.exit(1 if fails else 0)
