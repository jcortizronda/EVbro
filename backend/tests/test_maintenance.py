"""Mantenimiento: plan oficial de EBRO (próxima revisión, escalones, plazos) y plan personalizado."""
from __future__ import annotations

import datetime

import httpx
import pytest

from app.db.models import AppUser, MaintenanceItem, MaintenancePlan, Vehicle
from app.db.session import async_session_factory
from app.main import app
from app.security import auth, passwords
from app.services import maintenance_official as mo
from app.services.maintenance_alerts import collect_alerts

VIN = "LSJA0000000000001"
ENTREGA = datetime.datetime(2025, 3, 31)


# ───────────────────────── lógica pura ─────────────────────────
def test_el_primer_escalon_es_15000_y_despues_se_avanza_desde_el_ultimo_registrado() -> None:
    assert mo.next_step(None)["km"] == 15000
    assert mo.next_step(15000)["km"] == 30000
    assert mo.next_step(30000)["km"] == 45000
    assert mo.next_step(90000) is None
    # si se salta uno, el siguiente cuenta desde el más alto registrado
    assert mo.next_step(45000)["km"] == 60000


def test_vence_lo_que_antes_llegue_km_o_fecha() -> None:
    hoy = datetime.date(2026, 2, 1)
    por_km = mo.official_next(14800, hoy - datetime.timedelta(days=200), None, ENTREGA, 0.0, "S900 PHEV")
    assert por_km["km"] == 15000 and por_km["status"] == "warning"                 # 200 km: ya «próximo»
    por_fecha = mo.official_next(5000, datetime.date(2026, 3, 25), None, ENTREGA, 0.0, "S900 PHEV")
    assert por_fecha["status"] == "warning" and por_fecha["remaining_days"] == 6   # pocos km, pero casi 12 meses
    vencida = mo.official_next(5000, datetime.date(2026, 4, 2), None, ENTREGA, 0.0, "S900 PHEV")
    assert vencida["status"] == "due" and vencida["remaining_days"] < 0            # vencida por fecha con 5.000 km
    ok = mo.official_next(3000, datetime.date(2025, 6, 1), None, ENTREGA, 0.0, "S900 PHEV")
    assert ok["status"] == "ok"


def test_sin_fecha_de_entrega_solo_se_vigilan_los_kilometros() -> None:
    n = mo.official_next(9000, datetime.date(2030, 1, 1), None, None, 0.0, None)
    assert n["due_date"] is None and n["remaining_days"] is None and n["progress_time_pct"] is None
    assert n["status"] == "ok" and n["remaining_km"] == 6000


def test_meses_de_calendario_y_fin_de_mes() -> None:
    assert mo.add_months(datetime.datetime(2025, 3, 31), 12) == datetime.datetime(2026, 3, 31)
    assert mo.add_months(datetime.datetime(2025, 3, 31), 11) == datetime.datetime(2026, 2, 28)
    assert mo.add_months(datetime.datetime(2024, 1, 31), 1) == datetime.datetime(2024, 2, 29)     # bisiesto


def test_detalle_del_escalon_de_60000_incluye_anticongelante_y_transmision() -> None:
    phev = mo.step_detail(60000, "S900 PHEV")
    nombres = [i["name"] for i in phev["replace_items"]]
    assert "Anticongelante" in nombres and any("Transmisión" in n for n in nombres)
    assert phev["extra_labor_hours"] == 0.5 and phev["labor_hours"] == 2.3
    assert mo.step_detail(60000, "S800 HEV")["extra_labor_hours"] == 0.3
    assert mo.step_detail(15000, "S900 PHEV")["extra_labor_hours"] == 0.0


def test_las_sugerencias_no_se_crean_solas_y_tienen_intervalo() -> None:
    assert all(s["interval_km"] or s["interval_months"] for s in mo.SUGGESTIONS)


# ───────────────────────── API ─────────────────────────
class _Req:
    client = type("C", (), {"host": "127.0.0.1"})()
    headers = {"user-agent": "t"}


