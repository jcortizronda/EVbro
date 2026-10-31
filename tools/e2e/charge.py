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
def drag(pg,frm,to):
    r=pg.eval_on_selector("#energyTargetSlider .energy-progress-track","e=>{const b=e.getBoundingClientRect();return [b.left,b.width,b.top+b.height/2]}")
    x=lambda v:r[0]+r[1]*v/100
    pg.mouse.move(x(frm),r[2]); pg.mouse.down(); pg.mouse.move(x((frm+to)/2),r[2]); pg.mouse.move(x(to)+3,r[2]); pg.mouse.up()
with sync_playwright() as p:
    b=p.chromium.launch()
    for theme in ("dark","light"):
        ctx=b.new_context(viewport={"width":390,"height":844},is_mobile=True,has_touch=True,locale="es-ES",service_workers="block")
        ctx.request.post(f"{BASE}/api/auth/login",headers={"Origin":BASE},data={"username":USER,"password":PASSWORD})
        ctx.add_init_script(f"localStorage.setItem('evbro_theme','{theme}')")
        ctx.add_init_script("new MutationObserver(() => { const b = document.querySelector('#appDialog.open #appDialogOk'); if (b) b.click(); }).observe(document, { attributes: true, subtree: true, attributeFilter: ['class'] })")   # the app's own confirm dialog is accepted
        pg=ctx.new_page(); errs=[]; reqs=[]; state={"plan":plan(),"fail":None}
        pg.on("pageerror",lambda e:errs.append(str(e))); pg.on("dialog",lambda d:(errs.append("diálogo del navegador: "+d.message),d.dismiss()))
        def handle(route, request):
            url=request.url
            if "/charge/" not in url: return route.continue_()
            path=url.split("/charge/")[1].split("?")[0]
            body=json.loads(request.post_data) if request.post_data else None
            if request.method!="GET": reqs.append((request.method,path,body))
            if request.method!="GET" and state["fail"]:
                return route.fulfill(status=422,content_type="application/json",body=json.dumps(state["fail"]))
            if request.method=="PUT" and path=="target": state["plan"]["target_pct"]=body["target_pct"]
            if request.method=="POST" and path=="stop": state["plan"].update(mode="hold",hold_reason="user",status="hold_user",hold_refresh_in_min=1100)
            if request.method=="POST" and path=="start": state["plan"].update(mode="free",hold_reason=None,status="free",hold_refresh_in_min=None)
            if request.method=="PUT" and path=="schedule":
                st=body["start"]; sm=int(st[:2])*60+int(st[3:]);
                dur=body.get("duration_min") or ((int(body["end"][:2])*60+int(body["end"][3:])-sm)%1440)
                e=(sm+dur)%1440
                state["plan"]["schedule"]={"enabled":True,"start":st,"end":f"{e//60:02d}:{e%60:02d}","duration_min":dur}
                state["plan"].update(mode="scheduled",status="scheduled"); state["plan"]["car_schedule"]=dict(state["plan"]["schedule"])
            if request.method=="DELETE": state["plan"]["schedule"]["enabled"]=False; state["plan"].update(mode="free",status="free")
            route.fulfill(status=200,content_type="application/json",body=json.dumps(state["plan"]))
        pg.route("**/api/vehicles/**", handle)
        pg.goto(BASE); pg.wait_for_load_state("networkidle"); pg.wait_for_timeout(900)
        pg.evaluate("t => { currentVin='LSJA8888888888888'; applyTelemetry(t); }", SAMPLE)
        pg.evaluate("switchTab('charges')"); pg.evaluate("ChargeControl.load()"); pg.wait_for_timeout(500)
        print("==",theme)
        txt=lambda i: pg.inner_text(i)
        ok(pg.get_attribute("#energyTargetSlider","aria-valuenow")=="100" and txt("#energyTargetLabel")=="Objetivo: 100 %" and "sin límite" not in pg.inner_text(".native-energy-panel"),"objetivo inicial: solo el porcentaje, sin texto «sin límite»")
        ok(txt("#chargeStatusLine")=="Carga libre" and True,"estado «Carga libre»")
        ok("is-current" in pg.get_attribute("#btnChargeStart","class"),"Iniciar es el estado actual")
        ok(pg.text_content("#scheduleBadge").strip()=="Sin programar" and pg.get_attribute("#scheduleStatus","data-state")=="off" and pg.is_hidden("#btnScheduleClear"),"programación: SIN PROGRAMAR, sin botón de quitar")
        if theme=="dark": pg.screenshot(path=f"{OUT}/energia-dark.png",full_page=True)
        else: pg.screenshot(path=f"{OUT}/energia-light.png",full_page=True)
        # objetivo
        drag(pg,100,80); pg.wait_for_timeout(400)
        toasts=pg.inner_text("#appToastContainer") if pg.query_selector("#appToastContainer") else ""
        ok("guardado en EVBro" in toasts and "enviado al coche" not in toasts,"cambiar el objetivo NO dice «enviado al coche»: «guardado en EVBro»")
        drag(pg,80,85); pg.wait_for_timeout(400)
        ok(reqs[-1]==("PUT","target",{"target_pct":85}),"el círculo admite 85 % (pasos de 5)")
        drag(pg,85,80); pg.wait_for_timeout(400)
        ok(reqs[-1]==("PUT","target",{"target_pct":80}),"desplegable → PUT target 80")
        ok("80%" in pg.get_attribute("#energyTargetSlider .energy-thumb","style").replace(" ","") and "Objetivo: 80 %" in txt("#energyTargetLabel"),"el círculo queda al 80 % sobre la barra")
        ok("Se pausará al llegar al 80" in txt("#chargeStatusDetail"),"explica que se pausará al 80 %")
        # parar / iniciar
        pg.click("#btnChargeStop"); pg.wait_for_timeout(500)
        ok(reqs[-1]==("POST","stop",None),"Parar → POST stop (tras confirmar)")
        ok(txt("#chargeStatusTitle")=="Carga en pausa" and "is-current" in pg.get_attribute("#btnChargeStop","class"),"muestra «Carga en pausa» y Parar queda como actual")
        ok("renueva la pausa solo" in txt("#chargeStatusDetail"),"avisa de que la pausa se renueva sola")
        pg.evaluate("document.querySelectorAll('.app-toast').forEach(t=>t.remove())")
        pg.click("#btnChargeStart"); pg.wait_for_timeout(500)
        ok(reqs[-1]==("POST","start",None),"Iniciar → POST start")
        # programar con fin
        pg.fill("#schedStart","23:30"); pg.fill("#schedEnd","07:30")
        ok("23:30 a 07:30 (8 h)" in txt("#scheduleSummary"),"resumen: de 23:30 a 07:30 (8 h) (cruza medianoche)")
        pg.click("#btnScheduleApply"); pg.wait_for_timeout(500)
        ok(reqs[-1]==("PUT","schedule",{"start":"23:30","end":"07:30"}),"Programar con hora de fin → PUT schedule {start,end}")
        ok(txt("#chargeStatusTitle")=="Carga programada" and "23:30 → 07:30" in txt("#scheduleMain") and pg.text_content("#scheduleBadge").strip()=="Activa" and pg.get_attribute("#scheduleStatus","data-state")=="on" and pg.is_visible("#btnScheduleClear") and txt("#btnScheduleApply")=="Cambiar programación","estado «Carga programada»: bloque ACTIVA con 23:30 → 07:30, botón Quitar visible")
        # programar con duración
        pg.evaluate("document.querySelectorAll('.app-toast').forEach(t=>t.remove())")
        pg.check('input[name="schedMode"][value="duration"]')
        ok(pg.is_hidden("#schedEndRow") and pg.is_visible("#schedDurRow"),"modo duración: se oculta la hora de fin y salen horas/minutos")
        pg.fill("#schedStart","01:00"); pg.fill("#schedHours","5"); pg.select_option("#schedMinutes","30")
        ok("01:00 a 06:30 (5 h 30 min)" in txt("#scheduleSummary"),"resumen con duración: 01:00 a 06:30 (5 h 30 min)")
        pg.click("#btnScheduleApply"); pg.wait_for_timeout(500)
        ok(reqs[-1]==("PUT","schedule",{"start":"01:00","duration_min":330}),"Programar con duración → PUT schedule {start,duration_min:330}")
        pg.fill("#schedHours","0"); pg.select_option("#schedMinutes","30")
        ok("al menos 1 hora" in txt("#scheduleSummary"),"menos de 1 h: avisa antes de enviar")
        pg.evaluate("document.querySelectorAll('.app-toast').forEach(t=>t.remove())")
        pg.click("#btnScheduleClear"); pg.wait_for_timeout(500)
        ok(reqs[-1]==("DELETE","schedule",None),"Quitar programación → DELETE schedule")
        # error del coche
        state["fail"]={"success":False,"detail":"Falta el PIN de la cuenta Ebro","reason":"pin"}
        pg.evaluate("document.querySelectorAll('.app-toast').forEach(t=>t.remove())")
        pg.click("#btnChargeStop"); pg.wait_for_timeout(600)
        ok("Falta el PIN" in pg.inner_text("#appToastContainer") and "Ajustes" in pg.inner_text("#appToastContainer"),"error del servidor: aviso con el motivo y a dónde ir")
        ok(pg.evaluate("document.documentElement.scrollWidth-document.documentElement.clientWidth")==0,"sin desborde horizontal")
        ok(not errs,"sin errores JS "+str(errs[:2]))
        ctx.close()
    b.close()
print("\nFALLOS:",fails if fails else "ninguno"); sys.exit(1 if fails else 0)
