"""Manual charge cost and default price, efficiency without made-up figures, history retention and door alarms with confirmation."""
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


async def add_charge(kwh=10.0, **extra) -> int:
    async with async_session_factory() as s:
        c = ChargingSession(vin=VIN, start_time=D(2026, 10, 1, 20), end_time=D(2026, 10, 2, 2), start_battery_pct=20.0, end_battery_pct=70.0,
                            kwh_added=kwh, duration_seconds=21600, **extra)
        s.add(c)
        await s.commit()
        return c.id


# ───────────────────────── charge cost ─────────────────────────
async def test_price_per_kwh_computes_the_cost_on_the_added_energy(web) -> None:
    cid = await add_charge(10.0)
    r = await web.put(f"/api/charges/{cid}/cost", json={"price_per_kwh": 0.2})
    c = r.json()
    assert r.status_code == 200 and c["charge_cost"] == 2.0 and c["effective_cost_per_kwh"] == 0.2 and c["cost_mode"] == "manual"
    assert c["cost_source"] == "Introducida por ti" and c["has_cost"] is True


async def test_total_cost_derives_the_effective_price(web) -> None:
    cid = await add_charge(10.0)
    c = (await web.put(f"/api/charges/{cid}/cost", json={"total_cost": 3.0})).json()
    assert c["charge_cost"] == 3.0 and c["effective_cost_per_kwh"] == 0.3


async def test_meter_kwh_give_the_real_efficiency_and_the_cost_uses_grid_energy(web) -> None:
    cid = await add_charge(10.0)
    without = (await web.get(f"/api/charges/{cid}")).json()["charge"]
    assert without["kwh_used"] is None and without["efficiency_pct"] is None            # no meter: unknown, NOT 95 %
    c = (await web.put(f"/api/charges/{cid}/cost", json={"price_per_kwh": 0.2, "kwh_meter": 12.5})).json()
    assert c["kwh_used"] == 12.5 and c["efficiency_pct"] == 80.0 and c["charge_cost"] == 2.5     # 12.5 kWh paid × 0.20
    c = (await web.put(f"/api/charges/{cid}/cost", json={"kwh_meter": 12.0})).json()               # only the kWh change: recomputed
    assert c["charge_cost"] == 2.4 and c["efficiency_pct"] == 83.3


async def test_cost_validations(web) -> None:
    cid = await add_charge()
    assert (await web.put(f"/api/charges/{cid}/cost", json={})).status_code == 422
    assert (await web.put(f"/api/charges/{cid}/cost", json={"price_per_kwh": 0.2, "total_cost": 3})).status_code == 422
    assert (await web.put(f"/api/charges/{cid}/cost", json={"price_per_kwh": -1})).status_code == 422
    assert (await web.put("/api/charges/9999/cost", json={"price_per_kwh": 0.2})).status_code == 404


async def test_a_charge_without_tariff_is_shown_without_cost(web) -> None:
    cid = await add_charge()
    c = (await web.get(f"/api/charges/{cid}")).json()["charge"]
    assert c["has_cost"] is False and c["cost_source"] == "Sin tarifa" and c["charge_cost"] == 0.0


async def test_the_detector_sets_0_euros_per_kwh_outside_geofences(db, monkeypatch) -> None:
    async with async_session_factory() as s:
        s.add(Vehicle(owner_id=1, vin=VIN, name="Ebro", battery_capacity_kwh=20.0))
        await s.commit()
    cd._ACTIVE_CHARGES.clear()

    async def no_geofence(*a, **k):
        return None

    async def address(lat, lon):
        return "Mi garaje"

    monkeypatch.setattr(cd, "find_geofence", no_geofence)
    monkeypatch.setattr(cd, "reverse_geocode", address)

    async def reading(at, soc, charging, plug=True, kw=7.0):
        with freeze_time(at):
            async with async_session_factory() as s:
                v = (await s.execute(select(Vehicle).where(Vehicle.vin == VIN))).scalar_one()
                res = await ChargeDetector.process_snapshot(s, v, {"is_charging": charging, "cable_connected": plug, "battery_level": soc,
                                                                  "charge_power_kw": kw, "latitude": 37.0, "longitude": -6.0})
                await s.commit()
                return res

    await reading(D(2026, 10, 1, 22), 30.0, True)
    end = await reading(D(2026, 10, 2, 1), 80.0, False, kw=0.0)
    assert end.kwh_added == 10.0                       # 50 % of 20 kWh (the vehicle's capacity, not a fixed 19.27)
    assert end.kwh_used is None                        # grid energy is no longer made up
    assert end.cost_mode == "default" and end.effective_cost_per_kwh == 0.0 and end.charge_cost == 0.0       # 0 €/kWh by default