@pytest.fixture
async def web(db):
    async with async_session_factory() as s:
        s.add(Vehicle(owner_id=1, vin=VIN, name="Ebro", model="S900 PHEV", odometer=14000.0))
        user = AppUser(id=1, role="manager", username="juan", password_hash=passwords.hash_password("una-contraseña-larga-1"))
        s.add(user)
        await s.commit()
        token = await auth.create_session(s, user, _Req())
    c = httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://testserver")
    c.cookies.set(auth.COOKIE_NAME, token)
    yield c
    await c.aclose()


async def test_el_plan_por_defecto_es_oficial_y_no_inventa_tareas(web) -> None:
    r = await web.get(f"/api/vehicles/{VIN}/maintenance/plan")
    assert r.status_code == 200
    d = r.json()
    assert d["mode"] == "official" and d["baseline"]["configured"] is False
    assert d["custom"]["items"] == [] and len(d["custom"]["suggestions"]) == len(mo.SUGGESTIONS)
    assert d["official"]["next"]["km"] == 15000 and d["official"]["next"]["remaining_km"] == 1000.0
    assert (await web.get(f"/api/vehicles/{VIN}/maintenance")).json() == []          # ya no se crean 6 tareas solas


async def test_fijar_entrega_y_cambiar_de_modo(web) -> None:
    r = await web.put(f"/api/vehicles/{VIN}/maintenance/plan",
                      json={"mode": "custom", "baseline_date": "2025-03-31T00:00:00", "baseline_odometer": 12})
    d = r.json()
    assert d["mode"] == "custom" and d["baseline"]["configured"] and d["baseline"]["odometer"] == 12
    assert d["official"]["next"]["due_date"].startswith("2026-03-31")
    assert (await web.put(f"/api/vehicles/{VIN}/maintenance/plan", json={"mode": "otro"})).status_code == 422
    r = await web.put(f"/api/vehicles/{VIN}/maintenance/plan", json={"clear_baseline_date": True})
    assert r.json()["baseline"]["configured"] is False and r.json()["mode"] == "custom"   # el modo se conserva


async def test_registrar_un_escalon_avanza_al_siguiente_y_queda_en_el_historial(web) -> None:
    r = await web.post(f"/api/vehicles/{VIN}/maintenance/official/complete",
                       json={"step_km": 15000, "odometer": 15210, "cost": 120.5, "workshop": "Taller X"})
    assert r.status_code == 200 and r.json()["log_id"]
    d = (await web.get(f"/api/vehicles/{VIN}/maintenance/plan")).json()
    assert d["official"]["next"]["km"] == 30000
    assert [h["step_km"] for h in d["official"]["history"]] == [15000]
    assert d["official"]["matrix"]["columns"][0]["is_done"] and d["official"]["matrix"]["columns"][1]["is_next"]
    logs = (await web.get(f"/api/vehicles/{VIN}/maintenance/logs")).json()
    assert logs[0]["revision_km"] == 15000 and logs[0]["origin"] == "official" and logs[0]["cost"] == 120.5
    # no aparece como tarea propia
    assert (await web.get(f"/api/vehicles/{VIN}/maintenance")).json() == []
    # repetirlo es un error claro; un escalón inventado, 422
    dup = await web.post(f"/api/vehicles/{VIN}/maintenance/official/complete", json={"step_km": 15000})
    assert dup.status_code == 409
    assert (await web.post(f"/api/vehicles/{VIN}/maintenance/official/complete", json={"step_km": 20000})).status_code == 422


async def test_completar_todos_los_escalones_cierra_el_plan(web) -> None:
    for st in mo.STEPS:
        assert (await web.post(f"/api/vehicles/{VIN}/maintenance/official/complete", json={"step_km": st["km"]})).status_code == 200
    d = (await web.get(f"/api/vehicles/{VIN}/maintenance/plan")).json()
    assert d["official"]["finished"] is True and d["official"]["next"] is None


