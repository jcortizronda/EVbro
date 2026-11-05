"""Vehicle profile (with photo), refuels, costs and the tyre pressure alert."""
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


# ───────────────────────── vehicle profile ─────────────────────────
async def test_the_full_profile_is_saved_and_empty_values_clear_it(web) -> None:
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
    assert d["insurance_policy"] == "POL-998877" and d["model_year"] == 2024                       # what is not sent is kept


async def test_profile_validations(web) -> None:
    for body in ({"model_year": 1950}, {"purchase_price": -5}, {"tyre_front_pressure_bar": 0.1}, {"tyre_pressure_tolerance_pct": 0},
                   {"fuel_tank_liters": 1}, {"insurance_policy": "x" * 61}):
        assert (await web.put(f"/api/vehicles/{VIN}", json=body)).status_code == 422, body


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


async def test_photo_upload_serve_replace_and_delete(web, tmp_path) -> None:
    r = await web.post(f"/api/vehicles/{VIN}/photo", files={"file": ("coche.jpg", JPG, "image/jpeg")})
    assert r.status_code == 200 and r.json()["photo_url"].startswith(f"/api/vehicles/{VIN}/photo?v=")
    photo = await web.get(f"/api/vehicles/{VIN}/photo")
    assert photo.status_code == 200 and photo.content == JPG and photo.headers["x-content-type-options"] == "nosniff"
    old = list((tmp_path / "vehicle_photos").iterdir())
    assert len(old) == 1
    r2 = await web.post(f"/api/vehicles/{VIN}/photo", files={"file": ("coche.png", PNG, "image/png")})
    assert r2.json()["photo_url"] != r.json()["photo_url"]                                   # different URL: the browser reloads it
    new = list((tmp_path / "vehicle_photos").iterdir())
    assert len(new) == 1 and new[0].suffix == ".png"                                    # the previous one was deleted from disk
    assert (await web.delete(f"/api/vehicles/{VIN}/photo")).json()["photo_url"] is None
    assert list((tmp_path / "vehicle_photos").iterdir()) == [] and (await web.get(f"/api/vehicles/{VIN}/photo")).status_code == 404


async def test_photo_rejects_what_is_not_an_image(web) -> None:
    assert (await web.post(f"/api/vehicles/{VIN}/photo", files={"file": ("a.txt", b"hola", "text/plain")})).status_code == 415
    fake = await web.post(f"/api/vehicles/{VIN}/photo", files={"file": ("a.jpg", b"<script>alert(1)</script>", "image/jpeg")})
    assert fake.status_code == 415 and "válida" in fake.json()["detail"]                    # the client states the type: the content is checked
    large = await web.post(f"/api/vehicles/{VIN}/photo", files={"file": ("a.jpg", JPG + b"0" * (6 * 1024 * 1024), "image/jpeg")})
    assert large.status_code == 413
    assert (await web.post("/api/vehicles/NOEXISTE/photo", files={"file": ("a.jpg", JPG, "image/jpeg")})).status_code == 404


async def test_the_photo_is_not_served_without_a_session(db) -> None:
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://testserver") as c:
        assert (await c.get(f"/api/vehicles/{VIN}/photo")).status_code == 401
        assert (await c.post(f"/api/vehicles/{VIN}/photo", files={"file": ("a.jpg", JPG, "image/jpeg")})).status_code == 401


# ───────────────────────── refuel detector ─────────────────────────
@pytest.fixture(autouse=True)
def _clean_refuels(monkeypatch):
    rd._FUEL_LOW.clear()
    rd._LAST_REFUEL_AT.clear()
    rd._PREV_STOP.clear()

    async def address(lat, lon):
        return "Gasolinera Test"

    monkeypatch.setattr(rd, "reverse_geocode", address)
    yield
    rd._FUEL_LOW.clear()
    rd._LAST_REFUEL_AT.clear()
    rd._PREV_STOP.clear()


async def reading(at, fuel, speed=0.0, state="parked", odometer=15100, lat=37.0):
    with freeze_time(at):
        async with async_session_factory() as s:
            v = (await s.execute(select(Vehicle).where(Vehicle.vin == VIN))).scalar_one()
            res = await RefuelDetector.process_snapshot(s, v, {"fuel_level": fuel, "speed_kmh": speed, "state": state, "odometer_km": odometer,
                                                               "latitude": lat, "longitude": -6.0})
            await s.commit()
            return res


async def test_a_tank_rise_with_the_car_stopped_is_a_refuel(web) -> None:
    t = D(2026, 10, 28, 10, 0)
    await reading(t, 20.0)
    r = await reading(t + datetime.timedelta(minutes=30), 90.0)
    assert r is not None and (r.fuel_before_pct, r.fuel_after_pct) == (20.0, 90.0)
    assert r.liters == 35.0 and r.total_cost is None and r.detected and r.address == "Gasolinera Test"      # 70 % of 50 L; no price yet


