"""Browser test (Playwright) of Confort → «Precalentamiento de batería programado»: the state comes from what the car reports
(Activado / Desactivado), the schedule has its time, its weekday chips and its switch (one, like the official app, even for a
car that holds two entries), and «Programar» sends it.
The departure API is simulated (the demo car reports no plan). See tools/e2e/README.md."""
import json
import os
import sys
import tempfile

from playwright.sync_api import sync_playwright

BASE = os.environ.get("EVBRO_URL", "http://127.0.0.1:8099")
OUT = sys.argv[1] if len(sys.argv) > 1 else tempfile.mkdtemp(prefix="evbro-e2e-")
USER = os.environ.get("EVBRO_USER", "juan")
PASSWORD = os.environ.get("EVBRO_PASSWORD", "una-contraseña-larga-1")
fails = []


def ok(cond, msg):
    print(("  ✔ " if cond else "  ✘ ") + msg)
    if not cond:
        fails.append(msg)


with sync_playwright() as p:
    browser = p.chromium.launch()
    for theme in ("dark", "light"):
        ctx = browser.new_context(viewport={"width": 390, "height": 844}, is_mobile=True, has_touch=True, locale="es-ES", service_workers="block")
        assert ctx.request.post(f"{BASE}/api/auth/login", headers={"Origin": BASE}, data={"username": USER, "password": PASSWORD}).ok
        ctx.add_init_script(f"localStorage.setItem('evbro_theme','{theme}')")
        # the car: Monday to Friday at 07:30, switched off
        car = {"known": True, "enabled": False, "timezone": "Europe/Madrid", "departures": [{"time": "07:30", "days": [1, 2, 3, 4, 5], "enabled": False}]}
        puts = []

        def departure(route):
            if route.request.method == "PUT":
                body = json.loads(route.request.post_data)
                puts.append(body)
                car["departures"] = [{"time": d["time"], "days": d["days"], "enabled": d["enabled"]} for d in body["departures"]]
                car["enabled"] = any(d["enabled"] for d in car["departures"])
                route.fulfill(json={"success": True, **car})
            else:
                route.fulfill(json=car)

        ctx.route("**/api/vehicles/*/departure", departure)
        pg = ctx.new_page()
        errs = []
        pg.on("pageerror", lambda e: errs.append(str(e)))
        pg.goto(BASE)
        pg.wait_for_load_state("networkidle")
        pg.wait_for_timeout(1200)
        pg.evaluate("switchTab('climate')")
        pg.wait_for_selector("#departurePanel .dep-row")
        pg.locator("#departurePanel").scroll_into_view_if_needed()
        pg.wait_for_timeout(300)
        panel = pg.inner_text("#departurePanel")
        ok("Precalentamiento de batería programado" in panel and "app oficial" not in panel, f"{theme}: the panel has its new title and no «según la app oficial»")
        ok(pg.locator("#departurePanel .comfort-state").count() == 0 and pg.inner_text("#departureSave") == "Programar",
           f"{theme}: no ON/OFF badge, and the button says «Programar», like the scheduled climate")
        rows = pg.locator("#departurePanel .dep-row")
        ok(rows.count() == 1 and rows.locator(".csched-switch span").inner_text() == "Desactivado", f"{theme}: one schedule, and it says Desactivado")
        on = [rows.nth(0).locator(".csched-day").nth(k).get_attribute("aria-pressed") for k in range(7)]
        ok(on == ["true"] * 5 + ["false"] * 2, f"{theme}: the days of the car are marked, Monday first ({on})")
        ok(pg.is_disabled("#departureSave"), f"{theme}: nothing to save until something changes")
        over = pg.evaluate("document.documentElement.scrollWidth - document.documentElement.clientWidth")
        ok(over <= 0, f"{theme}: no horizontal overflow ({over} px)")
        chip = rows.nth(0).locator(".csched-day").first.bounding_box()
        ok(chip["height"] >= 40 and chip["width"] >= 36, f"{theme}: the day chips are big enough for a finger ({chip['width']:.0f}×{chip['height']:.0f})")
        pg.locator("#departurePanel").screenshot(path=f"{OUT}/departure-{theme}.png")

        # Saturday added, Friday taken away, and switched on
        rows.nth(0).locator(".csched-day").nth(5).click()
        rows.nth(0).locator(".csched-day").nth(4).click()
        pg.locator("#departurePanel .dep-row .csched-switch input").check()
        ok(pg.is_enabled("#departureSave"), f"{theme}: a change enables «Programar»")
        pg.click("#departureSave")
        pg.wait_for_timeout(800)
        ok(len(puts) == 1 and puts[0] == {"departures": [{"time": "07:30", "days": [1, 2, 3, 4, 6], "enabled": True}]},
           f"{theme}: one order with the time, the days and the switch ({puts})")
        row = pg.locator("#departurePanel .dep-row")
        ok(row.locator(".csched-switch span").inner_text() == "Activado" and pg.is_disabled("#departureSave"),
           f"{theme}: saved: it reads Activado")
        pg.locator("#departurePanel").screenshot(path=f"{OUT}/departure-on-{theme}.png")

        # the last day cannot be taken away
        for k in (0, 1, 2, 3, 5):
            row.locator(".csched-day").nth(k).click()
        left = [row.locator(".csched-day").nth(k).get_attribute("aria-pressed") for k in range(7)]
        ok(left.count("true") == 1, f"{theme}: the schedule keeps at least one day ({left})")
        ok(not errs, f"{theme}: no JS errors {errs}")
        ctx.close()
    browser.close()
print("FAILURES: " + ("; ".join(fails) if fails else "none"))
sys.exit(1 if fails else 0)
