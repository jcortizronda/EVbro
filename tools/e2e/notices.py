"""Browser test (Playwright) of the notice centre: dismissing one notice by swiping it sideways, or with its cross when there is a
mouse. The notice API is simulated. See tools/e2e/README.md."""
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


def notices():
    """One notice sent by two channels today, and two notices yesterday."""
    return [
        {"id": 11, "timestamp": "2026-11-21T09:00:00", "category": "security", "title": "Coche abierto", "message": "Sin cerrar con llave",
         "channel": "webpush", "success": True, "error": None, "unread": True},
        {"id": 12, "timestamp": "2026-11-21T09:00:00.300000", "category": "security", "title": "Coche abierto", "message": "Sin cerrar con llave",
         "channel": "telegram", "success": False, "error": "sin chat", "unread": True},
        {"id": 13, "timestamp": "2026-11-20T18:00:00", "category": "fuel_low", "title": "Depósito bajo", "message": "Queda un 10 %",
         "channel": "webpush", "success": True, "error": None, "unread": False},
        {"id": 14, "timestamp": "2026-11-20T08:00:00", "category": "maintenance", "title": "Revisión", "message": "Toca la revisión",
         "channel": "webpush", "success": True, "error": None, "unread": False},
    ]


def swipe(pg, selector, dx, steps=8):
    box = pg.locator(selector).bounding_box()
    y = box["y"] + box["height"] / 2
    x = box["x"] + box["width"] / 2
    pg.mouse.move(x, y)
    pg.mouse.down()
    for i in range(1, steps + 1):
        pg.mouse.move(x + dx * i / steps, y)
    pg.mouse.up()


with sync_playwright() as p:
    browser = p.chromium.launch()
    for theme, touch in (("dark", True), ("light", True), ("light", False)):
        opts = {"viewport": {"width": 390, "height": 844}, "is_mobile": True, "has_touch": True} if touch else {"viewport": {"width": 1280, "height": 900}}
        ctx = browser.new_context(locale="es-ES", service_workers="block", **opts)
        ctx.request.post(f"{BASE}/api/auth/login", headers={"Origin": BASE}, data={"username": USER, "password": PASSWORD})
        ctx.add_init_script(f"localStorage.setItem('evbro_theme','{theme}')")
        pg = ctx.new_page()
        errs, deleted = [], []
        state = {"rows": notices(), "fail": False}
        pg.on("pageerror", lambda e: errs.append(str(e)))

        def handle(route, request):
            url = request.url
            if request.method == "GET" and "/api/notifications/logs" in url:
                return route.fulfill(status=200, content_type="application/json", body=json.dumps(state["rows"]))
            if request.method == "DELETE" and "/api/notifications/logs/" in url:
                rid = int(url.rsplit("/", 1)[1])
                if state["fail"]:
                    return route.fulfill(status=500, content_type="application/json", body="{}")
                deleted.append(rid)
                return route.fulfill(status=200, content_type="application/json", body=json.dumps({"status": "ok", "removed": 1}))
            if "/api/notifications/seen" in url or "/api/notifications/unread" in url:
                return route.fulfill(status=200, content_type="application/json", body=json.dumps({"status": "ok", "count": 0}))
            return route.continue_()

        pg.route("**/api/notifications/**", handle)
        pg.goto(BASE)
        pg.wait_for_load_state("networkidle")
        pg.evaluate("openNotificationsModal()")
        pg.wait_for_timeout(600)
        tag = f"{theme}/{'touch' if touch else 'mouse'}"
        rows = lambda: pg.locator("#modalNotifLogsList .notice-row").count()  # noqa: E731
        ok(rows() == 3, f"{tag}: three notices (the one sent by two channels is one row)")
        first = "#modalNotifLogsList .notice-row >> nth=0"

        if touch:
            ok(pg.locator(".notice-dismiss >> nth=0").is_hidden(), f"{tag}: no cross on a touch screen")
            swipe(pg, first, 30)
            pg.wait_for_timeout(400)
            ok(rows() == 3 and deleted == [] and pg.evaluate("document.querySelector('.notice-row').style.transform") == "",
               f"{tag}: a short swipe puts the notice back")
            state["fail"] = True
            swipe(pg, first, 260)
            pg.wait_for_timeout(700)
            ok(rows() == 3 and pg.evaluate("!document.querySelector('.notice-row').classList.contains('is-leaving')"),
               f"{tag}: if the server fails, the notice comes back")
            state["fail"] = False
            pg.wait_for_timeout(300)
            swipe(pg, first, 260)
            pg.wait_for_timeout(700)
            ok(deleted == [11] and rows() == 2, f"{tag}: a long swipe to the right dismisses it ({deleted})")
            ok(pg.locator("#modalNotifLogsList .hist-month").count() == 1, f"{tag}: today's heading goes with its last notice")
            swipe(pg, first, -260)
            pg.wait_for_timeout(700)
            ok(deleted == [11, 13] and rows() == 1, f"{tag}: also to the left")
            pg.screenshot(path=f"{OUT}/notices-{theme}.png")
            swipe(pg, first, 260)
            pg.wait_for_timeout(700)
            ok("Todavía no hay avisos" in pg.inner_text("#modalNotifLogsList"), f"{tag}: the last one leaves the empty message")
        else:
            ok(pg.locator(".notice-dismiss >> nth=0").is_visible(), f"{tag}: with a mouse each notice has its cross")
            pg.click(".notice-dismiss >> nth=1")
            pg.wait_for_timeout(700)
            ok(deleted == [13] and rows() == 2, f"{tag}: the cross dismisses that notice ({deleted})")
            pg.screenshot(path=f"{OUT}/notices-desktop.png")
        over = pg.evaluate("document.documentElement.scrollWidth - document.documentElement.clientWidth")
        ok(over <= 0, f"{tag}: no horizontal overflow ({over} px)")
        ok(not errs, f"{tag}: no JS errors {errs}")
        ctx.close()
    browser.close()
print("FAILURES: " + ("; ".join(fails) if fails else "none"))
sys.exit(1 if fails else 0)