async def test_gauge_wobble_and_rises_while_driving_do_not_count(web) -> None:
    t = D(2026, 10, 28, 10, 0)
    await reading(t, 30.0)
    assert await reading(t + datetime.timedelta(minutes=5), 34.0) is None                      # +4 points: wobble
    assert await reading(t + datetime.timedelta(minutes=8), 33.0, speed=40.0, state="driving") is None
    assert await reading(t + datetime.timedelta(minutes=10), 60.0, speed=40.0, state="driving") is None    # rise on the road, no stop before


async def test_a_pump_stop_that_ends_before_the_next_reading_is_a_refuel_at_that_stop(web) -> None:
    """05-11-2026: 5 % stopped at the station, 43 % two minutes later already at 139 km/h."""
    t = D(2026, 11, 5, 15, 36)
    await reading(t, 7.0, speed=143.0, state="driving", odometer=10329)
    await reading(t + datetime.timedelta(minutes=2), 5.0, odometer=10331, lat=37.1)             # stopped at the pump
    r = await reading(t + datetime.timedelta(minutes=5), 43.0, speed=139.0, state="driving", odometer=10334)
    assert r is not None and (r.fuel_before_pct, r.fuel_after_pct) == (5.0, 43.0)
    assert r.timestamp == t + datetime.timedelta(minutes=2) and r.odometer == 10331 and r.latitude == 37.1   # the stop, not the road
    assert await reading(t + datetime.timedelta(hours=1), 23.0, odometer=10468) is None       # the next stop sees no second refuel


async def test_consumption_lowers_the_minimum_and_the_refuel_is_measured_from_the_last_minimum(web) -> None:
    t = D(2026, 10, 28, 10, 0)
    await reading(t, 60.0)
    await reading(t + datetime.timedelta(hours=1), 25.0, speed=50.0, state="driving")          # drops while driving
    r = await reading(t + datetime.timedelta(hours=2), 85.0)
    assert (r.fuel_before_pct, r.fuel_after_pct) == (25.0, 85.0)


async def test_a_zero_reading_that_comes_back_is_not_a_refuel(web) -> None:
    t = D(2026, 10, 28, 10, 0)
    await reading(t, 27.0)
    assert await reading(t + datetime.timedelta(minutes=5), 0.0) is None                       # invalid reading
    assert await reading(t + datetime.timedelta(minutes=10), 27.0) is None                     # and the real one comes back
    async with async_session_factory() as s:
        assert (await s.execute(select(Refuel))).scalars().all() == []


async def test_two_refuels_less_than_half_an_hour_apart_are_not_duplicated(web) -> None:
    t = D(2026, 10, 28, 10, 0)
    await reading(t, 20.0)
    assert await reading(t + datetime.timedelta(minutes=1), 60.0) is not None
    await reading(t + datetime.timedelta(minutes=5), 30.0, speed=30.0, state="driving")
    assert await reading(t + datetime.timedelta(minutes=10), 70.0) is None


async def test_after_a_restart_it_starts_from_the_last_stored_reading(web) -> None:
    from app.db.models import TelemetrySnapshot
    async with async_session_factory() as s:
        s.add(TelemetrySnapshot(vin=VIN, timestamp=D(2026, 10, 28, 9, 0), state="parked", fuel_level=22.0))
        await s.commit()
    r = await reading(D(2026, 10, 28, 10, 0), 80.0)                                          # empty memory
    assert r is not None and r.fuel_before_pct == 22.0


# ───────────────────────── refuels API ─────────────────────────
async def test_manual_refuel_with_price_or_total(web) -> None:
    r = await web.post(f"/api/vehicles/{VIN}/refuels", json={"liters": 40, "price_per_liter": 1.5, "station": "Repsol"})
    d = r.json()
    assert r.status_code == 200 and d["total_cost"] == 60.0 and d["detected"] is False and d["odometer"] == 15000.0 and d["needs_price"] is False
    d = (await web.post(f"/api/vehicles/{VIN}/refuels", json={"liters": 40, "total_cost": 66})).json()
    assert d["price_per_liter"] == 1.65
    assert (await web.post(f"/api/vehicles/{VIN}/refuels", json={"liters": 40, "total_cost": 66, "price_per_liter": 1.6})).status_code == 422
    assert (await web.post(f"/api/vehicles/{VIN}/refuels", json={"liters": 0})).status_code == 422
    assert (await web.post("/api/vehicles/NOEXISTE/refuels", json={"liters": 10})).status_code == 404


