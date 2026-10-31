"""Ficha del vehículo (con foto), repostajes, costes y alerta de presión de neumáticos."""
from __future__ import annotations

import datetime

import httpx
import pytest
from freezegun import freeze_time
from sqlalchemy import select

from app.config import settings
from app.db.models import AppUser, ChargingSession, Refuel, Vehicle
from app.db.session import async_session_factory
from app.main import app
from app.security import auth, passwords
from app.services import poller as poller_mod
from app.services import refuel_detector as rd
from app.services.poller import VehiclePoller
from app.services.refuel_detector import RefuelDetector

VIN = "LSJA0000000000001"
D = datetime.datetime
JPG = b"\xff\xd8\xff\xe0" + b"0" * 200
PNG = b"\x89PNG\r\n\x1a\n" + b"0" * 200


class _Req:
    client = type("C", (), {"host": "127.0.0.1"})()
    headers = {"user-agent": "t"}


@pytest.fixture
async def web(db, tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "DATA_DIR", str(tmp_path))
    async with async_session_factory() as s:
        s.add(Vehicle(owner_id=1, vin=VIN, name="Ebro", model="S900 PHEV", odometer=15000.0, fuel_tank_liters=50.0))
        user = AppUser(id=1, role="manager", username="juan", vehicle_limit=-1, password_hash=passwords.hash_password("una-contraseña-larga-1"))
        s.add(user)
        await s.commit()
        token = await auth.create_session(s, user, _Req())
    c = httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://testserver")
    c.cookies.set(auth.COOKIE_NAME, token)
    yield c
    await c.aclose()


# ───────────────────────── ficha del vehículo ─────────────────────────
async def test_la_ficha_completa_se_guarda_y_los_vacios_la_borran(web) -> None:
    r = await web.put(f"/api/vehicles/{VIN}", json={
        "brand": "EBRO", "model": "S900 PHEV", "model_year": 2024, "delivery_date": "2024-02-01T00:00:00", "purchase_price": 38900,
        "purchase_odometer": 12, "license_plate": "1234 ABC", "registration_date": "2024-01-15T00:00:00",
        "insurance_company": "Mapfre", "insurance_policy": "POL-998877", "tyre_front_size": "235/55 R19", "tyre_rear_size": "235/55 R19",
        "tyre_front_pressure_bar": 2.5, "tyre_rear_pressure_bar": 2.6, "tyre_pressure_tolerance_pct": 12, "fuel_tank_liters": 52})
    d = r.json()
    assert r.status_code == 200 and d["model_year"] == 2024 and d["purchase_price"] == 38900 and d["insurance_policy"] == "POL-998877"
    assert d["tyre_front_pressure_bar"] == 2.5 and d["tyre_pressure_tolerance_pct"] == 12 and d["fuel_tank_liters"] == 52 and d["brand"] == "EBRO"
    d = (await web.put(f"/api/vehicles/{VIN}", json={"insurance_company": "  ", "purchase_price": None, "tyre_front_size": ""})).json()
    assert d["insurance_company"] is None and d["purchase_price"] is None and d["tyre_front_size"] is None
    assert d["insurance_policy"] == "POL-998877" and d["model_year"] == 2024                       # lo no enviado se conserva


async def test_validaciones_de_la_ficha(web) -> None:
    for cuerpo in ({"model_year": 1950}, {"purchase_price": -5}, {"tyre_front_pressure_bar": 0.1}, {"tyre_pressure_tolerance_pct": 0},
                   {"fuel_tank_liters": 1}, {"insurance_policy": "x" * 61}):
        assert (await web.put(f"/api/vehicles/{VIN}", json=cuerpo)).status_code == 422, cuerpo


