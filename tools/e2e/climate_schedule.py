"""Scheduled climate (Confort): create, pause and delete a schedule. See tools/e2e/README.md."""
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


with sync_playwright() as p:
    b = p.chromium.launch()
    ctx = b.new_context(viewport={"width": 390, "height": 844}, is_mobile=True, has_touch=True, locale="es-ES")
    ctx.add_init_script("new MutationObserver(() => { const b = document.querySelector('#appDialog.open #appDialogOk'); if (b) b.click(); }).observe(document, { attributes: true, subtree: true, attributeFilter: ['class'] })")   # the app's own confirm dialog is accepted
    ctx.request.post(f"{BASE}/api/auth/login", headers={"Origin": BASE}, data={"username": USER, "password": PASSWORD})
    pg = ctx.new_page()
    errors = []
    pg.on("pageerror", lambda e: errors.append(str(e)))
    pg.on("dialog", lambda d: (errors.append("browser dialog: " + d.message), d.dismiss()))
    pg.goto(BASE)
    pg.wait_for_load_state("networkidle")
    pg.wait_for_timeout(800)
    txt = lambda sel: pg.inner_text(sel)

    pg.evaluate("switchTab('climate')")
    pg.wait_for_timeout(900)
    ok("Sin programaciones" in txt("#climateScheduleList"), f"the list starts empty ({txt('#climateScheduleList')[:50]!r})")
    pg.fill("#climateScheduleTime", "07:45")
    pg.click("#climateScheduleDays button >> nth=5")           # add Saturday
    pg.fill("#climateScheduleLabel", "Ir al trabajo")
    pg.click("#climateSchedulePanel >> text=Programar")
    pg.wait_for_timeout(900)
    row = txt("#climateScheduleList")
    ok("07:45" in row and "22 °C" in row and "Ir al trabajo" in row and "Activa" in row, f"the schedule shows up ({row[:90]!r})")
    ok("lunes, martes, miércoles, jueves, viernes, sábado" in row, "the chosen days are described")
    pg.screenshot(path=f"{OUT}/climate-schedule.png", full_page=True)
    pg.click("#climateScheduleList input[type=checkbox]")
    pg.wait_for_timeout(800)
    ok("En pausa" in txt("#climateScheduleList"), "the switch pauses it")
    api = ctx.request.get(f"{BASE}/api/vehicles/" + pg.evaluate("currentVin") + "/climate-schedules").json()
    ok(len(api) == 1 and api[0]["enabled"] is False and api[0]["time_local"] == "07:45" and api[0]["days"] == [0, 1, 2, 3, 4, 5], f"the server stores it the same way ({api})")
    pg.click("#climateScheduleList >> text=Borrar")
    pg.wait_for_timeout(800)
    ok("Sin programaciones" in txt("#climateScheduleList"), "deleting removes it")
    ok(not errors, f"no JS errors {errors}")
    b.close()
print("FAILURES:", fails if fails else "none")
sys.exit(1 if fails else 0)
