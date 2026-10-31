"""Coste de carga manual y precio por defecto, eficiencia sin inventar, retención del historial y alarmas de puertas con confirmación."""
from __future__ import annotations

import datetime

import httpx
import pytest
from freezegun import freeze_time
from sqlalchemy import func, select

from app.core.telemetry import parse_telemetry_data
from app.db.models import (
    AppUser,
    ChargePoint,
    ChargingSession,
    Drive,
    DrivePosition,
    TelemetrySnapshot,
    Vehicle,
)
from app.db.session import async_session_factory
from app.main import app
from app.security import auth, passwords
from app.services import charge_detector as cd
from app.services import poller as poller_mod
from app.services import retention
from app.services.charge_detector import ChargeDetector
from app.services.poller import VehiclePoller

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


async def carga(kwh=10.0, **extra) -> int:
    async with async_session_factory() as s:
        c = ChargingSession(vin=VIN, start_time=D(2026, 10, 1, 20), end_time=D(2026, 10, 2, 2), start_battery_pct=20.0, end_battery_pct=70.0,
                            kwh_added=kwh, duration_seconds=21600, **extra)
        s.add(c)
        await s.commit()
        return c.id


# ───────────────────────── coste de carga ─────────────────────────
async def test_precio_por_kwh_calcula_el_coste_sobre_la_energia_anadida(web) -> None:
    cid = await carga(10.0)
    r = await web.put(f"/api/charges/{cid}/cost", json={"price_per_kwh": 0.2})
    c = r.json()
    assert r.status_code == 200 and c["charge_cost"] == 2.0 and c["effective_cost_per_kwh"] == 0.2 and c["cost_mode"] == "manual"
    assert c["cost_source"] == "Introducida por ti" and c["has_cost"] is True


async def test_coste_total_deriva_el_precio_efectivo(web) -> None:
    cid = await carga(10.0)
    c = (await web.put(f"/api/charges/{cid}/cost", json={"total_cost": 3.0})).json()
    assert c["charge_cost"] == 3.0 and c["effective_cost_per_kwh"] == 0.3


async def test_kwh_del_contador_dan_la_eficiencia_real_y_el_coste_usa_la_energia_de_la_red(web) -> None:
    cid = await carga(10.0)
    sin = (await web.get(f"/api/charges/{cid}")).json()["charge"]
    assert sin["kwh_used"] is None and sin["efficiency_pct"] is None            # sin contador: desconocida, NO 95 %
    c = (await web.put(f"/api/charges/{cid}/cost", json={"price_per_kwh": 0.2, "kwh_meter": 12.5})).json()
    assert c["kwh_used"] == 12.5 and c["efficiency_pct"] == 80.0 and c["charge_cost"] == 2.5     # 12,5 kWh pagados × 0,20
    c = (await web.put(f"/api/charges/{cid}/cost", json={"kwh_meter": 12.0})).json()               # solo cambian los kWh: se recalcula
    assert c["charge_cost"] == 2.4 and c["efficiency_pct"] == 83.3


async def test_validaciones_del_coste(web) -> None:
    cid = await carga()
    assert (await web.put(f"/api/charges/{cid}/cost", json={})).status_code == 422
    assert (await web.put(f"/api/charges/{cid}/cost", json={"price_per_kwh": 0.2, "total_cost": 3})).status_code == 422
    assert (await web.put(f"/api/charges/{cid}/cost", json={"price_per_kwh": -1})).status_code == 422
    assert (await web.put("/api/charges/9999/cost", json={"price_per_kwh": 0.2})).status_code == 404


async def test_la_carga_sin_tarifa_se_muestra_sin_coste(web) -> None:
    cid = await carga()
    c = (await web.get(f"/api/charges/{cid}")).json()["charge"]
    assert c["has_cost"] is False and c["cost_source"] == "Sin tarifa" and c["charge_cost"] == 0.0


