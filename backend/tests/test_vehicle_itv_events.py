"""Configuración del vehículo (fechas), ITV desde la matriculación e intervenciones puntuales de mantenimiento."""
from __future__ import annotations

import datetime

import httpx
import pytest

from app.db.models import AppUser, Vehicle
from app.db.session import async_session_factory
from app.main import app
from app.security import auth, passwords
from app.services import maintenance_official as mo
from app.services.maintenance_alerts import collect_alerts

VIN = "LSJA0000000000001"
D = datetime.datetime


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


# ───────────────────────── lógica pura de la ITV ─────────────────────────
def test_primera_itv_a_los_4_anos_de_la_matriculacion() -> None:
    n = mo.itv_next_due(D(2023, 5, 10), None)
    assert n["due_date"] == D(2027, 5, 10) and n["basis"] == "registration"


def test_con_itv_previa_cada_2_anos_y_cada_1_a_partir_de_los_10() -> None:
    assert mo.itv_next_due(D(2023, 5, 10), D(2027, 5, 2))["due_date"] == D(2029, 5, 2)
    viejo = mo.itv_next_due(D(2012, 1, 1), D(2023, 3, 1))                  # 11 años al pasarla
    assert viejo["due_date"] == D(2024, 3, 1)
    assert mo.itv_next_due(D(2013, 1, 1), D(2023, 3, 1))["due_date"] == D(2024, 3, 1)       # justo 10 años y 2 meses


def test_la_pegatina_manda_sobre_el_calculo() -> None:
    n = mo.itv_next_due(D(2023, 5, 10), D(2027, 5, 2), valid_until=D(2029, 4, 30))
    assert n["due_date"] == D(2029, 4, 30) and n["basis"] == "valid_until"


def test_sin_matriculacion_no_hay_fecha_calculada() -> None:
    assert mo.itv_next_due(None, None) == {"due_date": None, "basis": "unknown"}
    assert mo.itv_status(None, datetime.date(2026, 1, 1))["status"] == "unknown"


def test_estados_de_la_itv() -> None:
    hoy = datetime.date(2026, 6, 1)
    assert mo.itv_status(D(2026, 5, 31), hoy)["status"] == "due"
    assert mo.itv_status(D(2026, 7, 20), hoy)["status"] == "warning"
    assert mo.itv_status(D(2026, 9, 1), hoy)["status"] == "ok"


# ───────────────────────── configuración del vehículo ─────────────────────────
async def test_fechas_y_capacidad_se_guardan_y_se_borran_con_null(web) -> None:
    r = await web.put(f"/api/vehicles/{VIN}", json={"registration_date": "2023-05-10T00:00:00", "delivery_date": "2023-06-01T00:00:00",
                                                    "battery_capacity_kwh": 18.5})
    d = r.json()
    assert r.status_code == 200 and d["registration_date"].startswith("2023-05-10") and d["delivery_date"].startswith("2023-06-01")
    assert d["battery_capacity_kwh"] == 18.5
    r = await web.put(f"/api/vehicles/{VIN}", json={"delivery_date": None})
    d = r.json()
    assert d["delivery_date"] is None and d["registration_date"].startswith("2023-05-10")
    assert d["battery_capacity_kwh"] == 18.5                                        # lo no enviado no se toca


async def test_validaciones_de_la_configuracion(web) -> None:
    assert (await web.put(f"/api/vehicles/{VIN}", json={"battery_capacity_kwh": 0})).status_code == 422
    assert (await web.get("/api/vehicles")).json()[0]["battery_capacity_kwh"] != 0              # el 422 no dejó nada a medias
    # no order is imposed between registration and delivery (a second-hand car is delivered after its registration)
    r = await web.put(f"/api/vehicles/{VIN}", json={"registration_date": "2019-01-01T00:00:00", "delivery_date": "2023-01-01T00:00:00"})
    assert r.status_code == 200 and r.json()["delivery_date"].startswith("2023-01-01")


async def test_el_plan_oficial_usa_la_fecha_de_entrega_del_vehiculo(web) -> None:
    await web.put(f"/api/vehicles/{VIN}", json={"delivery_date": "2025-03-31T00:00:00"})
    d = (await web.get(f"/api/vehicles/{VIN}/maintenance/plan")).json()
    assert d["baseline"]["configured"] and d["official"]["next"]["due_date"].startswith("2026-03-31")


async def test_el_endpoint_antiguo_del_plan_escribe_la_fecha_en_el_vehiculo(web) -> None:
    await web.put(f"/api/vehicles/{VIN}/maintenance/plan", json={"baseline_date": "2025-03-31T00:00:00"})
    assert (await web.get("/api/vehicles")).json()[0]["delivery_date"].startswith("2025-03-31")