async def test_delivery_may_be_after_or_before_registration(web) -> None:
    """A second-hand car is delivered years after its registration; no order is imposed between the two dates."""
    for reg, delivery in (("2020-06-01", "2024-01-01"), ("2024-06-01", "2024-01-01"), ("2024-06-01", "2024-06-01")):
        r = await web.put(f"/api/vehicles/{VIN}", json={"registration_date": f"{reg}T00:00:00", "delivery_date": f"{delivery}T00:00:00"})
        assert r.status_code == 200 and r.json()["delivery_date"].startswith(delivery)


async def test_manual_car_chassis_number_is_informative_and_never_changes_the_vin(web) -> None:
    created = (await web.post("/api/vehicles", json={"name": "Corolla", "brand": "Toyota", "model": "Corolla"})).json()
    vin = created["vin"]
    assert vin.startswith("MAN") and created["chassis_number"] is None
    r = await web.put(f"/api/vehicles/{vin}", json={"chassis_number": " jtdkb20u 093123456 "})
    assert r.status_code == 200 and r.json()["chassis_number"] == "JTDKB20U093123456" and r.json()["vin"] == vin
    for bad in ("SHORT", "X" * 18, "VF1-234567890"):
        assert (await web.put(f"/api/vehicles/{vin}", json={"chassis_number": bad})).status_code == 422, bad
    assert (await web.put(f"/api/vehicles/{vin}", json={"chassis_number": None})).json()["chassis_number"] is None     # null clears it
    await web.put(f"/api/vehicles/{vin}", json={"chassis_number": "VF1RFB00012345678"})
    assert (await web.put(f"/api/vehicles/{vin}", json={"chassis_number": ""})).json()["chassis_number"] is None       # empty clears it


async def test_a_connected_car_does_not_take_a_chassis_number(web) -> None:
    r = await web.put(f"/api/vehicles/{VIN}", json={"chassis_number": "VF1RFB00012345678"})
    assert r.status_code == 409
    assert (await web.put(f"/api/vehicles/{VIN}", json={"chassis_number": None})).status_code == 200


async def test_foto_subir_servir_sustituir_y_borrar(web, tmp_path) -> None:
    r = await web.post(f"/api/vehicles/{VIN}/photo", files={"file": ("coche.jpg", JPG, "image/jpeg")})
    assert r.status_code == 200 and r.json()["photo_url"].startswith(f"/api/vehicles/{VIN}/photo?v=")
    foto = await web.get(f"/api/vehicles/{VIN}/photo")
    assert foto.status_code == 200 and foto.content == JPG and foto.headers["x-content-type-options"] == "nosniff"
    antigua = list((tmp_path / "vehicle_photos").iterdir())
    assert len(antigua) == 1
    r2 = await web.post(f"/api/vehicles/{VIN}/photo", files={"file": ("coche.png", PNG, "image/png")})
    assert r2.json()["photo_url"] != r.json()["photo_url"]                                   # URL distinta: el navegador la recarga
    nuevas = list((tmp_path / "vehicle_photos").iterdir())
    assert len(nuevas) == 1 and nuevas[0].suffix == ".png"                                    # la anterior se borró del disco
    assert (await web.delete(f"/api/vehicles/{VIN}/photo")).json()["photo_url"] is None
    assert list((tmp_path / "vehicle_photos").iterdir()) == [] and (await web.get(f"/api/vehicles/{VIN}/photo")).status_code == 404


async def test_foto_rechaza_lo_que_no_es_una_imagen(web) -> None:
    assert (await web.post(f"/api/vehicles/{VIN}/photo", files={"file": ("a.txt", b"hola", "text/plain")})).status_code == 415
    falso = await web.post(f"/api/vehicles/{VIN}/photo", files={"file": ("a.jpg", b"<script>alert(1)</script>", "image/jpeg")})
    assert falso.status_code == 415 and "válida" in falso.json()["detail"]                    # el tipo lo dice el cliente: se mira el contenido
    grande = await web.post(f"/api/vehicles/{VIN}/photo", files={"file": ("a.jpg", JPG + b"0" * (6 * 1024 * 1024), "image/jpeg")})
    assert grande.status_code == 413
    assert (await web.post("/api/vehicles/NOEXISTE/photo", files={"file": ("a.jpg", JPG, "image/jpeg")})).status_code == 404