async def test_el_detector_pone_0_euros_por_kwh_fuera_de_geocercas(db, monkeypatch) -> None:
    async with async_session_factory() as s:
        s.add(Vehicle(owner_id=1, vin=VIN, name="Ebro", battery_capacity_kwh=20.0))
        await s.commit()
    cd._ACTIVE_CHARGES.clear()

    async def sin_geocerca(*a, **k):
        return None

    async def direccion(lat, lon):
        return "Mi garaje"

    monkeypatch.setattr(cd, "find_geofence", sin_geocerca)
    monkeypatch.setattr(cd, "reverse_geocode", direccion)

    async def lectura(at, soc, charging, plug=True, kw=7.0):
        with freeze_time(at):
            async with async_session_factory() as s:
                v = (await s.execute(select(Vehicle).where(Vehicle.vin == VIN))).scalar_one()
                res = await ChargeDetector.process_snapshot(s, v, {"is_charging": charging, "cable_connected": plug, "battery_level": soc,
                                                                  "charge_power_kw": kw, "latitude": 37.0, "longitude": -6.0})
                await s.commit()
                return res

    await lectura(D(2026, 10, 1, 22), 30.0, True)
    fin = await lectura(D(2026, 10, 2, 1), 80.0, False, kw=0.0)
    assert fin.kwh_added == 10.0                       # 50 % de 20 kWh (capacidad del vehículo, no 19,27 fijo)
    assert fin.kwh_used is None                        # la energía de la red ya no se inventa
    assert fin.cost_mode == "default" and fin.effective_cost_per_kwh == 0.0 and fin.charge_cost == 0.0       # 0 €/kWh por defecto


# ───────────────────────── retención ─────────────────────────
async def _datos():
    ahora = D(2026, 10, 28)
    async with async_session_factory() as s:
        if (await s.execute(select(Vehicle).where(Vehicle.vin == VIN))).scalar_one_or_none() is None:
            s.add(Vehicle(owner_id=1, vin=VIN, name="Ebro"))
        await s.flush()
        for dias, cerrado in ((400, True), (100, True), (400, False)):
            d = Drive(vin=VIN, start_time=ahora - datetime.timedelta(days=dias), end_time=(ahora - datetime.timedelta(days=dias, minutes=-10)) if cerrado else None)
            s.add(d)
            await s.flush()
            s.add(DrivePosition(drive_id=d.id, timestamp=d.start_time, latitude=1.0, longitude=1.0))
        for dias in (400, 100):
            c = ChargingSession(vin=VIN, start_time=ahora - datetime.timedelta(days=dias), end_time=ahora - datetime.timedelta(days=dias, minutes=-60), kwh_added=5)
            s.add(c)
            await s.flush()
            s.add(ChargePoint(charging_session_id=c.id, timestamp=c.start_time, battery_pct=50.0))
        for dias in (500, 200, 1):
            s.add(TelemetrySnapshot(vin=VIN, timestamp=ahora - datetime.timedelta(days=dias), state="parked"))
        await s.commit()
    return ahora


async def contar(modelo) -> int:
    async with async_session_factory() as s:
        return (await s.execute(select(func.count()).select_from(modelo))).scalar_one()


async def test_por_defecto_viajes_y_cargas_se_guardan_para_siempre_y_la_telemetria_bruta_un_ano(db) -> None:
    ahora = await _datos()
    async with async_session_factory() as s:
        assert await retention.get_config(s) == {"trips_days": 0, "charges_days": 0, "snapshots_days": 365}
        borrado = await retention.apply(s, ahora)
    assert borrado == {"trips": 0, "charges": 0, "snapshots": 1}                     # solo la lectura de hace 500 días
    assert await contar(Drive) == 3 and await contar(ChargingSession) == 2


async def test_la_retencion_borra_solo_lo_cerrado_y_viejo_con_sus_puntos(db) -> None:
    ahora = await _datos()
    async with async_session_factory() as s:
        await retention.set_config(s, {"trips_days": 365, "charges_days": 365, "snapshots_days": 0})
        borrado = await retention.apply(s, ahora)
    assert borrado == {"trips": 1, "charges": 1, "snapshots": 0}
    assert await contar(Drive) == 2                      # queda el de 100 días y el ABIERTO de 400 (no se toca lo que no ha cerrado)
    assert await contar(DrivePosition) == 2 and await contar(ChargePoint) == 1               # sin puntos huérfanos
    assert await contar(TelemetrySnapshot) == 3                                               # 0 = para siempre


@pytest.fixture
async def consola(db):
    """Retention is a server setting: it lives in the admin console, behind the administrator's own session."""
    from app.admin_app import admin_app
    async with async_session_factory() as s:
        admin = AppUser(id=90, role="admin", is_admin=True, username="admin", password_hash=passwords.hash_password("una-contraseña-larga-1"))
        s.add(admin)
        await s.commit()
        token = await auth.create_session(s, admin, _Req())
    c = httpx.AsyncClient(transport=httpx.ASGITransport(app=admin_app), base_url="http://testserver")
    c.cookies.set(auth.ADMIN_COOKIE_NAME, token)
    yield c
    await c.aclose()