async def test_las_tareas_propias_y_sus_sugerencias(web) -> None:
    r = await web.post(f"/api/vehicles/{VIN}/maintenance", json={"name": "Rotación de neumáticos", "category": "Neumáticos",
                                                                 "interval_km": 10000, "interval_months": 12})
    assert r.status_code == 200 and r.json()["status"] == "ok"
    d = (await web.get(f"/api/vehicles/{VIN}/maintenance/plan")).json()
    assert [i["name"] for i in d["custom"]["items"]] == ["Rotación de neumáticos"]
    assert "Rotación de neumáticos" not in [s["name"] for s in d["custom"]["suggestions"]]     # ya no se sugiere
    item_id = d["custom"]["items"][0]["id"]
    done = await web.post(f"/api/maintenance/{item_id}/complete", json={"odometer": 14100, "cost": 30})
    assert done.status_code == 200 and done.json()["next_due_odometer"] == 24100.0


async def test_cambiar_el_intervalo_de_una_tarea_recalcula_su_vencimiento(web) -> None:
    r = await web.post(f"/api/vehicles/{VIN}/maintenance", json={"name": "Aceite", "interval_km": 10000, "interval_months": 12,
                                                                 "last_service_odometer": 12000})
    item_id = r.json()["id"]
    assert r.json()["next_due_odometer"] == 22000
    r = await web.put(f"/api/maintenance/{item_id}", json={"interval_km": 15000, "interval_months": 6})
    assert r.json()["next_due_odometer"] == 27000 and r.json()["interval_months"] == 6 and r.json()["next_due_date"]
    r = await web.put(f"/api/maintenance/{item_id}", json={"interval_km": None})
    assert r.json()["next_due_odometer"] is None                                           # solo por meses


async def test_las_rutas_del_plan_exigen_sesion(db) -> None:
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://testserver") as c:
        assert (await c.get(f"/api/vehicles/{VIN}/maintenance/plan")).status_code == 401
        assert (await c.put(f"/api/vehicles/{VIN}/maintenance/plan", json={})).status_code == 401
        assert (await c.post(f"/api/vehicles/{VIN}/maintenance/official/complete", json={"step_km": 15000})).status_code == 401


async def test_vehiculo_inexistente_da_404(web) -> None:
    assert (await web.get("/api/vehicles/NOEXISTE/maintenance/plan")).status_code == 404


# ───────────────────────── avisos ─────────────────────────
async def test_avisos_en_modo_oficial_usan_el_escalon_siguiente(db) -> None:
    async with async_session_factory() as s:
        s.add(Vehicle(owner_id=1, vin=VIN, name="Ebro", model="S900 PHEV", odometer=14600.0))
        s.add(MaintenancePlan(vin=VIN, mode="official", baseline_date=ENTREGA, baseline_odometer=0.0))
        await s.commit()
    async with async_session_factory() as s:
        a = await collect_alerts(s, VIN, 14600.0, datetime.datetime(2025, 9, 1))
    assert len(a) == 1 and a[0]["key"] == "official:15000:warning" and not a[0]["is_overdue"]
    assert "Aceite (4,5 Litros) 0W-20" in a[0]["items"]
    async with async_session_factory() as s:
        a = await collect_alerts(s, VIN, 5000.0, datetime.datetime(2026, 5, 1))            # vencida por fecha
    assert a[0]["is_overdue"] and a[0]["key"] == "official:15000:due"