async def test_la_foto_no_se_sirve_sin_sesion(db) -> None:
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://testserver") as c:
        assert (await c.get(f"/api/vehicles/{VIN}/photo")).status_code == 401
        assert (await c.post(f"/api/vehicles/{VIN}/photo", files={"file": ("a.jpg", JPG, "image/jpeg")})).status_code == 401


# ───────────────────────── detector de repostajes ─────────────────────────
@pytest.fixture(autouse=True)
def _repostajes_limpios(monkeypatch):
    rd._FUEL_LOW.clear()
    rd._LAST_REFUEL_AT.clear()

    async def direccion(lat, lon):
        return "Gasolinera Test"

    monkeypatch.setattr(rd, "reverse_geocode", direccion)
    yield
    rd._FUEL_LOW.clear()
    rd._LAST_REFUEL_AT.clear()


async def lectura(at, fuel, speed=0.0, state="parked"):
    with freeze_time(at):
        async with async_session_factory() as s:
            v = (await s.execute(select(Vehicle).where(Vehicle.vin == VIN))).scalar_one()
            res = await RefuelDetector.process_snapshot(s, v, {"fuel_level": fuel, "speed_kmh": speed, "state": state, "odometer_km": 15100,
                                                               "latitude": 37.0, "longitude": -6.0})
            await s.commit()
            return res


async def test_una_subida_del_deposito_con_el_coche_parado_es_un_repostaje(web) -> None:
    t = D(2026, 10, 28, 10, 0)
    await lectura(t, 20.0)
    r = await lectura(t + datetime.timedelta(minutes=30), 90.0)
    assert r is not None and (r.fuel_before_pct, r.fuel_after_pct) == (20.0, 90.0)
    assert r.liters == 35.0 and r.total_cost is None and r.detected and r.address == "Gasolinera Test"      # 70 % de 50 L; sin precio aún


async def test_el_bamboleo_del_indicador_y_las_subidas_en_marcha_no_cuentan(web) -> None:
    t = D(2026, 10, 28, 10, 0)
    await lectura(t, 30.0)
    assert await lectura(t + datetime.timedelta(minutes=5), 34.0) is None                      # +4 puntos: bamboleo
    assert await lectura(t + datetime.timedelta(minutes=10), 60.0, speed=40.0, state="driving") is None    # subida con el coche en marcha


async def test_el_consumo_baja_el_minimo_y_el_repostaje_se_mide_desde_el_ultimo_minimo(web) -> None:
    t = D(2026, 10, 28, 10, 0)
    await lectura(t, 60.0)
    await lectura(t + datetime.timedelta(hours=1), 25.0, speed=50.0, state="driving")          # conduciendo baja
    r = await lectura(t + datetime.timedelta(hours=2), 85.0)
    assert (r.fuel_before_pct, r.fuel_after_pct) == (25.0, 85.0)


async def test_una_lectura_de_cero_que_vuelve_no_es_un_repostaje(web) -> None:
    t = D(2026, 10, 28, 10, 0)
    await lectura(t, 27.0)
    assert await lectura(t + datetime.timedelta(minutes=5), 0.0) is None                       # lectura inválida
    assert await lectura(t + datetime.timedelta(minutes=10), 27.0) is None                     # y la real vuelve
    async with async_session_factory() as s:
        assert (await s.execute(select(Refuel))).scalars().all() == []


async def test_dos_repostajes_con_menos_de_media_hora_no_se_duplican(web) -> None:
    t = D(2026, 10, 28, 10, 0)
    await lectura(t, 20.0)
    assert await lectura(t + datetime.timedelta(minutes=1), 60.0) is not None
    await lectura(t + datetime.timedelta(minutes=5), 30.0, speed=30.0, state="driving")
    assert await lectura(t + datetime.timedelta(minutes=10), 70.0) is None


