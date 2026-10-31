"""Consola de administración (otro puerto, interfaz de escritorio): acceso, gestores, invitaciones, límites, que el administrador no vea los
coches, las dos puertas separadas y los ajustes del servidor. Ver tools/e2e/README.md."""
import os
import sys
import tempfile
from playwright.sync_api import sync_playwright

BASE = os.environ.get("EVBRO_URL", "http://127.0.0.1:8099")
CONSOLE = os.environ.get("EVBRO_ADMIN_URL", "http://127.0.0.1:8098")
ADMIN, ADMIN_PW = "admin", "contraseña-del-admin-1"
OUT = sys.argv[1] if len(sys.argv) > 1 else tempfile.mkdtemp(prefix="evbro-e2e-")
MGR, MGR_PW = "maria", "contraseña-de-maria-1"
fails = []


def ok(c, m):
    print(("  ✔ " if c else "  ✘ ") + m)
    if not c:
        fails.append(m)


OBSERVER = """new MutationObserver(() => {
  const dlg = document.getElementById('dlgAsk');
  if (!dlg || !dlg.open || dlg.dataset.answered === '1') return;
  dlg.dataset.answered = '1';
  const field = document.getElementById('askField');
  if (!field.hidden) document.getElementById('askInput').value = (window.__answers || []).shift() || '';
  setTimeout(() => { document.getElementById('askOk').click(); delete dlg.dataset.answered; }, 20);
}).observe(document, { attributes: true, subtree: true, attributeFilter: ['open'] })"""


def answers_for(pg, queue):
    """The console's own dialog (ask()) is accepted; a text field takes the next value of `window.__answers`. A browser dialog is a failure."""
    pg.add_init_script(OBSERVER)
    pg.on("dialog", lambda d: (queue.append("diálogo del navegador: " + d.message), d.dismiss()))