# ───────────────────────── ITV ─────────────────────────
async def test_la_itv_cuenta_desde_la_matriculacion_y_se_registra(web) -> None:
    d = (await web.get(f"/api/vehicles/{VIN}/maintenance/plan")).json()
    assert d["itv"]["status"] == "unknown" and d["itv"]["next_due_date"] is None                   # sin matriculación no hay fecha
    await web.put(f"/api/vehicles/{VIN}", json={"registration_date": "2023-05-10T00:00:00"})
    itv = (await web.get(f"/api/vehicles/{VIN}/maintenance/plan")).json()["itv"]
    assert itv["next_due_date"].startswith("2027-05-10") and itv["basis"] == "registration"

    r = await web.post(f"/api/vehicles/{VIN}/maintenance/itv/complete", json={"service_date": "2027-05-02T00:00:00", "cost": 45.5})
    assert r.status_code == 200 and r.json()["next_due_date"].startswith("2029-05-02")
    itv = (await web.get(f"/api/vehicles/{VIN}/maintenance/plan")).json()["itv"]
    assert itv["last_date"].startswith("2027-05-02") and itv["next_due_date"].startswith("2029-05-02")

    await web.post(f"/api/vehicles/{VIN}/maintenance/itv/complete", json={"service_date": "2029-04-20T00:00:00", "valid_until": "2031-04-30T00:00:00"})
    itv = (await web.get(f"/api/vehicles/{VIN}/maintenance/plan")).json()["itv"]
    assert itv["next_due_date"].startswith("2031-04-30") and itv["basis"] == "valid_until"
    logs = (await web.get(f"/api/vehicles/{VIN}/maintenance/logs")).json()
    assert [x["item_name"] for x in logs] == ["ITV", "ITV"] and logs[0]["category"] == "Legal"
    assert (await web.get(f"/api/vehicles/{VIN}/maintenance")).json() == []                        # no es una tarea propia


async def test_aviso_de_itv_en_los_dos_modos(web) -> None:
    await web.put(f"/api/vehicles/{VIN}", json={"registration_date": "2023-05-10T00:00:00"})
    for modo in ("official", "custom"):
        await web.put(f"/api/vehicles/{VIN}/maintenance/plan", json={"mode": modo})
        async with async_session_factory() as s:
            alertas = await collect_alerts(s, VIN, 14000.0, D(2027, 4, 20))                       # 20 días antes
        itv = [a for a in alertas if a["key"].startswith("itv:")]
        assert len(itv) == 1 and itv[0]["remaining_days"] == 20 and not itv[0]["is_overdue"], modo
    async with async_session_factory() as s:
        vencida = [a for a in await collect_alerts(s, VIN, 14000.0, D(2027, 6, 1)) if a["key"].startswith("itv:")]
    assert vencida[0]["is_overdue"] and vencida[0]["key"] == "itv:due"


# ───────────────────────── intervenciones puntuales ─────────────────────────
async def test_intervencion_puntual_con_titulo_categoria_y_coste(web) -> None:
    r = await web.post(f"/api/vehicles/{VIN}/maintenance/events", json={
        "title": "Pastillas de freno delanteras", "category": "Frenos", "service_date": "2026-09-01T00:00:00",
        "odometer": 13500, "cost": 120.0, "workshop": "Taller X", "notes": "Discos en buen estado"})
    assert r.status_code == 200
    logs = (await web.get(f"/api/vehicles/{VIN}/maintenance/logs")).json()
    assert len(logs) == 1
    e = logs[0]
    assert e["item_name"] == "Pastillas de freno delanteras" and e["category"] == "Frenos" and e["origin"] == "oneoff"
    assert e["odometer"] == 13500 and e["cost"] == 120.0 and e["workshop"] == "Taller X"
    assert (await web.get(f"/api/vehicles/{VIN}/maintenance")).json() == []                        # no crea una tarea con intervalo
    d = (await web.get(f"/api/vehicles/{VIN}/maintenance/plan")).json()
    assert d["custom"]["items"] == []


async def test_intervencion_puntual_valida_y_usa_el_odometro_actual_por_defecto(web) -> None:
    assert (await web.post(f"/api/vehicles/{VIN}/maintenance/events", json={"title": "x"})).status_code == 422
    r = await web.post(f"/api/vehicles/{VIN}/maintenance/events", json={"title": "Reparación de rueda", "category": "Neumáticos"})
    assert r.status_code == 200
    log = (await web.get(f"/api/vehicles/{VIN}/maintenance/logs")).json()[0]
    assert log["odometer"] == 14000.0


async def test_las_rutas_nuevas_exigen_sesion(db) -> None:
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://testserver") as c:
        assert (await c.post(f"/api/vehicles/{VIN}/maintenance/events", json={"title": "xx"})).status_code == 401
        assert (await c.post(f"/api/vehicles/{VIN}/maintenance/itv/complete", json={})).status_code == 401