async def test_avisos_en_modo_personalizado_ignoran_el_plan_oficial_y_miran_las_tareas(db) -> None:
    async with async_session_factory() as s:
        s.add(Vehicle(owner_id=1, vin=VIN, name="Ebro", model="S900 PHEV", odometer=30000.0))
        s.add(MaintenancePlan(vin=VIN, mode="custom", baseline_date=ENTREGA))
        s.add(MaintenanceItem(vin=VIN, name="Aceite", category="Motor", origin="custom", interval_km=10000.0,
                              last_service_odometer=19000.0, next_due_odometer=29000.0))
        s.add(MaintenanceItem(vin=VIN, name="Frenos", category="Frenos", origin="custom", interval_km=30000.0,
                              last_service_odometer=20000.0, next_due_odometer=50000.0))
        await s.commit()
    async with async_session_factory() as s:
        a = await collect_alerts(s, VIN, 30000.0, datetime.datetime(2026, 1, 1))
    assert [x["label"] for x in a] == ["Aceite"] and a[0]["is_overdue"]


async def test_el_texto_del_aviso_dice_km_o_dias_segun_lo_que_toque(monkeypatch) -> None:
    from app.services.notification_service import NotificationService

    enviados = []

    async def falso(cls, category, title, message, tags=None, priority="default", **kwargs):
        enviados.append((str(title), str(message)))          # `Message`s render in Spanish by default
        return []

    monkeypatch.setattr(NotificationService, "dispatch", classmethod(falso))
    await NotificationService.notify_maintenance_alert("Ebro", "revisión oficial de 15.000 km", 800.0, False, ["Aceite"], 1.0, remaining_days=20)
    assert "800 km o 20 días" in enviados[0][1] and "lo que antes llegue" in enviados[0][1] and enviados[0][0].startswith("Próximo")
    await NotificationService.notify_maintenance_alert("Ebro", "revisión oficial de 15.000 km", 2000.0, True, [], None, remaining_days=-9)
    assert "9 días" in enviados[1][1] and "km" not in enviados[1][1].split("desde hace")[1] and enviados[1][0].startswith("Mantenimiento vencido")


# ───────────────────────── the official plan is EBRO's ─────────────────────────
def test_is_ebro_ignores_case_and_spaces() -> None:
    assert mo.is_ebro("EBRO") and mo.is_ebro("Ebro") and mo.is_ebro(" ebro ")
    assert not mo.is_ebro("Toyota") and not mo.is_ebro(None) and not mo.is_ebro("")


async def test_another_brand_has_no_official_plan_but_keeps_the_itv(web) -> None:
    assert (await web.put(f"/api/vehicles/{VIN}", json={"brand": "Toyota", "registration_date": "2020-01-10T00:00:00"})).status_code == 200
    d = (await web.get(f"/api/vehicles/{VIN}/maintenance/plan")).json()
    assert d["official_available"] is False and d["mode"] == "custom"
    assert d["itv"]["next_due_date"] is not None
    r = await web.put(f"/api/vehicles/{VIN}/maintenance/plan", json={"mode": "official"})
    assert r.status_code == 409 and "Ebro" in r.json()["detail"]
    assert (await web.put(f"/api/vehicles/{VIN}/maintenance/plan", json={"mode": "custom"})).status_code == 200
    # back to EBRO: the official plan is available again
    await web.put(f"/api/vehicles/{VIN}", json={"brand": "Ebro"})
    d = (await web.put(f"/api/vehicles/{VIN}/maintenance/plan", json={"mode": "official"})).json()
    assert d["official_available"] is True and d["mode"] == "official"


async def test_alerts_of_another_brand_skip_official_steps(db) -> None:
    async with async_session_factory() as s:
        s.add(Vehicle(owner_id=1, vin=VIN, name="Corolla", brand="Toyota", model="Corolla", odometer=14600.0))
        s.add(MaintenancePlan(vin=VIN, mode="official", baseline_date=ENTREGA, baseline_odometer=0.0))
        s.add(MaintenanceItem(vin=VIN, name="Aceite", category="Motor", origin="custom", interval_km=10000.0,
                              last_service_odometer=4000.0, next_due_odometer=14000.0))
        await s.commit()
    async with async_session_factory() as s:
        a = await collect_alerts(s, VIN, 14600.0, datetime.datetime(2025, 9, 1))
    assert [x["label"] for x in a] == ["Aceite"]