async def test_tras_un_reinicio_se_parte_de_la_ultima_lectura_guardada(web) -> None:
    from app.db.models import TelemetrySnapshot
    async with async_session_factory() as s:
        s.add(TelemetrySnapshot(vin=VIN, timestamp=D(2026, 10, 28, 9, 0), state="parked", fuel_level=22.0))
        await s.commit()
    r = await lectura(D(2026, 10, 28, 10, 0), 80.0)                                          # memoria vacía
    assert r is not None and r.fuel_before_pct == 22.0


# ───────────────────────── API de repostajes ─────────────────────────
async def test_repostaje_manual_con_precio_o_total(web) -> None:
    r = await web.post(f"/api/vehicles/{VIN}/refuels", json={"liters": 40, "price_per_liter": 1.5, "station": "Repsol"})
    d = r.json()
    assert r.status_code == 200 and d["total_cost"] == 60.0 and d["detected"] is False and d["odometer"] == 15000.0 and d["needs_price"] is False
    d = (await web.post(f"/api/vehicles/{VIN}/refuels", json={"liters": 40, "total_cost": 66})).json()
    assert d["price_per_liter"] == 1.65
    assert (await web.post(f"/api/vehicles/{VIN}/refuels", json={"liters": 40, "total_cost": 66, "price_per_liter": 1.6})).status_code == 422
    assert (await web.post(f"/api/vehicles/{VIN}/refuels", json={"liters": 0})).status_code == 422
    assert (await web.post("/api/vehicles/NOEXISTE/refuels", json={"liters": 10})).status_code == 404


async def test_un_repostaje_detectado_se_completa_con_el_precio_y_los_litros_reales(web) -> None:
    t = D(2026, 10, 28, 10, 0)
    await lectura(t, 20.0)
    await lectura(t + datetime.timedelta(minutes=30), 90.0)
    rid = (await web.get(f"/api/vehicles/{VIN}/refuels")).json()[0]["id"]
    d = (await web.put(f"/api/refuels/{rid}", json={"price_per_liter": 1.6})).json()
    assert d["total_cost"] == 56.0 and d["liters_estimated"] is True and d["needs_price"] is False
    d = (await web.put(f"/api/refuels/{rid}", json={"liters": 34.2})).json()                       # litros reales del ticket
    assert d["liters"] == 34.2 and d["total_cost"] == round(1.6 * 34.2, 2)
    d = (await web.put(f"/api/refuels/{rid}", json={"total_cost": 60.0, "station": "BP"})).json()
    assert d["price_per_liter"] == round(60 / 34.2, 3) and d["station"] == "BP"
    assert (await web.delete(f"/api/refuels/{rid}")).json()["status"] == "ok" and (await web.get(f"/api/vehicles/{VIN}/refuels")).json() == []
    assert (await web.put("/api/refuels/9999", json={"liters": 3})).status_code == 404