async def test_a_detected_refuel_is_completed_with_the_price_and_the_real_litres(web) -> None:
    t = D(2026, 10, 28, 10, 0)
    await reading(t, 20.0)
    await reading(t + datetime.timedelta(minutes=30), 90.0)
    rid = (await web.get(f"/api/vehicles/{VIN}/refuels")).json()[0]["id"]
    d = (await web.put(f"/api/refuels/{rid}", json={"price_per_liter": 1.6})).json()
    assert d["total_cost"] == 56.0 and d["liters_estimated"] is True and d["needs_price"] is False
    d = (await web.put(f"/api/refuels/{rid}", json={"liters": 34.2})).json()                       # real litres from the receipt
    assert d["liters"] == 34.2 and d["total_cost"] == round(1.6 * 34.2, 2)
    d = (await web.put(f"/api/refuels/{rid}", json={"total_cost": 60.0, "station": "BP"})).json()
    assert d["price_per_liter"] == round(60 / 34.2, 3) and d["station"] == "BP"
    assert (await web.delete(f"/api/refuels/{rid}")).json()["status"] == "ok" and (await web.get(f"/api/vehicles/{VIN}/refuels")).json() == []
    assert (await web.put("/api/refuels/9999", json={"liters": 3})).status_code == 404


# ───────────────────────── costs ─────────────────────────
async def test_costs_add_up_purchase_charges_refuels_and_maintenance(web) -> None:
    await web.put(f"/api/vehicles/{VIN}", json={"purchase_price": 30000, "delivery_date": "2025-01-10T00:00:00", "purchase_odometer": 5000})
    async with async_session_factory() as s:
        s.add(ChargingSession(vin=VIN, start_time=D(2026, 9, 5), end_time=D(2026, 9, 5, 4), kwh_added=10, charge_cost=3.0, cost_mode="manual", address="Casa"))
        s.add(ChargingSession(vin=VIN, start_time=D(2026, 10, 5), end_time=D(2026, 10, 5, 4), kwh_added=8, charge_cost=0.0))          # no cost
        await s.commit()
    await web.post(f"/api/vehicles/{VIN}/refuels", json={"liters": 40, "total_cost": 60, "timestamp": "2026-10-10T00:00:00"})
    await web.post(f"/api/vehicles/{VIN}/refuels", json={"liters": 30, "timestamp": "2026-10-11T00:00:00"})                          # no price
    await web.post(f"/api/vehicles/{VIN}/maintenance/events", json={"title": "Pastillas", "cost": 120, "service_date": "2026-09-20T00:00:00"})
    await web.post(f"/api/vehicles/{VIN}/maintenance/official/complete", json={"step_km": 15000, "cost": 200, "service_date": "2026-10-15T00:00:00"})
    d = (await web.get(f"/api/vehicles/{VIN}/costs")).json()
    t = d["totals"]
    assert (t["purchase"], t["charges"], t["refuels"], t["maintenance"]) == (30000, 3.0, 60.0, 320.0)
    assert t["running"] == 383.0 and t["all"] == 30383.0
    assert d["pending"] == {"charges": 1, "refuels": 1}                                       # what has no price yet, in sight
    assert d["km"] == 10000 and d["per_100km"] == 3.83                                       # (15000 - 5000 km at purchase)
    assert d["energy"] == {"kwh": 18.0, "liters": 70.0}
    months = {m["month"]: m for m in d["monthly"]}
    assert months["2026-09"]["charges"] == 3.0 and months["2026-09"]["maintenance"] == 120.0 and months["2026-10"]["maintenance"] == 200.0
    assert [x["type"] for x in d["recent"]][:2] == ["maintenance", "refuel"] and d["recent"][0]["title"] == "Revisión oficial EBRO"


async def test_costs_by_period_and_validation(web) -> None:
    await web.put(f"/api/vehicles/{VIN}", json={"purchase_price": 30000, "delivery_date": "2020-01-10T00:00:00"})
    await web.post(f"/api/vehicles/{VIN}/refuels", json={"liters": 40, "total_cost": 60, "timestamp": "2019-10-10T00:00:00"})
    before = (await web.get(f"/api/vehicles/{VIN}/costs?period=12m")).json()["totals"]
    assert before["purchase"] == 0 and before["refuels"] == 0 and before["all"] == 0                  # the old purchase and refuel are left out
    assert (await web.get(f"/api/vehicles/{VIN}/costs?period=otro")).status_code == 422
    assert (await web.get("/api/vehicles/NOEXISTE/costs")).status_code == 404


async def test_cost_and_refuel_routes_require_a_session(db) -> None:
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://testserver") as c:
        for url in (f"/api/vehicles/{VIN}/costs", f"/api/vehicles/{VIN}/refuels"):
            assert (await c.get(url)).status_code == 401
        assert (await c.put("/api/refuels/1", json={})).status_code == 401


