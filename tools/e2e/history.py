"""Prueba de navegador (Playwright) contra un servidor de demostración: ver tools/e2e/README.md.

Historial de viajes y de cargas en filas: se despliegan al tocarlas, separadores de mes con totales y «Ver más» por páginas.
Las respuestas de la API se simulan para tener un historial largo (65 viajes, 40 cargas).
"""
import os, sys, tempfile  # noqa: E401
import json
from urllib.parse import urlparse, parse_qs
from playwright.sync_api import sync_playwright
BASE=os.environ.get("EVBRO_URL","http://127.0.0.1:8099"); OUT=sys.argv[1] if len(sys.argv)>1 else tempfile.mkdtemp(prefix="evbro-e2e-")
USER=os.environ.get("EVBRO_USER","juan"); PASSWORD=os.environ.get("EVBRO_PASSWORD","una-contraseña-larga-1")
fails=[]
def ok(c,m):
    print(("  ✔ " if c else "  ✘ ")+m)
    if not c: fails.append(m)

def drive(i):
    day=28-(i%28); month=10 if i<28 else (9 if i<56 else 8)
    return {"id":1000+i,"start_time":f"2026-{month:02d}-{day:02d}T08:00:00","end_time":f"2026-{month:02d}-{day:02d}T08:40:00",
            "start_address":"Calle <b>Mayor</b> 1, Zaragoza" if i==0 else f"Origen {i}","end_address":f"Destino con un nombre bastante largo {i}",
            "duration_seconds":2400,"duration_min":40,"distance_km":12.3,"start_battery_pct":80,"end_battery_pct":62,"battery_used_pct":18,
            "energy_consumed_kwh":3.1,"energy_consumed_wh":3100,"fuel_used_l":0.4 if i%3==0 else None,"speed_avg_kmh":31,"speed_max_kmh":88,
            "max_power_kw":64,"climate_used":i%2==0,"outside_temp_avg":14.5,"notes":"","consumption_wh_km":252,"consumption_kwh_100km":25.2,
            "consumption_l_100km":3.3 if i%3==0 else None,"is_incomplete":False,"start_estimated":i==1}
DRIVES=[drive(i) for i in range(65)]
MONTHS=[{"month":"2026-10","count":28,"distance_km":344.4,"energy_kwh":86.8,"fuel_l":4.0},
        {"month":"2026-09","count":28,"distance_km":344.4,"energy_kwh":86.8,"fuel_l":4.0},
        {"month":"2026-08","count":9,"distance_km":110.7,"energy_kwh":27.9,"fuel_l":1.2}]
def charge(i):
    day=28-(i%28); month=10 if i<28 else 9
    return {"id":2000+i,"vin":"X","start_time":f"2026-{month:02d}-{day:02d}T22:00:00","end_time":f"2026-{month:02d}-{day:02d}T23:30:00",
            "address":"Casa","latitude":None,"longitude":None,"start_battery_pct":30,"end_battery_pct":80,"start_range_km":None,"end_range_km":None,
            "kwh_added":9.5,"kwh_used":10.6 if i==0 else None,"duration_seconds":5400,"max_charge_power_kw":6.6,"charge_cost":1.42,
            "cost_mode":"pvpc","effective_cost_per_kwh":0.149}
CHARGES=[charge(i) for i in range(40)]

def handle(route, request):
    u=urlparse(request.url); q=parse_qs(u.query)
    if request.method!="GET": return route.continue_()
    if u.path.endswith("/drives"):
        lim=int(q.get("limit",["100"])[0]); off=int(q.get("offset",["0"])[0])
        body={"summary":{"count":65,"total_distance_km":799.5,"total_energy_kwh":201.5,"total_fuel_l":9.2,"total_duration_seconds":156000,
                         "total_duration_days":1.8,"avg_consumption_wh_km":252,"avg_fuel_consumption_l_100km":1.15},
              "drives":DRIVES[off:off+lim],"has_more":off+lim<len(DRIVES),"months":MONTHS,"incomplete_drives":[]}
        return route.fulfill(status=200,content_type="application/json",body=json.dumps(body))
    if u.path.endswith("/charges/summary"):
        body={"count":40,"kwh_added":380.0,"cost":56.8,"months":[{"month":"2026-10","count":28,"kwh_added":266.0,"cost":39.76},
                                                                 {"month":"2026-09","count":12,"kwh_added":114.0,"cost":17.04}]}
        return route.fulfill(status=200,content_type="application/json",body=json.dumps(body))
    if u.path.endswith("/charges"):
        lim=int(q.get("limit",["50"])[0]); off=int(q.get("offset",["0"])[0])
        return route.fulfill(status=200,content_type="application/json",body=json.dumps(CHARGES[off:off+lim]))
    return route.continue_()

def overflow(pg):
    return pg.evaluate("document.documentElement.scrollWidth > window.innerWidth + 1")