async def test_api_de_retencion(consola) -> None:
    await _datos()
    r = await consola.get("/api/settings/retention")
    d = r.json()
    assert d["config"] == {"trips_days": 0, "charges_days": 0, "snapshots_days": 365} and d["stored"]["trips"]["count"] == 3
    d = (await consola.put("/api/settings/retention", json={"trips_days": 90})).json()
    assert d["config"]["trips_days"] == 90 and d["config"]["snapshots_days"] == 365          # lo no enviado se conserva
    assert (await consola.put("/api/settings/retention", json={"trips_days": -1})).status_code == 422
    assert (await consola.put("/api/settings/retention", json={"charges_days": 99999})).status_code == 422


async def test_aplicar_ahora_devuelve_lo_borrado(consola) -> None:
    await _datos()
    await consola.put("/api/settings/retention", json={"charges_days": 30})
    d = (await consola.post("/api/settings/retention/run")).json()
    assert d["deleted"]["charges"] == 2 and d["stored"]["charges"]["count"] == 0


async def test_retencion_exige_sesion_de_administrador_y_no_esta_en_la_app(db) -> None:
    from app.admin_app import admin_app
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=admin_app), base_url="http://testserver") as c:
        assert (await c.get("/api/settings/retention")).status_code == 401
        assert (await c.post("/api/settings/retention/run")).status_code == 401
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://testserver") as c:
        assert (await c.get("/api/settings/retention")).status_code in (401, 404)


# ───────────────────────── alarmas: el coche debe estar apagado y la situación mantenerse ─────────────────────────
@pytest.fixture
async def poller(db, monkeypatch):
    async with async_session_factory() as s:
        s.add(Vehicle(owner_id=1, vin=VIN, name="Ebro"))
        await s.commit()
    p = VehiclePoller.get_instance()
    p._pending_since.clear()
    p._last_security_alert_time.clear()
    p.__dict__.pop("_last_windows_alert_time", None)
    avisos = []

    async def seguridad(cls, nombre, problemas, **kwargs):
        avisos.append(("seguridad", problemas))

    async def ventanas(cls, nombre, ventanas, **kwargs):
        avisos.append(("ventanas", ventanas))

    monkeypatch.setattr(poller_mod.NotificationService, "notify_security_alert", classmethod(seguridad))
    monkeypatch.setattr(poller_mod.NotificationService, "notify_windows_open_alert", classmethod(ventanas))
    monkeypatch.setattr(VehiclePoller, "request_poll", lambda self, vin, force=False: None)
    p.avisos = avisos
    yield p
    p._pending_since.clear()


async def comprobar(p, at, **tel):
    base = {"state": "parked", "high_voltage_on": False, "is_locked": True, "doors": {"fl": 1}, "windows": {}, "odometer_km": None}
    with freeze_time(at):
        async with async_session_factory() as s:
            v = (await s.execute(select(Vehicle).where(Vehicle.vin == VIN))).scalar_one()
            await p._check_and_dispatch_alerts(s, v, {**base, **tel})
            await s.commit()


async def test_una_puerta_abierta_al_llegar_no_da_alarma_hasta_que_se_mantiene_cinco_minutos(poller) -> None:
    t = D(2026, 10, 28, 12, 0)
    await comprobar(poller, t)                                              # abres la puerta al llegar
    await comprobar(poller, t + datetime.timedelta(minutes=2))
    assert poller.avisos == []                                              # antes avisaba en la primera lectura
    await comprobar(poller, t + datetime.timedelta(minutes=5, seconds=1))
    assert poller.avisos == [("seguridad", ["Puertas abiertas: fl"])]


async def test_si_cierras_la_puerta_antes_no_hay_aviso_y_la_cuenta_se_reinicia(poller) -> None:
    t = D(2026, 10, 28, 12, 0)
    await comprobar(poller, t)
    await comprobar(poller, t + datetime.timedelta(minutes=3), doors={"fl": 0})        # ya cerrada
    await comprobar(poller, t + datetime.timedelta(minutes=4))                         # se vuelve a abrir: la cuenta empieza de cero
    await comprobar(poller, t + datetime.timedelta(minutes=6))
    assert poller.avisos == []


async def test_con_el_coche_encendido_no_se_avisa_nunca(poller) -> None:
    t = D(2026, 10, 28, 12, 0)
    for minutos in (0, 3, 6, 20):
        await comprobar(poller, t + datetime.timedelta(minutes=minutos), high_voltage_on=True)   # sales del coche con el coche en marcha
    assert poller.avisos == []


