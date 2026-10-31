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
def plan(**kw):
    base={"target_pct":100,"target_choices":[50,60,70,80,90,100],"mode":"free","hold_reason":None,"status":"free",
          "schedule":{"enabled":False,"start":None,"end":None,"duration_min":None},"soc":65,"plugged":True,"charging":False,
          "last_error":None,"hold_refresh_in_min":None,"timezone":"Europe/Madrid","car_schedule":{"enabled":False,"start":None,"end":None,"duration_min":None}}
    base.update(kw); return base
with sync_playwright() as p:
    b=p.chromium.launch()
    for theme in ("dark","light"):
        ctx=b.new_context(viewport={"width":390,"height":844},is_mobile=True,has_touch=True,locale="es-ES",service_workers="block")
        ctx.request.post(f"{BASE}/api/auth/login",headers={"Origin":BASE},data={"username":USER,"password":PASSWORD})
        ctx.add_init_script(f"localStorage.setItem('evbro_theme','{theme}')")
        pg=ctx.new_page(); errs=[]; cur={"plan":plan()}
        pg.on("pageerror",lambda e:errs.append(str(e)))
        pg.route("**/charge/plan**", lambda r: r.fulfill(status=200,content_type="application/json",body=json.dumps(cur["plan"])))
        pg.goto(BASE); pg.wait_for_load_state("networkidle"); pg.wait_for_timeout(900)
        print("==",theme)
        def tel(**over):
            t=json.loads(json.dumps(SAMPLE)); 
            for k,v in over.items(): 
                if isinstance(v,dict): t.setdefault(k,{}).update(v)
                else: t[k]=v
            pg.evaluate("t => { currentVin='LSJA8888888888888'; applyTelemetry(t); }", t); pg.wait_for_timeout(150)
        txt=lambda i: pg.text_content(i).strip()
        tel(is_locked=True, doors={"trunk":0}, windows={"fl":0,"fr":0,"rl":0,"rr":0,"sunroof":0})
        ok((txt("#cardLockAction"),txt("#cardLockStatus"))==("Cierre","Cerrado"),"cierre: título «Cierre», estado «Cerrado»")
        ok(txt("#cardTrunkStatus")=="Cerrado" and txt("#cardSunroofStatus")=="Cerrado" and txt("#cardWindowsStatus")=="Cerradas","maletero/techo «Cerrado», ventanillas «Cerradas»")
        pg.screenshot(path=f"{OUT}/inicio-cerrado-{theme}.png")
        # figura del coche: compacta, y cada parte con el estado que dijo el coche
        box=pg.evaluate("(() => { const r=document.getElementById('carFigure').getBoundingClientRect(); return [r.width, r.height]; })()")
        ok(box[1] <= 170 and box[0] <= 390, f"la figura del coche es compacta ({box[0]:.0f}×{box[1]:.0f} px)")
        st=lambda part: pg.get_attribute(f'#carFigure [data-part="{part}"]','data-state')
        ok(st("trunk")=="closed" and st("sunroof")=="closed" and st("win-fl")=="closed" and st("lock")=="locked", "figura: maletero, techo y ventanilla cerrados; cierre bloqueado")
        tel(doors={"hood":None,"fl":None})
        ok(st("hood")=="unknown" and st("door-fl")=="unknown" and st("trunk")=="closed", "figura: capó y puertas sin dato quedan en «sin datos», no en cerrado")
        tel(is_locked=False, doors={"trunk":1}, windows={"fl":1,"sunroof":1})
        ok(txt("#cardLockStatus")=="Abierto" and txt("#cardTrunkStatus")=="Abierto" and txt("#cardSunroofStatus")=="Abierto","cierre/maletero/techo «Abierto»")
        ok(st("trunk")=="open" and st("win-fl")=="open" and st("lock")=="unlocked" and txt("#carFigureSummary")=="Maletero · Techo · Ventanilla del. izq.", f"figura: abiertos en ámbar y resumen ({txt('#carFigureSummary')})")
        tel(doors={"fl":0,"fr":0,"rl":0,"rr":0,"hood":0,"trunk":0}, windows={"fl":0,"sunroof":0}, tyre_pressures={"fl":2.4,"fr":2.4,"rl":2.3,"rr":1.8}, tyre_warnings={"rr":True})
        ok(txt("#carFigureSummary")=="Todo cerrado" and pg.text_content("#cfTyreRR")=="1.8" and pg.get_attribute('#carFigure [data-wheel="rr"]','data-state')=="warn", "figura: «Todo cerrado» y la rueda avisada en rojo con su presión")
        pg.screenshot(path=f"{OUT}/figura-{theme}.png")
        tel(is_locked=False, doors={"trunk":1}, windows={"fl":1,"sunroof":1})
        ok(txt("#cardWindowsStatus")=="1 abierta","1 ventanilla → «1 abierta»")
        tel(windows={"fl":1,"fr":1,"rl":1,"rr":1})
        ok(txt("#cardWindowsStatus")=="4 abiertas","4 ventanillas → «4 abiertas»")
        tel(windows={"fl":1,"fr":1,"rl":0,"rr":0}); ok(txt("#cardWindowsStatus")=="2 abiertas","2 → «2 abiertas»")
        pg.evaluate("openWindowsActionSheet()"); pg.wait_for_timeout(300)
        ok("2 abiertas" in pg.inner_text("body"),"la hoja de ventanillas dice cuántas hay abiertas")
        pg.evaluate("closeActionSheet()")
        pg.screenshot(path=f"{OUT}/inicio-abierto-{theme}.png")
        # modelo
        pg.evaluate("allVehiclesList=[{vin:currentVin,name:'Mi Ebro',model:'S800 PHEV',license_plate:'1234'}]; openVehicleSettings()"); pg.wait_for_timeout(200)
        opts=pg.eval_on_selector_all("#vpModel option","els=>els.map(e=>e.value)")
        ok(opts==["S700 PHEV","S800 PHEV","S900 PHEV"],f"desplegable de modelo: {opts}")
        ok(pg.input_value("#vpModel")=="S800 PHEV","selecciona el modelo del coche")
        pg.evaluate("allVehiclesList=[{vin:currentVin,name:'x',model:'Ebro Auto'}]; openVehicleSettings()")
        ok(pg.eval_on_selector_all("#vpModel option","e=>e.length")==4 and pg.input_value("#vpModel")=="Ebro Auto","un valor antiguo se conserva como opción extra (no se pierde)")
        pg.screenshot(path=f"{OUT}/modelo-{theme}.png")
        pg.evaluate("allVehiclesList=[{vin:currentVin,name:'x',model:'S700 PHEV'}]; openVehicleSettings()")
        ok(pg.eval_on_selector_all("#vpModel option","e=>e.length")==3,"la opción extra se retira al abrir con un modelo de la lista")
        # programación
        pg.evaluate("switchTab('charges')")
        def show(**kw):
            cur["plan"]=plan(**kw); pg.evaluate("ChargeControl.load()"); pg.wait_for_timeout(350)
        show()
        ok((txt("#scheduleBadge"),pg.get_attribute("#scheduleStatus","data-state"))==("Sin programar","off"),"sin programación → SIN PROGRAMAR")
        pg.screenshot(path=f"{OUT}/prog-off-{theme}.png",full_page=True)
        sc={"enabled":True,"start":"22:00","end":"06:00","duration_min":480}
        show(mode="scheduled",status="scheduled",schedule=sc,car_schedule=sc)
        ok(txt("#scheduleBadge")=="Activa" and txt("#scheduleMain")=="22:00 → 06:00" and "8 h" in txt("#scheduleSub"),"programada → ACTIVA 22:00 → 06:00 · 8 h")
        ok(pg.input_value("#schedStart")=="22:00" and pg.input_value("#schedEnd")=="06:00","el formulario se rellena con la programación vigente")
        pg.screenshot(path=f"{OUT}/prog-on-{theme}.png",full_page=True)
        show(mode="hold",hold_reason="user",status="hold_user",hold_refresh_in_min=1000,car_schedule={"enabled":True,"start":"20:30","end":"21:30","duration_min":60})
        ok(txt("#scheduleBadge")=="En pausa" and "20:30" not in txt("#scheduleMain"),"pausa → EN PAUSA y no enseña la ventana interna")
        pg.screenshot(path=f"{OUT}/prog-pausa-{theme}.png",full_page=True)
        show(car_schedule={"enabled":True,"start":"01:00","end":"05:00","duration_min":240})
        ok(txt("#scheduleBadge")=="Activa en el coche" and txt("#scheduleMain")=="01:00 → 05:00" and pg.is_visible("#btnScheduleClear"),"programada fuera de EVBro → ACTIVA EN EL COCHE")
        ok(not errs,f"sin errores JS {errs[:2]}")
        ctx.close()
    b.close()
print("FALLOS:",fails if fails else "ninguno"); sys.exit(1 if fails else 0)