# ───────────────────────── costes ─────────────────────────
async def test_los_costes_suman_compra_cargas_repostajes_y_mantenimiento(web) -> None:
    await web.put(f"/api/vehicles/{VIN}", json={"purchase_price": 30000, "delivery_date": "2025-01-10T00:00:00", "purchase_odometer": 5000})
    async with async_session_factory() as s:
        s.add(ChargingSession(vin=VIN, start_time=D(2026, 9, 5), end_time=D(2026, 9, 5, 4), kwh_added=10, charge_cost=3.0, cost_mode="manual", address="Casa"))
        s.add(ChargingSession(vin=VIN, start_time=D(2026, 10, 5), end_time=D(2026, 10, 5, 4), kwh_added=8, charge_cost=0.0))          # sin coste
        await s.commit()
    await web.post(f"/api/vehicles/{VIN}/refuels", json={"liters": 40, "total_cost": 60, "timestamp": "2026-10-10T00:00:00"})
    await web.post(f"/api/vehicles/{VIN}/refuels", json={"liters": 30, "timestamp": "2026-10-11T00:00:00"})                          # sin precio
    await web.post(f"/api/vehicles/{VIN}/maintenance/events", json={"title": "Pastillas", "cost": 120, "service_date": "2026-09-20T00:00:00"})
    await web.post(f"/api/vehicles/{VIN}/maintenance/official/complete", json={"step_km": 15000, "cost": 200, "service_date": "2026-10-15T00:00:00"})
    d = (await web.get(f"/api/vehicles/{VIN}/costs")).json()
    t = d["totals"]
    assert (t["purchase"], t["charges"], t["refuels"], t["maintenance"]) == (30000, 3.0, 60.0, 320.0)
    assert t["running"] == 383.0 and t["all"] == 30383.0
    assert d["pending"] == {"charges": 1, "refuels": 1}                                       # lo que aún no tiene precio, a la vista
    assert d["km"] == 10000 and d["per_100km"] == 3.83                                       # (15000 - 5000 km al comprarlo)
    assert d["energy"] == {"kwh": 18.0, "liters": 70.0}
    meses = {m["month"]: m for m in d["monthly"]}
    assert meses["2026-09"]["charges"] == 3.0 and meses["2026-09"]["maintenance"] == 120.0 and meses["2026-10"]["maintenance"] == 200.0
    assert [x["type"] for x in d["recent"]][:2] == ["maintenance", "refuel"] and d["recent"][0]["title"] == "Revisión oficial EBRO"


async def test_los_costes_por_periodo_y_validacion(web) -> None:
    await web.put(f"/api/vehicles/{VIN}", json={"purchase_price": 30000, "delivery_date": "2020-01-10T00:00:00"})
    await web.post(f"/api/vehicles/{VIN}/refuels", json={"liters": 40, "total_cost": 60, "timestamp": "2019-10-10T00:00:00"})
    antes = (await web.get(f"/api/vehicles/{VIN}/costs?period=12m")).json()["totals"]
    assert antes["purchase"] == 0 and antes["refuels"] == 0 and antes["all"] == 0                  # la compra y el repostaje antiguos quedan fuera
    assert (await web.get(f"/api/vehicles/{VIN}/costs?period=otro")).status_code == 422
    assert (await web.get("/api/vehicles/NOEXISTE/costs")).status_code == 404


async def test_las_rutas_de_costes_y_repostajes_exigen_sesion(db) -> None:
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://testserver") as c:
        for url in (f"/api/vehicles/{VIN}/costs", f"/api/vehicles/{VIN}/refuels"):
            assert (await c.get(url)).status_code == 401
        assert (await c.put("/api/refuels/1", json={})).status_code == 401


# ───────────────────────── presión de neumáticos ─────────────────────────
def test_los_neumaticos_fuera_de_rango_se_listan_con_su_desviacion() -> None:
    v = Vehicle(vin=VIN, tyre_front_pressure_bar=2.5, tyre_rear_pressure_bar=2.6, tyre_pressure_tolerance_pct=10.0)
    tel = {"tyre_pressures": {"fl": 2.5, "fr": 2.1, "rl": 2.9, "rr": 2.6}}
    out = VehiclePoller.tyre_pressure_issues(v, tel)
    assert len(out) == 2 and "Delantero derecho" in out[0] and "-16 %" in out[0] and "Trasero izquierdo" in out[1] and "+12 %" in out[1]
    assert VehiclePoller.tyre_pressure_issues(Vehicle(vin=VIN), tel) == []                    # sin presión de referencia no se avisa
    assert VehiclePoller.tyre_pressure_issues(v, {"tyre_pressures": {"fl": None, "fr": 0}}) == []


