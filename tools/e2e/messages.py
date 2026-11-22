"""Browser test (Playwright): a message from the administrator. The console (desktop, port 8098) sends it from «Mensajes»; the
manager finds it in the bell of the app (phone) marked as coming from the administrator, silences these messages in
Ajustes → Avisos, and the next one is counted as silenced in the console. See tools/e2e/README.md."""
import os
import sys
import tempfile

from playwright.sync_api import sync_playwright

BASE = os.environ.get("EVBRO_URL", "http://127.0.0.1:8099")
CONSOLE = os.environ.get("EVBRO_ADMIN_URL", "http://127.0.0.1:8098")
ADMIN, ADMIN_PW = "admin", "contraseña-del-admin-1"
USER = os.environ.get("EVBRO_USER", "juan")
PASSWORD = os.environ.get("EVBRO_PASSWORD", "una-contraseña-larga-1")
OUT = sys.argv[1] if len(sys.argv) > 1 else tempfile.mkdtemp(prefix="evbro-e2e-")
BODY = "El sábado de 22:00 a 23:00 el servidor estará parado.\nNo hace falta que hagas nada: <b>esto no es HTML</b>."
fails = []


def ok(cond, msg):
    print(("  ✔ " if cond else "  ✘ ") + msg)
    if not cond:
        fails.append(msg)


ACCEPT = """new MutationObserver(() => {
  const dlg = document.getElementById('dlgAsk');
  if (!dlg || !dlg.open || dlg.dataset.answered === '1') return;
  dlg.dataset.answered = '1';
  window.__asked = (window.__asked || []).concat(document.getElementById('askMessage').textContent);
  setTimeout(() => { document.getElementById('askOk').click(); delete dlg.dataset.answered; }, 20);
}).observe(document, { attributes: true, subtree: true, attributeFilter: ['open'] })"""


def send(pg, title):
    pg.fill("#msgTitle", title)
    pg.fill("#msgBody", BODY)
    pg.click("#btnMsgSend")
    pg.wait_for_function("() => document.getElementById('msgTitle').value === ''", timeout=8000)
    pg.wait_for_timeout(400)


def open_bell(pg):
    pg.evaluate("openNotificationsModal()")
    pg.wait_for_selector("#notificationsCenterModal.open .notice-row")
    pg.wait_for_timeout(300)