# ───────────────────────── retention ─────────────────────────
async def _seed_data():
    now = D(2026, 10, 28)
    async with async_session_factory() as s:
        if (await s.execute(select(Vehicle).where(Vehicle.vin == VIN))).scalar_one_or_none() is None:
            s.add(Vehicle(owner_id=1, vin=VIN, name="Ebro"))
        await s.flush()
        for days, closed in ((400, True), (100, True), (400, False)):
            d = Drive(vin=VIN, start_time=now - datetime.timedelta(days=days), end_time=(now - datetime.timedelta(days=days, minutes=-10)) if closed else None)
            s.add(d)
            await s.flush()
            s.add(DrivePosition(drive_id=d.id, timestamp=d.start_time, latitude=1.0, longitude=1.0))
        for days in (400, 100):
            c = ChargingSession(vin=VIN, start_time=now - datetime.timedelta(days=days), end_time=now - datetime.timedelta(days=days, minutes=-60), kwh_added=5)
            s.add(c)
            await s.flush()
            s.add(ChargePoint(charging_session_id=c.id, timestamp=c.start_time, battery_pct=50.0))
        for days in (500, 200, 1):
            s.add(TelemetrySnapshot(vin=VIN, timestamp=now - datetime.timedelta(days=days), state="parked"))
        await s.commit()
    return now


async def count_rows(model) -> int:
    async with async_session_factory() as s:
        return (await s.execute(select(func.count()).select_from(model))).scalar_one()


async def test_by_default_trips_and_charges_are_kept_forever_and_raw_telemetry_one_year(db) -> None:
    now = await _seed_data()
    async with async_session_factory() as s:
        assert await retention.get_config(s) == {"trips_days": 0, "charges_days": 0, "snapshots_days": 365}
        deleted = await retention.apply(s, now)
    assert deleted == {"trips": 0, "charges": 0, "snapshots": 1}                     # only the reading from 500 days ago
    assert await count_rows(Drive) == 3 and await count_rows(ChargingSession) == 2


async def test_retention_deletes_only_what_is_closed_and_old_with_its_points(db) -> None:
    now = await _seed_data()
    async with async_session_factory() as s:
        await retention.set_config(s, {"trips_days": 365, "charges_days": 365, "snapshots_days": 0})
        deleted = await retention.apply(s, now)
    assert deleted == {"trips": 1, "charges": 1, "snapshots": 0}
    assert await count_rows(Drive) == 2                      # the 100-day one and the OPEN 400-day one remain (what has not closed is left alone)
    assert await count_rows(DrivePosition) == 2 and await count_rows(ChargePoint) == 1               # no orphan points
    assert await count_rows(TelemetrySnapshot) == 3                                               # 0 = forever


@pytest.fixture
async def console(db):
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


async def test_retention_api(console) -> None:
    await _seed_data()
    r = await console.get("/api/settings/retention")
    d = r.json()
    assert d["config"] == {"trips_days": 0, "charges_days": 0, "snapshots_days": 365} and d["stored"]["trips"]["count"] == 3
    d = (await console.put("/api/settings/retention", json={"trips_days": 90})).json()
    assert d["config"]["trips_days"] == 90 and d["config"]["snapshots_days"] == 365          # what is not sent is kept
    assert (await console.put("/api/settings/retention", json={"trips_days": -1})).status_code == 422
    assert (await console.put("/api/settings/retention", json={"charges_days": 99999})).status_code == 422


async def test_apply_now_returns_what_was_deleted(console) -> None:
    await _seed_data()
    await console.put("/api/settings/retention", json={"charges_days": 30})
    d = (await console.post("/api/settings/retention/run")).json()
    assert d["deleted"]["charges"] == 2 and d["stored"]["charges"]["count"] == 0


async def test_retention_requires_an_admin_session_and_is_not_in_the_app(db) -> None:
    from app.admin_app import admin_app
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=admin_app), base_url="http://testserver") as c:
        assert (await c.get("/api/settings/retention")).status_code == 401
        assert (await c.post("/api/settings/retention/run")).status_code == 401
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://testserver") as c:
        assert (await c.get("/api/settings/retention")).status_code in (401, 404)


# ───────────────────────── alarms: the car must be off and the situation must persist ─────────────────────────
@pytest.fixture
async def poller(db, monkeypatch):
    async with async_session_factory() as s:
        s.add(Vehicle(owner_id=1, vin=VIN, name="Ebro"))
        await s.commit()
    p = VehiclePoller.get_instance()
    p._pending_since.clear()
    p._last_security_alert_time.clear()
    p.__dict__.pop("_last_windows_alert_time", None)
    alerts = []

    async def security(cls, name, problems, **kwargs):
        alerts.append(("security", problems))

    async def windows(cls, name, windows, **kwargs):
        alerts.append(("windows", windows))

    monkeypatch.setattr(poller_mod.NotificationService, "notify_security_alert", classmethod(security))
    monkeypatch.setattr(poller_mod.NotificationService, "notify_windows_open_alert", classmethod(windows))
    monkeypatch.setattr(VehiclePoller, "request_poll", lambda self, vin, force=False: None)
    p.alerts_sent = alerts
    yield p
    p._pending_since.clear()


async def check(p, at, **tel):
    base = {"state": "parked", "high_voltage_on": False, "is_locked": True, "doors": {"fl": 1}, "windows": {}, "odometer_km": None}
    with freeze_time(at):
        async with async_session_factory() as s:
            v = (await s.execute(select(Vehicle).where(Vehicle.vin == VIN))).scalar_one()
            await p._check_and_dispatch_alerts(s, v, {**base, **tel})
            await s.commit()