@pytest.fixture
async def poller(db, monkeypatch):
    async with async_session_factory() as s:
        s.add(Vehicle(owner_id=1, vin=VIN, name="Ebro", tyre_front_pressure_bar=2.5, tyre_rear_pressure_bar=2.6, tyre_pressure_tolerance_pct=10.0))
        await s.commit()
    p = VehiclePoller.get_instance()
    p._pending_since.clear()
    p._last_tyre_alert_time.clear()
    avisos = []

    async def neumaticos(cls, nombre, problemas, **kwargs):
        avisos.append(problemas)

    monkeypatch.setattr(poller_mod.NotificationService, "notify_tyre_pressure", classmethod(neumaticos))
    monkeypatch.setattr(VehiclePoller, "request_poll", lambda self, vin, force=False: None)
    p.avisos = avisos
    yield p
    p._pending_since.clear()


async def comprobar(p, at, presiones, **cfg):
    with freeze_time(at):
        async with async_session_factory() as s:
            v = (await s.execute(select(Vehicle).where(Vehicle.vin == VIN))).scalar_one()
            for k, val in cfg.items():
                setattr(v, k, val)
            await p._check_tyre_pressure(v, {"tyre_pressures": presiones})
            await s.commit()


async def test_la_presion_baja_avisa_tras_diez_minutos_y_una_vez_cada_12_horas(poller) -> None:
    t = D(2026, 10, 28, 12, 0)
    malas = {"fl": 2.5, "fr": 2.0, "rl": 2.6, "rr": 2.6}
    await comprobar(poller, t, malas)
    await comprobar(poller, t + datetime.timedelta(minutes=5), malas)
    assert poller.avisos == []                                                                 # aún no han pasado los 10 minutos
    await comprobar(poller, t + datetime.timedelta(minutes=11), malas)
    assert len(poller.avisos) == 1 and "Delantero derecho" in poller.avisos[0][0]
    await comprobar(poller, t + datetime.timedelta(hours=2), malas)
    assert len(poller.avisos) == 1                                                             # no se repite dentro de las 12 h
    await comprobar(poller, t + datetime.timedelta(hours=13), malas)
    assert len(poller.avisos) == 2


async def test_si_la_presion_se_normaliza_se_reinicia_la_cuenta(poller) -> None:
    t = D(2026, 10, 28, 12, 0)
    await comprobar(poller, t, {"fr": 2.0})
    await comprobar(poller, t + datetime.timedelta(minutes=5), {"fr": 2.5})                    # vuelve al rango
    await comprobar(poller, t + datetime.timedelta(minutes=8), {"fr": 2.0})                    # vuelve a salir: cuenta desde cero
    await comprobar(poller, t + datetime.timedelta(minutes=15), {"fr": 2.0})
    assert poller.avisos == []


# ───────────────────────── tipo de motor ─────────────────────────
async def test_el_tipo_de_motor_se_guarda_se_valida_y_tiene_un_valor_por_marca(web) -> None:
    assert (await web.get("/api/vehicles")).json()[0]["fuel_type"] == "phev"                 # existing cars stay PHEV
    r = await web.put(f"/api/vehicles/{VIN}", json={"fuel_type": "diesel"})
    assert r.status_code == 200 and r.json()["fuel_type"] == "diesel"
    assert (await web.put(f"/api/vehicles/{VIN}", json={"fuel_type": "nuclear"})).status_code == 422
    assert (await web.put(f"/api/vehicles/{VIN}", json={"name": "Solo el nombre"})).json()["fuel_type"] == "diesel"     # untouched when not sent
    ebro = (await web.post("/api/vehicles", json={"name": "Otro Ebro", "brand": "EBRO", "model": "S700 PHEV"})).json()
    toyota = (await web.post("/api/vehicles", json={"name": "Corolla", "brand": "Toyota", "model": "Corolla"})).json()
    tesla = (await web.post("/api/vehicles", json={"name": "Model 3", "brand": "Tesla", "model": "Model 3", "fuel_type": "ev"})).json()
    assert (ebro["fuel_type"], toyota["fuel_type"], tesla["fuel_type"]) == ("phev", "gasoline", "ev")