with sync_playwright() as p:
    browser = p.chromium.launch()
    for theme in ("dark", "light"):
        # the manager starts with the messages switched on
        app = browser.new_context(viewport={"width": 390, "height": 844}, is_mobile=True, has_touch=True, locale="es-ES", service_workers="block")
        assert app.request.post(f"{BASE}/api/auth/login", headers={"Origin": BASE}, data={"username": USER, "password": PASSWORD}).ok
        assert app.request.put(f"{BASE}/api/notifications/config", headers={"Origin": BASE}, data={"notify_admin_messages": True}).ok
        app.request.delete(f"{BASE}/api/notifications/logs", headers={"Origin": BASE})
        app.add_init_script(f"localStorage.setItem('evbro_theme','{theme}')")

        # 1. The console sends a message to every manager
        con = browser.new_context(viewport={"width": 1280, "height": 900}, color_scheme=theme, locale="es-ES")
        cp = con.new_page()
        cerrs = []
        cp.on("pageerror", lambda e: cerrs.append(str(e)))
        cp.add_init_script(ACCEPT)
        cp.goto(CONSOLE)
        cp.wait_for_selector("#viewAuth:not([hidden])")
        cp.fill("#authUser", ADMIN)
        cp.fill("#authPass", ADMIN_PW)
        cp.click("#authSubmit")
        cp.wait_for_selector(".tab[data-tab='messages']")
        cp.click(".tab[data-tab='messages']")
        cp.wait_for_timeout(500)
        ok(cp.is_visible("#tabMessages") and cp.is_hidden("#tabManagers"), f"{theme}: the console has a «Mensajes» tab")
        total = sum(1 for u in con.request.get(f"{CONSOLE}/api/admin/users").json() if u["role"] == "manager" and u["is_active"])
        ok(cp.inner_text("#msgSummary") == f"Gestores que lo recibirán: {total}." and cp.is_enabled("#btnMsgSend"),
           f"{theme}: it says how many managers will get it ({cp.inner_text('#msgSummary')!r})")
        cp.check("input[name='msgAudience'][value='selected']")
        ok(cp.is_visible("#msgManagers") and cp.is_disabled("#btnMsgSend") and USER in cp.inner_text("#msgManagers"),
           f"{theme}: «solo los que elija» lists the managers and waits for one to be chosen ({cp.inner_text('#msgSummary')!r})")
        cp.locator("#msgManagers input[type='checkbox']").first.check()
        ok(cp.is_enabled("#btnMsgSend"), f"{theme}: with one chosen it can be sent")
        cp.check("input[name='msgAudience'][value='all']")
        cp.fill("#msgBody", BODY)
        ok(cp.inner_text("#msgCount").startswith(str(len(BODY))), f"{theme}: the text shows its length ({cp.inner_text('#msgCount')!r})")
        before = cp.locator("#tblMessages tbody tr").count()
        send(cp, f"Mantenimiento el sábado ({theme})")
        asked = cp.evaluate("window.__asked || []")
        ok(len(asked) == 1 and "No se puede retirar" in asked[0], f"{theme}: it asks before sending ({asked})")
        row = cp.locator("#tblMessages tbody tr").first
        ok(cp.locator("#tblMessages tbody tr").count() == before + 1 and f"({theme})" in row.inner_text() and "esto no es HTML" in row.inner_text(),
           f"{theme}: the message is listed as sent, as text")
        cells = [c.strip() for c in row.locator("td.num").all_inner_texts()]
        ok(cells[1:] == [str(total), "0", "0"], f"{theme}: every manager received it, nobody had it silenced ({cells})")
        cp.screenshot(path=f"{OUT}/messages-console-{theme}.png", full_page=True)

        # 2. The manager finds it in the bell, on the phone
        pg = app.new_page()
        errs = []
        pg.on("pageerror", lambda e: errs.append(str(e)))
        pg.goto(BASE)
        pg.wait_for_load_state("networkidle")
        pg.wait_for_timeout(1500)
        ok(pg.evaluate("document.getElementById('headerNotifDot').classList.contains('is-on')"), f"{theme}: the bell lights its dot")
        open_bell(pg)
        first = pg.locator("#notificationsCenterModal .notice-row").first
        ok("notice-admin" in first.get_attribute("class") and "Mensaje del administrador del servidor".upper() in first.inner_text().upper()
           and f"({theme})" in first.inner_text(), f"{theme}: the notice says it comes from the administrator")
        ok(pg.evaluate("document.querySelector('#notificationsCenterModal .notice-row .notice-text b') === null")
           and "<b>esto no es HTML</b>" in first.inner_text(), f"{theme}: the text is shown as typed, not as HTML")
        lines = pg.evaluate("getComputedStyle(document.querySelector('#notificationsCenterModal .notice-text')).whiteSpace")
        ok(lines == "pre-line", f"{theme}: line breaks are kept ({lines})")
        over = pg.evaluate("document.documentElement.scrollWidth - document.documentElement.clientWidth")
        ok(over <= 0, f"{theme}: no horizontal overflow in the notice centre ({over} px)")
        pg.screenshot(path=f"{OUT}/messages-bell-{theme}.png")
        pg.evaluate("closeNotificationsModal()")

        # 3. Silencing them in Ajustes → Avisos
        pg.evaluate("switchTab('settings'); switchSettingsSubTab('notifs')")
        pg.wait_for_timeout(500)
        pg.evaluate("document.querySelector('[data-key=\"notif-events\"]').open = true")
        card = pg.locator(".notif-trigger-card", has=pg.locator("#notifEvtAdminMessages"))
        card.scroll_into_view_if_needed()
        ok(pg.is_checked("#notifEvtAdminMessages") and "Mensajes del administrador" in card.inner_text(), f"{theme}: Avisos has the switch, on by default")
        over = pg.evaluate("document.documentElement.scrollWidth - document.documentElement.clientWidth")
        ok(over <= 0, f"{theme}: no horizontal overflow in Ajustes → Avisos ({over} px)")
        pg.screenshot(path=f"{OUT}/messages-switch-{theme}.png")
        card.locator(".ios-switch").click()
        ok(not pg.is_checked("#notifEvtAdminMessages"), f"{theme}: the switch turns off")
        pg.evaluate("saveNotificationConfig()")
        pg.wait_for_timeout(900)
        cfg = app.request.get(f"{BASE}/api/notifications/config").json()
        ok(cfg["notify_admin_messages"] is False, f"{theme}: saved as silenced")

        # 4. The next message is kept in the bell and counted as silenced in the console
        send(cp, f"Novedades ({theme})")
        cells = [c.strip() for c in cp.locator("#tblMessages tbody tr").first.locator("td.num").all_inner_texts()]
        ok(cells[1:] == [str(total), "0", "1"], f"{theme}: the console counts it as received, and silenced by one ({cells})")
        open_bell(pg)
        titles = pg.locator("#notificationsCenterModal .notice-row strong").all_inner_texts()
        ok(any("Novedades" in t for t in titles) and len(titles) >= 2, f"{theme}: the silenced message is still in the bell ({titles})")
        cp.click(".tab[data-tab='audit']")
        cp.wait_for_timeout(500)
        ok("Mensaje enviado a las cuentas" in cp.inner_text("#tblAudit"), f"{theme}: the action log records the sending")
        ok(not errs and not cerrs, f"{theme}: no JS errors (app {errs}, console {cerrs})")
        con.close()
        app.close()
    browser.close()
print("FAILURES: " + ("; ".join(fails) if fails else "none"))
sys.exit(1 if fails else 0)
