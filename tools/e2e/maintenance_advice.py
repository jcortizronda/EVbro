"""Browser test (Playwright): Mantenimiento → «Recomendaciones de EVbro». What is due is listed first with its reason and is
answered with «Recuérdamelo más adelante», «Revisado, está bien» or «Registrar intervención»; any of them can be switched off and on
again. See tools/e2e/README.md."""
import datetime
import os
import sys
import tempfile

from playwright.sync_api import sync_playwright

BASE = os.environ.get("EVBRO_URL", "http://127.0.0.1:8099")
USER = os.environ.get("EVBRO_USER", "juan")
PASSWORD = os.environ.get("EVBRO_PASSWORD", "una-contraseña-larga-1")
OUT = sys.argv[1] if len(sys.argv) > 1 else tempfile.mkdtemp(prefix="evbro-e2e-")
fails = []


def ok(cond, msg):
    print(("  ✔ " if cond else "  ✘ ") + msg)
    if not cond:
        fails.append(msg)


def status(pg, key):
    return pg.get_attribute(f".ma-item[data-key='{key}']", "data-status")


def answer(pg, key, text):
    pg.locator(f".ma-item[data-key='{key}'] button", has_text=text).click()
    pg.wait_for_timeout(700)


with sync_playwright() as p:
    browser = p.chromium.launch()
    # the answers are kept on the server, so the walk through them is done once (dark); the light pass only looks at the card
    for theme in ("dark", "light"):
        ctx = browser.new_context(viewport={"width": 390, "height": 844}, is_mobile=True, has_touch=True, locale="es-ES", service_workers="block")
        assert ctx.request.post(f"{BASE}/api/auth/login", headers={"Origin": BASE}, data={"username": USER, "password": PASSWORD}).ok
        ctx.add_init_script(f"localStorage.setItem('evbro_theme','{theme}')")
        vin = ctx.request.get(f"{BASE}/api/vehicles").json()[0]["vin"]
        # a car delivered two years ago: brakes, rotation and wipers are due (the demo car has 14,520 km and no delivery date)
        delivered = (datetime.date.today() - datetime.timedelta(days=730)).isoformat()
        ctx.request.put(f"{BASE}/api/vehicles/{vin}", headers={"Origin": BASE}, data={"delivery_date": f"{delivered}T00:00:00"})
        for key in ("brakes", "tyre_rotation", "wipers", "tyre_age", "battery_12v"):                    # as never answered
            ctx.request.put(f"{BASE}/api/vehicles/{vin}/maintenance/advice/{key}", headers={"Origin": BASE}, data={"enabled": True})
        pg = ctx.new_page()
        errs = []
        pg.on("pageerror", lambda e, errs=errs: errs.append(str(e)))
        pg.goto(BASE)
        pg.wait_for_load_state("networkidle")
        pg.wait_for_timeout(1200)
        pg.evaluate("switchTab('maintenance')")
        pg.wait_for_selector("#maintAdviceCard:not([hidden]) .ma-item")
        pg.wait_for_timeout(500)
        keys = pg.locator(".ma-item").evaluate_all("els => els.map(e => e.dataset.key + ':' + e.dataset.status)")
        ok(pg.is_visible("text=Recomendaciones de EVbro") and keys[0].endswith(":due"), f"{theme}: the card lists what is due first ({keys})")
        ok(pg.inner_text("#maintAdviceCount") == str(sum(1 for k in keys if k.endswith(":due"))), f"{theme}: the count beside the title is what is due")
        ok(pg.evaluate("document.documentElement.scrollWidth - document.documentElement.clientWidth") <= 0, f"{theme}: the card does not overflow the phone")
        pg.locator("#maintAdviceCard").scroll_into_view_if_needed()
        pg.locator("#maintAdviceCard").screenshot(path=f"{OUT}/advice-{theme}.png")
        if theme == "light":
            ok(not errs, f"{theme}: no JS errors {errs}")
            ctx.close()
            continue
        ok(status(pg, "tyre_rotation") == "due" and "km desde" in pg.inner_text(".ma-item[data-key='tyre_rotation'] .ma-standing"),
           f"{theme}: the rotation is due by km and says since when ({pg.inner_text(chr(46) + 'ma-item[data-key=' + chr(39) + 'tyre_rotation' + chr(39) + '] .ma-standing')!r})")
        ok(pg.is_visible(".ma-item[data-key='tyre_rotation'] .ma-why") and not pg.locator(".ma-item[data-status='ok'] .ma-why").count(),
           f"{theme}: what is due explains why; what is in time stays short")

        # ── «Recuérdamelo más adelante»
        answer(pg, "tyre_rotation", "Recuérdamelo más adelante")
        ok(status(pg, "tyre_rotation") == "snoozed" and "Aplazada hasta" in pg.inner_text(".ma-item[data-key='tyre_rotation'] .ma-standing"),
           f"{theme}: «más adelante» postpones it and says until when")
        # ── «Revisado, está bien»
        answer(pg, "tyre_rotation", "Revisado, está bien")
        ok(status(pg, "tyre_rotation") == "ok" and "Faltan 10.000 km" in pg.inner_text(".ma-item[data-key='tyre_rotation'] .ma-standing"),
           f"{theme}: «revisado» counts again from today's km ({pg.inner_text(chr(46) + 'ma-item[data-key=' + chr(39) + 'tyre_rotation' + chr(39) + '] .ma-standing')!r})")
        # ── «Registrar intervención»: the usual form, started with the recommendation; saving it answers it
        if status(pg, "brakes") == "due":
            answer(pg, "brakes", "Registrar intervención")
            pg.wait_for_selector("#maintEventModal.open")
            ok(pg.input_value("#evTitle") == "Revisar pastillas y discos de freno" and pg.input_value("#evCat") == "Frenos",
               f"{theme}: the intervention form opens with the recommendation's name and category")
            pg.click("#maintEventModal .btn-primary")
            pg.wait_for_selector("#maintEventModal:not(.open)", state="attached")
            pg.wait_for_timeout(1200)
            ok(status(pg, "brakes") == "ok", f"{theme}: recording the intervention answers the recommendation")
        else:
            ok(False, f"{theme}: the brakes were expected to be due ({status(pg, 'brakes')})")
        # ── off and on again
        answer(pg, "wipers", "No me interesa")
        ok(status(pg, "wipers") == "off", f"{theme}: «no me interesa» switches it off")
        answer(pg, "wipers", "Activar")
        ok(status(pg, "wipers") == "due", f"{theme}: and «activar» brings it back")
        ok(not errs, f"{theme}: no JS errors {errs}")

        # clean up for the next theme: the interventions this one recorded
        for log in ctx.request.get(f"{BASE}/api/vehicles/{vin}/maintenance/logs").json():
            if (log.get("title") or "") == "Revisar pastillas y discos de freno":
                ctx.request.delete(f"{BASE}/api/maintenance/logs/{log['id']}", headers={"Origin": BASE})
        ctx.close()
    # leave the demo car as it was: no delivery date
    ctx = browser.new_context()
    ctx.request.post(f"{BASE}/api/auth/login", headers={"Origin": BASE}, data={"username": USER, "password": PASSWORD})
    ctx.request.put(f"{BASE}/api/vehicles/{vin}", headers={"Origin": BASE}, data={"delivery_date": None})
    browser.close()
print("FAILURES: " + ("; ".join(fails) if fails else "none"))
sys.exit(1 if fails else 0)