# ───────────────────────── tyre pressure ─────────────────────────
def test_tyres_out_of_range_are_listed_with_their_deviation() -> None:
    v = Vehicle(vin=VIN, tyre_front_pressure_bar=2.5, tyre_rear_pressure_bar=2.6, tyre_pressure_tolerance_pct=10.0)
    tel = {"tyre_pressures": {"fl": 2.5, "fr": 2.1, "rl": 2.9, "rr": 2.6}}
    out = VehiclePoller.tyre_pressure_issues(v, tel)
    assert len(out) == 2 and "Delantero derecho" in out[0] and "-16 %" in out[0] and "Trasero izquierdo" in out[1] and "+12 %" in out[1]
    assert VehiclePoller.tyre_pressure_issues(Vehicle(vin=VIN), tel) == []                    # no reference pressure, no alert
    assert VehiclePoller.tyre_pressure_issues(v, {"tyre_pressures": {"fl": None, "fr": 0}}) == []


@pytest.fixture
async def poller(db, monkeypatch):
    async with async_session_factory() as s:
        s.add(Vehicle(owner_id=1, vin=VIN, name="Ebro", tyre_front_pressure_bar=2.5, tyre_rear_pressure_bar=2.6, tyre_pressure_tolerance_pct=10.0))
        await s.commit()
    p = VehiclePoller.get_instance()
    p._pending_since.clear()
    p._last_tyre_alert_time.clear()
    alerts = []

    async def tyres(cls, name, problems, **kwargs):
        alerts.append(problems)

    monkeypatch.setattr(poller_mod.NotificationService, "notify_tyre_pressure", classmethod(tyres))
    monkeypatch.setattr(VehiclePoller, "request_poll", lambda self, vin, force=False: None)
    p.tyre_alerts = alerts
    yield p
    p._pending_since.clear()


async def check(p, at, pressures, **cfg):
    with freeze_time(at):
        async with async_session_factory() as s:
            v = (await s.execute(select(Vehicle).where(Vehicle.vin == VIN))).scalar_one()
            for k, val in cfg.items():
                setattr(v, k, val)
            await p._check_tyre_pressure(v, {"tyre_pressures": pressures})
            await s.commit()


async def test_low_pressure_alerts_after_ten_minutes_and_once_every_12_hours(poller) -> None:
    t = D(2026, 10, 28, 12, 0)
    bad = {"fl": 2.5, "fr": 2.0, "rl": 2.6, "rr": 2.6}
    await check(poller, t, bad)
    await check(poller, t + datetime.timedelta(minutes=5), bad)
    assert poller.tyre_alerts == []                                                                 # the 10 minutes have not passed yet
    await check(poller, t + datetime.timedelta(minutes=11), bad)
    assert len(poller.tyre_alerts) == 1 and "Delantero derecho" in poller.tyre_alerts[0][0]
    await check(poller, t + datetime.timedelta(hours=2), bad)
    assert len(poller.tyre_alerts) == 1                                                             # not repeated within the 12 h
    await check(poller, t + datetime.timedelta(hours=13), bad)
    assert len(poller.tyre_alerts) == 2


async def test_if_the_pressure_returns_to_normal_the_count_restarts(poller) -> None:
    t = D(2026, 10, 28, 12, 0)
    await check(poller, t, {"fr": 2.0})
    await check(poller, t + datetime.timedelta(minutes=5), {"fr": 2.5})                    # back in range
    await check(poller, t + datetime.timedelta(minutes=8), {"fr": 2.0})                    # out again: counts from zero
    await check(poller, t + datetime.timedelta(minutes=15), {"fr": 2.0})
    assert poller.tyre_alerts == []


# ───────────────────────── powertrain type ─────────────────────────
async def test_the_powertrain_type_is_saved_validated_and_has_a_default_per_brand(web) -> None:
    assert (await web.get("/api/vehicles")).json()[0]["fuel_type"] == "phev"                 # existing cars stay PHEV
    r = await web.put(f"/api/vehicles/{VIN}", json={"fuel_type": "diesel"})
    assert r.status_code == 200 and r.json()["fuel_type"] == "diesel"
    assert (await web.put(f"/api/vehicles/{VIN}", json={"fuel_type": "nuclear"})).status_code == 422
    assert (await web.put(f"/api/vehicles/{VIN}", json={"name": "Solo el nombre"})).json()["fuel_type"] == "diesel"     # untouched when not sent
    ebro = (await web.post("/api/vehicles", json={"name": "Otro Ebro", "brand": "EBRO", "model": "S700 PHEV"})).json()
    toyota = (await web.post("/api/vehicles", json={"name": "Corolla", "brand": "Toyota", "model": "Corolla"})).json()
    tesla = (await web.post("/api/vehicles", json={"name": "Model 3", "brand": "Tesla", "model": "Model 3", "fuel_type": "ev"})).json()
    assert (ebro["fuel_type"], toyota["fuel_type"], tesla["fuel_type"]) == ("phev", "gasoline", "ev")