with sync_playwright() as p:
    b = p.chromium.launch()
    for theme in ("dark", "light"):
        tag = theme
        ctx = b.new_context(viewport={"width": 1280, "height": 800}, color_scheme=theme)
        pg = ctx.new_page()
        errs, queue = [], []
        pg.on("pageerror", lambda e: errs.append(str(e)))
        pg.on("console", lambda m: errs.append(m.text) if m.type == "error" and "Failed to load resource" not in m.text else None)
        answers_for(pg, queue)

        # acceso
        pg.goto(CONSOLE)
        pg.wait_for_selector("#viewAuth:not([hidden])")
        ok(pg.text_content("#authTitle").strip() == "Iniciar sesión", f"{tag}: la consola pide iniciar sesión (el administrador ya existe)")
        ok(pg.is_hidden("#rowCode"), f"{tag}: no pide el código de configuración si ya hay administrador")
        pg.fill("#authUser", ADMIN)
        pg.fill("#authPass", "x" * 12)
        pg.click("#authSubmit")
        pg.wait_for_timeout(600)
        ok(pg.is_visible("#authError"), f"{tag}: una contraseña incorrecta se rechaza")
        pg.fill("#authPass", ADMIN_PW)
        pg.click("#authSubmit")
        pg.wait_for_selector("#viewApp:not([hidden])")
        ok(pg.text_content("#whoName").strip() == ADMIN, f"{tag}: el administrador entra en la consola")
        pg.screenshot(path=f"{OUT}/admin-managers-{theme}.png")

        # el gestor demo (juan) ya existe: se ve su recuento pero no su coche
        pg.wait_for_selector("#tblManagers tbody tr")
        rows = pg.eval_on_selector_all("#tblManagers tbody tr", "e=>e.map(r=>r.innerText)")
        ok(any("juan" in r and "1 /" in r for r in rows), f"{tag}: el administrador ve al gestor de la demo con su nº de coches")
        ok("Ebro Demo" not in pg.content() and "LSJA8888888888888" not in pg.content(), f"{tag}: pero ni el nombre ni el VIN de su coche")

        # crear gestor
        pg.click("#btnNewManager")
        pg.fill("#mgrUser", MGR)
        pg.fill("#mgrPass", MGR_PW)
        pg.fill("#mgrVeh", "1")
        pg.fill("#mgrSto", "50")
        pg.click("#formManager button[type=submit]")
        pg.wait_for_timeout(900)
        rows = pg.eval_on_selector_all("#tblManagers tbody tr", "e=>e.map(r=>r.innerText)")
        ok(any(MGR in r and "0 / 1" in r for r in rows), f"{tag}: se crea el gestor con su límite de 1 coche")
        heads = pg.eval_on_selector_all("#tblManagers thead th", "e=>e.map(x=>x.textContent)")
        ok("Espacio" in heads, f"{tag}: la tabla de gestores tiene la columna «Espacio»")
        ok(any(MGR in r and "0 B / 50 MB" in r for r in rows), f"{tag}: el espacio del gestor nuevo es «0 B / 50 MB»")
        ok(any("juan" in r and "/ 50 MB" in r for r in rows), f"{tag}: el gestor de la demo tiene el espacio por defecto (50 MB)")

        # el botón de cerrar sesión no queda pegado al de cambiar contraseña
        gap = pg.evaluate("""() => { const a = document.getElementById('btnPassword').getBoundingClientRect(), b = document.getElementById('btnLogout').getBoundingClientRect(); return b.left - a.right; }""")
        ok(gap >= 24, f"{tag}: «Cerrar sesión» queda separado de «Cambiar contraseña» ({gap:.0f} px)")

        # editar límites en un diálogo (coches, cuentas asociadas, caducidad, solo lectura)
        pg.click(f"#tblManagers tr:has-text('{MGR}') >> text=Límites")
        pg.wait_for_selector("#dlgLimits[open]")
        ok(pg.input_value("#limVeh") == "1" and pg.input_value("#limMem") == "5", f"{tag}: el diálogo de límites arranca con los valores actuales")
        ok(pg.input_value("#limSto") == "50", f"{tag}: el diálogo trae el campo «Espacio (MB)» con su valor")
        pg.fill("#limSto", "-1")
        pg.fill("#limVeh", "3")
        pg.fill("#limMem", "-1")
        pg.fill("#limExp", "2030-01-01")
        pg.screenshot(path=f"{OUT}/admin-limits-{theme}.png")
        pg.click("#formLimits button[type=submit]")
        pg.wait_for_timeout(900)
        row = pg.text_content(f"#tblManagers tr:has-text('{MGR}')")
        ok("0 / 3" in row and "0 / ∞" in row and "2030" in row, f"{tag}: se cambian los límites y la caducidad ({' '.join(row.split())[:90]})")
        ok("sin límite" in row, f"{tag}: el espacio pasa a «sin límite»")

        # el gestor entra en la app; el administrador NO
        actx = b.new_context(viewport={"width": 390, "height": 844}, is_mobile=True)
        app = actx.new_page()
        app.goto(BASE)
        app.wait_for_selector("#authOverlay:not([hidden])")
        app.fill("#authUsername", ADMIN)
        app.fill("#authPassword", ADMIN_PW)
        app.click("#authSubmit")
        app.wait_for_timeout(800)
        ok(app.is_visible("#authError"), f"{tag}: el administrador no puede entrar en la app")
        app.fill("#authUsername", MGR)
        app.fill("#authPassword", MGR_PW)
        app.click("#authSubmit")
        app.wait_for_load_state("load")
        app.wait_for_timeout(2500)
        ok(app.evaluate("window.accountInfo.role") == "manager" and app.evaluate("window.accountInfo.vehicleLimit") == 3, f"{tag}: la gestora entra en la app con sus límites")
        # añade un coche manual: el administrador solo ve el recuento
        app.evaluate("fetch('/api/vehicles',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({name:'Coche secreto de María'})})")
        app.wait_for_timeout(700)
        pg.click("#btnLogout")
        pg.wait_for_selector("#viewAuth:not([hidden])")
        pg.fill("#authUser", ADMIN)
        pg.fill("#authPass", ADMIN_PW)
        pg.click("#authSubmit")
        pg.wait_for_selector("#viewApp:not([hidden])")
        pg.wait_for_timeout(600)
        row = pg.text_content(f"#tblManagers tr:has-text('{MGR}')")
        ok("1 / 3" in row and "Coche secreto" not in pg.content(), f"{tag}: el administrador ve «1 / 3» coches de la gestora, no su nombre")

        # estadísticas
        pg.click("[data-tab=stats]")
        pg.wait_for_timeout(900)
        kpis = pg.text_content("#kpiMain")
        ok("Cuentas de usuario" in kpis and "Coches" in kpis and "Base de datos" in kpis, f"{tag}: la pestaña Estadísticas muestra los totales")
        ok(pg.locator("#chartGrowth rect").count() == 8, f"{tag}: el gráfico de altas por semana tiene 8 barras")
        usage = pg.text_content("#tblUsage")
        ok(MGR in usage and "juan" in usage, f"{tag}: el uso por gestor lista a los gestores")
        ok("Ebro Demo" not in pg.content() and "Coche secreto" not in pg.content() and "LSJA8888888888888" not in pg.content(), f"{tag}: las estadísticas no enseñan ningún coche")
        pg.screenshot(path=f"{OUT}/admin-stats-{theme}.png", full_page=True)

        # invitaciones
        pg.click("[data-tab=invites]")
        pg.fill("#invNote", "Beto (beta)")
        pg.fill("#invSto", "25")
        pg.click("#formInvite button[type=submit]")
        pg.wait_for_timeout(900)
        token = pg.text_content("#tokenValue").strip()
        ok(token.startswith("ebm_") and pg.is_visible("#tokenBox"), f"{tag}: se muestra el código una vez")
        ok("Beto (beta)" in pg.text_content("#tblInvites") and "Activa" in pg.text_content("#tblInvites"), f"{tag}: la invitación aparece como activa")
        # se registra desde un contexto limpio (la sesión de la gestora sigue abierta en otro)
        rctx = b.new_context(viewport={"width": 390, "height": 844}, is_mobile=True)
        rp = rctx.new_page()
        rp.goto(BASE)
        rp.wait_for_selector("#authOverlay:not([hidden])")
        rp.click("#authSwitch")
        rp.fill("#authInviteCode", token)
        rp.fill("#authUsername", "beto")
        rp.fill("#authPassword", "contraseña-de-beto-1")
        rp.fill("#authPassword2", "contraseña-de-beto-1")
        rp.click("#authSubmit")
        rp.wait_for_timeout(400)
        ok(rp.is_visible("#authError") and "casilla" in rp.inner_text("#authError"), f"{tag}: sin aceptar la información sobre los datos no se crea la cuenta")
        rp.check("#authConsent")
        rp.click("#authSubmit")
        rp.wait_for_load_state("load")
        rp.wait_for_timeout(2500)
        ok(rp.evaluate("window.accountInfo.role") == "manager", f"{tag}: Beto se registra en la app con el código")
        beto_storage = rp.evaluate("fetch('/api/account/storage').then(r => r.json())")
        ok(beto_storage == {"used_bytes": 0, "limit_mb": 25}, f"{tag}: Beto empieza con el espacio de la invitación ({beto_storage})")
        pg.reload()
        pg.wait_for_selector("#viewApp:not([hidden])")
        pg.click("[data-tab=invites]")
        pg.wait_for_timeout(500)
        ok("Usada" in pg.text_content("#tblInvites") and "beto" in pg.text_content("#tblInvites"), f"{tag}: la invitación queda usada por beto")
        pg.screenshot(path=f"{OUT}/admin-invites-{theme}.png")

        # servidor
        pg.click("[data-tab=server]")
        pg.wait_for_timeout(500)
        ok(pg.input_value("#retSnap") == "365" and pg.input_value("#polParked") != "", f"{tag}: la pestaña Servidor carga retención y sondeo")
        pg.fill("#retSnap", "200")
        pg.click("#formRetention button[type=submit]")
        pg.wait_for_timeout(800)
        pg.reload()
        pg.wait_for_selector("#viewApp:not([hidden])")
        pg.click("[data-tab=server]")
        pg.wait_for_timeout(500)
        ok(pg.input_value("#retSnap") == "200", f"{tag}: la retención se guarda")
        pg.screenshot(path=f"{OUT}/admin-server-{theme}.png")

        # borrar el espacio de María (prompt: confirmar con el nombre)
        pg.click("[data-tab=managers]")
        pg.evaluate(f"window.__answers = ['{MGR}']")
        pg.click(f"#tblManagers tr:has-text('{MGR}') >> text=Borrar espacio")
        pg.wait_for_timeout(1000)
        ok(MGR not in pg.text_content("#tblManagers"), f"{tag}: el espacio de la gestora se borra")
        ok(app.evaluate("fetch('/api/vehicles').then(r=>r.status)") == 401, f"{tag}: y su sesión en la app deja de valer")

        # cabeceras y aislamiento de puertas
        r = ctx.request.get(CONSOLE + "/")
        ok("default-src 'self'" in r.headers.get("content-security-policy", ""), f"{tag}: la consola lleva una política CSP estricta")
        ok(ctx.request.get(BASE + "/api/admin/users").status in (401, 404), f"{tag}: las rutas de administración no están en el puerto de la app")
        errs.extend(queue)                                    # a browser dialog (alert/confirm/prompt) is a failure
        ok(not errs, f"{tag}: sin errores JS ni de CSP {errs[:2]}")
        for c in (ctx, actx, rctx):
            c.close()
        if theme == "dark":
            import subprocess
            subprocess.run([os.path.join(os.path.dirname(__file__), "demo_server.sh"), "start"], check=True, capture_output=True)
    b.close()
print("FALLOS:", fails if fails else "ninguno")
sys.exit(1 if fails else 0)
