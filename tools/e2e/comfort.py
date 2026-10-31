"""Prueba de navegador (Playwright) contra un servidor de demostración: ver tools/e2e/README.md."""
import os, sys, tempfile  # noqa: E401
import json
from playwright.sync_api import sync_playwright
BASE=os.environ.get("EVBRO_URL","http://127.0.0.1:8099"); OUT=sys.argv[1] if len(sys.argv)>1 else tempfile.mkdtemp(prefix="evbro-e2e-")
USER=os.environ.get("EVBRO_USER","juan"); PASSWORD=os.environ.get("EVBRO_PASSWORD","una-contraseña-larga-1")
SAMPLE=json.load(open(os.path.join(os.path.dirname(__file__),"fixtures","telemetry_sample.json")))
fails=[]
def ok(c,m):
    print(("  ✔ " if c else "  ✘ ")+m)
    if not c: fails.append(m)
with sync_playwright() as p:
    b=p.chromium.launch()
    for theme in ("dark","light"):
        ctx=b.new_context(viewport={"width":390,"height":844},is_mobile=True,has_touch=True,locale="es-ES",service_workers="block")
        ctx.request.post(f"{BASE}/api/auth/login",headers={"Origin":BASE},data={"username":USER,"password":PASSWORD})
        ctx.add_init_script(f"localStorage.setItem('evbro_theme','{theme}')")
        pg=ctx.new_page(); errs=[]
        pg.on("pageerror",lambda e:errs.append(str(e)))
        sent=[]; mode={"ok":True}
        def handle(route, request):
            if request.method=="POST" and "/commands" in request.url:
                sent.append(json.loads(request.post_data))
                if mode["ok"]: route.fulfill(status=200,content_type="application/json",body=json.dumps({"success":True,"message":"ok"}))
                else: route.fulfill(status=422,content_type="application/json",body=json.dumps({"success":False,"detail":"rechazado","reason":"config"}))
            elif request.method=="GET" and request.url.endswith("/alarm"):
                route.fulfill(status=200,content_type="application/json",body=json.dumps({"enabled":True}))
            elif request.method=="POST" and "/poll" in request.url:
                route.fulfill(status=200,content_type="application/json",body="{}")
            else: route.continue_()
        pg.route("**/api/vehicles/**", handle)
        pg.goto(BASE); pg.wait_for_load_state("networkidle"); pg.wait_for_timeout(1000)
        pg.evaluate("t => { currentVin = 'LSJA8888888888888'; applyTelemetry(t); }", SAMPLE)
        pg.evaluate("switchTab('climate')"); pg.wait_for_timeout(500)
        st=lambda i: pg.inner_text(f'[data-comfort-id="{i}"] .comfort-state')
        key=lambda i: pg.get_attribute(f'[data-comfort-id="{i}"] .comfort-state',"data-state")
        print(f"== {theme}")
        ok(st("windshield")=="OFF" and st("rear")=="ON" and st("wheel")=="ON","estado inicial: parabrisas OFF, luneta ON, volante ON")
        ok(st("seat_driver_heat")=="ON" and st("seat_passenger_heat")=="OFF","asientos: conductor calor ON, copiloto OFF")
        ok(st("seat_rear_left_heat")=="ON · 2","asiento trasero izq. muestra el nivel (ON · 2)")
        ok(pg.inner_text("#climateStateBadge")=="ON" and pg.get_attribute("#btnClimatePowerToggle","aria-pressed")=="true","climatizador ON (insignia y botón)")
        ok(pg.inner_text("#climateStatusBadge").lower().endswith("activas"),"cabecera resume: "+pg.inner_text("#climateStatusBadge"))
        if theme=="dark": pg.screenshot(path=f"{OUT}/confort-dark.png", full_page=True)
        else: pg.screenshot(path=f"{OUT}/confort-light.png", full_page=True)
        # toque: parabrisas OFF → enciende
        pg.click('[data-comfort-id="windshield"]'); pg.wait_for_timeout(500)
        ok(sent and sent[-1]["command"]=="defrost_parabrisas","OFF → manda defrost_parabrisas (encender)")
        ok(key("windshield")=="on-waiting" and st("windshield")=="ON","muestra ON esperando confirmación")
        pg.evaluate("t => { t.raw_data.frontWindshieldHeat='1'; t.front_windshield_heat=true; applyTelemetry(t); }", SAMPLE)
        pg.wait_for_timeout(200); ok(key("windshield")=="on","al llegar la telemetría queda confirmado")
        # toque: volante ON → apaga
        pg.click('[data-comfort-id="wheel"]'); pg.wait_for_timeout(500)
        ok(sent[-1]["command"]=="volante_caliente_off","ON → manda volante_caliente_off (apagar)")
        # ventilación asiento conductor
        pg.click('[data-comfort-id="seat_driver_vent"]'); pg.wait_for_timeout(400)
        ok(sent[-1]["command"]=="asiento_conductor_ventilacion","ventilación conductor → asiento_conductor_ventilacion")
        pg.click('[data-comfort-id="seat_rear_right_heat"]'); pg.wait_for_timeout(400)
        ok(sent[-1]["command"]=="asiento_tras_der_caliente","trasero der. calor → asiento_tras_der_caliente")
        # fallo: no cambia el estado
        mode["ok"]=False
        before=st("rear")
        pg.click('[data-comfort-id="rear"]'); pg.wait_for_timeout(600)
        ok(st("rear")==before and key("rear")=="on","si el servidor rechaza la orden, el estado NO cambia")
        mode["ok"]=True
        # preajuste
        pg.click('.comfort-chip:has-text("Fresco")'); pg.wait_for_timeout(300)
        ok(sent[-1]["command"]=="clima_on" and sent[-1]["params"]=={"temperature":"16.0"},"Fresco → clima_on a 16.0 (mínimo del climatizador)")
        pg.click('.comfort-chip:has-text("Cálido")'); pg.wait_for_timeout(300)
        ok(sent[-1]["params"]=={"temperature":"30.0"},"Cálido → clima_on a 30.0 (máximo)")
        ok(pg.inner_text("#comfortPresetLabel").startswith("Encender"),"etiqueta de los preajustes")
        pg.click('.comfort-chip:has-text("Confort")'); pg.wait_for_timeout(400)
        ok(sent[-1]["command"]=="clima_on" and sent[-1]["params"]=={"temperature":"22.0"},"preajuste Confort → clima_on con temperature 22.0")
        # deslizador con el clima encendido → aplica
        n=len(sent)
        pg.evaluate("() => { const s=document.getElementById('climateSlider'); s.value='19'; s.dispatchEvent(new Event('input', { bubbles: true })); s.dispatchEvent(new Event('change', { bubbles: true })); }"); pg.wait_for_timeout(400)
        ok(len(sent)==n+1 and sent[-1]["params"]=={"temperature":"19.0"},"deslizador con el clima ON → clima_on a 19.0")
        # clima: apagar
        pg.click('#btnClimatePowerToggle'); pg.wait_for_timeout(400)
        ok(sent[-1]["command"]=="clima_off","botón del climatizador ON → clima_off")

        # ───── sin imágenes y cabeceras compactas ─────
        ok(pg.evaluate("document.querySelectorAll('.native-hero-banner, .hero-img').length")==0,"no quedan banners con imagen")
        ok(pg.evaluate("[...document.images].filter(i=>i.src.includes('/static/images/')).length")==0,"ninguna imagen de /static/images en la página")
        ok(pg.locator('#comfortDefrost .comfort-icon svg').count()==3,"los 3 botones de desempañado/volante llevan icono SVG")
        pg.evaluate("switchTab('overview')"); pg.wait_for_timeout(300)
        ok(pg.is_visible("#vehicleName") and pg.is_visible("#vehicleVin") and pg.is_visible("#vehicleStatusBadge"),"cabecera de la portada: nombre, VIN y estado")
        # ───── portada: alarma con estado real y PIN ─────
        pg.evaluate("refreshAlarm()"); pg.wait_for_timeout(400)
        ok(st("alarm")=="ON","la alarma muestra el estado leído del coche (ON)")
        n=len(sent)
        pg.click('#btnAlarmCard'); pg.wait_for_selector('#pinModal.open',timeout=3000)
        ok(len(sent)==n,"desactivar la alarma abre el PIN y no envía nada todavía")
        pg.fill('#pinInput','1234'); pg.click('#btnConfirmCommand'); pg.wait_for_timeout(600)
        ok(sent[-1]["command"]=="alarma_off" and sent[-1]["pin"]=="1234","con el PIN envía alarma_off")
        ok(st("alarm")=="OFF" and key("alarm")=="off-waiting","tras el PIN la alarma queda OFF esperando confirmación (evento de orden aceptada)")
        # ───── la portada: iconos que cambian de forma y de color con el estado ─────
        pg.evaluate("switchTab('overview')")
        pg.evaluate("t => { const u=JSON.parse(JSON.stringify(t)); u.is_locked=false; u.doors.trunk=1; u.windows.fl=1; u.windows.sunroof=1; applyTelemetry(u); }", SAMPLE)
        pg.wait_for_timeout(200)
        col=lambda i: pg.evaluate(f"getComputedStyle(document.querySelector('#{i} .card-icon-wrap')).color")
        bg=lambda i: pg.evaluate(f"getComputedStyle(document.getElementById('{i}')).backgroundColor")
        abiertos={i: pg.get_attribute("#"+i,"data-state") for i in ("btnLockAction","btnTrunkCard","btnWindowsCard","btnSunroofCard")}
        ok(set(abiertos.values())=={"open"},"cierre desbloqueado, maletero, ventanilla y techo abiertos → las 4 fichas en «open»")
        pg.mouse.move(0,0); pg.wait_for_timeout(300)
        html_abierto={i: pg.inner_html(f"#{i} .card-icon-wrap") for i in abiertos}
        bg_abierto={i: bg(i) for i in abiertos}; col_abierto={i: col(i) for i in abiertos}
        pg.evaluate("t => applyTelemetry(t)", SAMPLE); pg.mouse.move(0,0); pg.wait_for_timeout(300)
        ok(all(pg.get_attribute("#"+i,"data-state")=="closed" for i in abiertos),"con todo cerrado → «closed»")
        ok(all(pg.inner_html(f"#{i} .card-icon-wrap")!=html_abierto[i] for i in abiertos),"el ICONO cambia de forma al cambiar el estado")
        ok(all(col(i)!=col_abierto[i] for i in abiertos),"el ICONO cambia de color (abierto ámbar / cerrado verde)")
        ok(all(bg(i)==bg_abierto[i] for i in abiertos),"el fondo del BOTÓN no cambia")
        ok(pg.locator('text=Ventilar').count()==0 or not pg.locator('#tabOverview >> text=Ventilar').is_visible(),"ya no hay ficha «Ventilar» en la portada")
        # ───── sin datos: no se afirma «cerrado» ─────
        pg.evaluate("switchTab('overview')")
        pg.evaluate("t => { const u=JSON.parse(JSON.stringify(t)); u.is_locked=null; for (const k in u.doors) u.doors[k]=null; for (const k in u.windows) u.windows[k]=null; applyTelemetry(u); }", SAMPLE)
        pg.wait_for_timeout(200)
        ok(pg.get_attribute("#btnLockAction","data-state")=="unknown" and pg.get_attribute("#btnTrunkCard","data-state")=="unknown","sin datos: las fichas quedan en estado «unknown» (icono gris)")
        ok(pg.inner_text("#cardLockStatus")=="Sin datos" and pg.inner_text("#cardTrunkStatus")=="Sin datos" and pg.inner_text("#cardWindowsStatus")=="Sin datos" and pg.inner_text("#cardSunroofStatus")=="Sin datos","sin datos: cierre, maletero, ventanas y techo dicen «Sin datos»")
        pg.click('#btnLockAction'); pg.wait_for_timeout(500)
        ok("Cierre centralizado" in pg.inner_text("body") and pg.locator('.action-sheet-btn').count()>=2,"con el cierre desconocido se deja elegir (hoja de acciones), no se adivina")
        pg.evaluate("closeActionSheet()")
        pg.evaluate("switchTab('climate')"); pg.wait_for_timeout(300)
        # catálogo «qué envía»
        rows=pg.locator('.comfort-row').count()
        ov=pg.evaluate("document.documentElement.scrollWidth-document.documentElement.clientWidth")
        ok(ov==0,f"sin desborde horizontal ({ov}px)")
        ok(not errs,"sin errores JS "+str(errs[:2]))
        ctx.close()
    b.close()
print("\nFALLOS:",fails if fails else "ninguno"); sys.exit(1 if fails else 0)