async def test_a_door_opened_on_arrival_does_not_alarm_until_it_stays_five_minutes(poller) -> None:
    t = D(2026, 10, 28, 12, 0)
    await check(poller, t)                                              # you open the door on arrival
    await check(poller, t + datetime.timedelta(minutes=2))
    assert poller.alerts_sent == []                                              # it used to alert on the first reading
    await check(poller, t + datetime.timedelta(minutes=5, seconds=1))
    assert poller.alerts_sent == [("security", ["Puertas abiertas: fl"])]


async def test_closing_the_door_earlier_gives_no_alert_and_the_count_restarts(poller) -> None:
    t = D(2026, 10, 28, 12, 0)
    await check(poller, t)
    await check(poller, t + datetime.timedelta(minutes=3), doors={"fl": 0})        # already closed
    await check(poller, t + datetime.timedelta(minutes=4))                         # opened again: the count starts from zero
    await check(poller, t + datetime.timedelta(minutes=6))
    assert poller.alerts_sent == []


async def test_with_the_car_on_it_never_alerts(poller) -> None:
    t = D(2026, 10, 28, 12, 0)
    for minutes in (0, 3, 6, 20):
        await check(poller, t + datetime.timedelta(minutes=minutes), high_voltage_on=True)   # you get out with the car running
    assert poller.alerts_sent == []


async def test_without_high_voltage_data_parked_and_the_wait_are_enough(poller) -> None:
    t = D(2026, 10, 28, 12, 0)
    await check(poller, t, high_voltage_on=None)
    await check(poller, t + datetime.timedelta(minutes=6), high_voltage_on=None)
    assert len(poller.alerts_sent) == 1


async def test_an_unlocked_car_also_waits(poller) -> None:
    t = D(2026, 10, 28, 12, 0)
    await check(poller, t, is_locked=False, doors={})
    assert poller.alerts_sent == []
    await check(poller, t + datetime.timedelta(minutes=6), is_locked=False, doors={})
    assert poller.alerts_sent == [("security", ["Vehículo sin cerrar con llave."])]


async def test_an_open_window_with_the_car_locked_alerts_after_the_wait(poller) -> None:
    t = D(2026, 10, 28, 12, 0)
    await check(poller, t, doors={}, windows={"fl": 1})
    assert poller.alerts_sent == []
    await check(poller, t + datetime.timedelta(minutes=6), doors={}, windows={"fl": 1})
    assert poller.alerts_sent == [("windows", ["Delantera Izquierda"])]


# ───────────────────────── parser ─────────────────────────
def test_high_voltage_on_comes_from_hvoltagestate() -> None:
    assert parse_telemetry_data({"hVoltageState": "1"})["high_voltage_on"] is True
    assert parse_telemetry_data({"hVoltageState": "0"})["high_voltage_on"] is False
    assert parse_telemetry_data({"hVoltageState": 1.0})["high_voltage_on"] is True
    assert parse_telemetry_data({})["high_voltage_on"] is None


# ───────────────────────── AC or DC ─────────────────────────
async def test_a_charge_is_ac_unless_the_car_says_dc_and_it_can_be_corrected(web, monkeypatch) -> None:
    from freezegun import freeze_time

    from app.services import charge_detector as cd

    async def no_geofence(*a, **k):
        return None

    async def address(lat, lon):
        return "Área de servicio"

    monkeypatch.setattr(cd, "find_geofence", no_geofence)
    monkeypatch.setattr(cd, "reverse_geocode", address)

    async def reading(at, soc, charging, plug=True, kw=7.0, fast=False):
        with freeze_time(at):
            async with async_session_factory() as s:
                v = (await s.execute(select(Vehicle).where(Vehicle.vin == VIN))).scalar_one()
                res = await ChargeDetector.process_snapshot(s, v, {"is_charging": charging, "cable_connected": plug, "battery_level": soc,
                                                                  "charge_power_kw": kw, "fast_charging": fast, "latitude": 37.0, "longitude": -6.0})
                await s.commit()
                return res

    # 1. a plain 7 kW charge: AC
    await reading(D(2026, 10, 1, 22), 30.0, True)
    ac = await reading(D(2026, 10, 2, 1), 80.0, False, kw=0.0)
    assert ac.charge_type == "ac"
    # 2. the fast gun shows up mid-session: DC
    await reading(D(2026, 10, 3, 10), 20.0, True)
    await reading(D(2026, 10, 3, 10, 5), 30.0, True, kw=8.0, fast=True)
    dc = await reading(D(2026, 10, 3, 10, 40), 80.0, False, kw=0.0)
    assert dc.charge_type == "dc"
    # 3. a power beyond AC levels is DC even without the flag
    await reading(D(2026, 10, 4, 10), 20.0, True, kw=45.0)
    fast = await reading(D(2026, 10, 4, 10, 30), 70.0, False, kw=0.0)
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
    old = await add_charge()
    assert (await web.get(f"/api/charges/{old}")).json()["charge"]["charge_type"] == "ac"