with sync_playwright() as p:
    b=p.chromium.launch()
    for theme in ("dark","light"):
        print("==",theme)
        # bypass_csp: `wait_for_function` evaluates a string in the page, which the app's CSP forbids (csp.py checks the policy itself)
        ctx=b.new_context(viewport={"width":390,"height":844},is_mobile=True,has_touch=True,locale="es-ES",bypass_csp=True,service_workers="block")
        ctx.request.post(f"{BASE}/api/auth/login",headers={"Origin":BASE},data={"username":USER,"password":PASSWORD})
        ctx.add_init_script(f"localStorage.setItem('evbro_theme','{theme}')")
        pg=ctx.new_page(); errs=[]
        pg.on("pageerror",lambda e:errs.append(str(e)))
        pg.route("**/api/vehicles/**", handle)
        pg.goto(BASE); pg.wait_for_load_state("networkidle"); pg.wait_for_timeout(700)

        # ── Viajes ──
        pg.evaluate("switchTab('drives')"); pg.wait_for_selector("#drivesList .hist-row")
        rows=lambda: pg.locator("#drivesList .hist-row").count()
        ok(rows()==30 and pg.is_visible("#drivesMore"),"viajes: primera página de 30 filas y botón «Ver más viajes»")
        heads=pg.locator("#drivesList .hist-month").all_inner_texts()
        ok(len(heads)==2 and "Octubre 2026" in heads[0].title() and "28 viajes" in heads[0] and "344,4 km" in heads[0],f"viajes: separadores de mes con los totales del servidor ({heads})")
        first=pg.locator("#drivesList .hist-row").first
        second=pg.locator("#drivesList .hist-row").nth(1)
        ok("−18 % · 0,4 L" in first.inner_text() and "−18 % · 3,1 kWh" in second.inner_text() and "80 %" not in first.inner_text(),"viajes: la fila muestra lo gastado (batería y gasolina o energía), no el inicio y el fin")
        ok(pg.locator("#drivesList .hist-place b").count()==0 and "<b>Mayor</b>" in first.inner_text(),"viajes: las direcciones se escapan")
        ok(not first.locator(".hist-detail").is_visible(),"viajes: el detalle empieza plegado")
        first.locator("summary").click(); pg.wait_for_timeout(200)
        det=first.locator(".hist-detail").inner_text()
        ok(first.locator(".hist-detail").is_visible() and "80 % → 62 %" in det and "25,2 kWh/100 km" in det and "Eficiencia" not in det,"viajes: al tocar se ve el detalle con consumo y sin «eficiencia»")
        ok(first.get_by_role("button",name="Ver trazado").is_visible() and first.get_by_role("button",name="Editar").is_visible(),"viajes: acciones en el detalle")
        if theme=="dark": pg.screenshot(path=f"{OUT}/historial-viajes-{theme}.png",full_page=False)
        pg.click("#drivesMore"); pg.wait_for_function("document.querySelectorAll('#drivesList .hist-row').length===60")
        pg.click("#drivesMore"); pg.wait_for_function("document.querySelectorAll('#drivesList .hist-row').length===65")
        ok(pg.is_hidden("#drivesMore"),"viajes: tras la última página desaparece «Ver más»")
        ok(pg.locator("#drivesList .hist-month").count()==3,"viajes: cada mes aparece una sola vez al ir cargando páginas")
        ok(not overflow(pg),"viajes: nada se sale a 390 px")
        pg.screenshot(path=f"{OUT}/historial-viajes-{theme}-full.png",full_page=True)

        # ── Cargas ──
        pg.evaluate("switchTab('charges')"); pg.evaluate("switchChargesSubTab('history')"); pg.evaluate("loadCharges()")
        pg.wait_for_selector("#chargesList .hist-row")
        ok(pg.inner_text("#chargesTotalCount")=="40" and "380,0 kWh" in pg.inner_text("#chargesTotalKwh"),"cargas: los totales salen de todas las cargas (servidor)")
        crows=lambda: pg.locator("#chargesList .hist-row").count()
        ok(crows()==30 and pg.is_visible("#chargesMore"),"cargas: primera página de 30 filas y «Ver más cargas»")
        c0=pg.locator("#chargesList .hist-row").first
        ok("+9,5 kWh" in c0.inner_text() and "+50 %" in c0.inner_text() and "1,42 €" in c0.inner_text(),"cargas: la fila muestra energía, % ganado y coste")
        c0.locator("summary").click(); pg.wait_for_timeout(200)
        cd=c0.locator(".hist-detail").inner_text()
        ok("30 % → 80 %" in cd and "10,6 kWh" in cd and "PVPC" in cd,"cargas: el detalle muestra batería, energía de la red y tarifa")
        if theme=="dark": pg.screenshot(path=f"{OUT}/historial-cargas-{theme}.png",full_page=False)
        pg.click("#chargesMore"); pg.wait_for_function("document.querySelectorAll('#chargesList .hist-row').length===40")
        ok(pg.is_hidden("#chargesMore"),"cargas: tras la última página desaparece «Ver más»")
        ok(not overflow(pg),"cargas: nada se sale a 390 px")
        c0.get_by_role("button",name="Ver detalle y gráfica").click(); pg.wait_for_timeout(400)
        ok(pg.evaluate("document.getElementById('chargesSubTabDetails')?.classList.contains('active') ?? true"),"cargas: «Ver detalle y gráfica» abre la sesión")
        ok(not errs,f"sin errores de JavaScript {errs}")
        ctx.close()
    b.close()
print("FALLOS:", fails or "ninguno")
sys.exit(1 if fails else 0)