async def test_sin_dato_de_alta_tension_basta_con_aparcado_y_la_espera(poller) -> None:
    t = D(2026, 10, 28, 12, 0)
    await comprobar(poller, t, high_voltage_on=None)
    await comprobar(poller, t + datetime.timedelta(minutes=6), high_voltage_on=None)
    assert len(poller.avisos) == 1


async def test_coche_sin_cerrar_tambien_espera(poller) -> None:
    t = D(2026, 10, 28, 12, 0)
    await comprobar(poller, t, is_locked=False, doors={})
    assert poller.avisos == []
    await comprobar(poller, t + datetime.timedelta(minutes=6), is_locked=False, doors={})
    assert poller.avisos == [("seguridad", ["Vehículo sin cerrar con llave."])]


async def test_ventana_abierta_con_el_coche_cerrado_avisa_tras_la_espera(poller) -> None:
    t = D(2026, 10, 28, 12, 0)
    await comprobar(poller, t, doors={}, windows={"fl": 1})
    assert poller.avisos == []
    await comprobar(poller, t + datetime.timedelta(minutes=6), doors={}, windows={"fl": 1})
    assert poller.avisos == [("ventanas", ["Delantera Izquierda"])]


# ───────────────────────── parser ─────────────────────────
def test_high_voltage_on_sale_de_hvoltagestate() -> None:
    assert parse_telemetry_data({"hVoltageState": "1"})["high_voltage_on"] is True
    assert parse_telemetry_data({"hVoltageState": "0"})["high_voltage_on"] is False
    assert parse_telemetry_data({"hVoltageState": 1.0})["high_voltage_on"] is True
    assert parse_telemetry_data({})["high_voltage_on"] is None


# ───────────────────────── AC o DC ─────────────────────────
async def test_una_carga_es_ac_salvo_que_el_coche_diga_dc_y_se_puede_corregir(web, monkeypatch) -> None:
    from freezegun import freeze_time

    from app.services import charge_detector as cd

    async def sin_geocerca(*a, **k):
        return None

    async def direccion(lat, lon):
        return "Área de servicio"

    monkeypatch.setattr(cd, "find_geofence", sin_geocerca)
    monkeypatch.setattr(cd, "reverse_geocode", direccion)

    async def lectura(at, soc, charging, plug=True, kw=7.0, fast=False):
        with freeze_time(at):
            async with async_session_factory() as s:
                v = (await s.execute(select(Vehicle).where(Vehicle.vin == VIN))).scalar_one()
                res = await ChargeDetector.process_snapshot(s, v, {"is_charging": charging, "cable_connected": plug, "battery_level": soc,
                                                                  "charge_power_kw": kw, "fast_charging": fast, "latitude": 37.0, "longitude": -6.0})
                await s.commit()
                return res

    # 1. a plain 7 kW charge: AC
    await lectura(D(2026, 10, 1, 22), 30.0, True)
    ac = await lectura(D(2026, 10, 2, 1), 80.0, False, kw=0.0)
    assert ac.charge_type == "ac"
    # 2. the fast gun shows up mid-session: DC
    await lectura(D(2026, 10, 3, 10), 20.0, True)
    await lectura(D(2026, 10, 3, 10, 5), 30.0, True, kw=8.0, fast=True)
    dc = await lectura(D(2026, 10, 3, 10, 40), 80.0, False, kw=0.0)
    assert dc.charge_type == "dc"
    # 3. a power beyond AC levels is DC even without the flag
    await lectura(D(2026, 10, 4, 10), 20.0, True, kw=45.0)
    fast = await lectura(D(2026, 10, 4, 10, 30), 70.0, False, kw=0.0)
    assert fast.charge_type == "dc"
    # the API shows it, the summary counts it, and it can be corrected by hand
    rows = (await web.get(f"/api/vehicles/{VIN}/charges")).json()
    assert sorted(r["charge_type"] for r in rows) == ["ac", "dc", "dc"]
    summary = (await web.get(f"/api/vehicles/{VIN}/charges/summary")).json()
    assert summary["count"] == 3 and summary["dc_count"] == 2
    r = await web.put(f"/api/charges/{fast.id}/type", json={"charge_type": "ac"}, headers={"Origin": "http://testserver"})
    assert r.status_code == 200 and r.json()["charge_type"] == "ac"
    assert (await web.put(f"/api/charges/{fast.id}/type", json={"charge_type": "fast"}, headers={"Origin": "http://testserver"})).status_code == 422
    assert (await web.get(f"/api/vehicles/{VIN}/charges/summary")).json()["dc_count"] == 1
    # an old charge without the column is shown as AC
    old = await carga()
    assert (await web.get(f"/api/charges/{old}")).json()["charge"]["charge_type"] == "ac"
