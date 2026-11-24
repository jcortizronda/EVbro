"""Electric kilometres from the car's EV odometer, and electric consumption only over fully electric drives.

The car counts apart the km driven with the petrol engine off («Cuentakilómetros EV», `electricRange`). When the engine runs it
also charges the battery, so the battery points spent understate what those km cost: those drives stay out of the kWh/100 km.
"""
from __future__ import annotations

import asyncio
import datetime

import httpx
import pytest
from freezegun import freeze_time
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import create_async_engine

from app.db import migrate
from app.db.models import AppUser, Drive, Vehicle
from app.db.session import async_session_factory
from app.main import app
from app.security import auth, passwords
from app.services import stats
from app.services import trip_detector as td
from app.services.trip_detector import TripDetector

VIN = "LSJA0000000000029"
D = datetime.datetime


def drive(**kw) -> Drive:
    base = {"vin": VIN, "distance_km": 20.0, "energy_consumed_kwh": 4.0, "fuel_used_l": 0.0}
    base.update(kw)
    return Drive(**base)


# ───────────────────────── rules ─────────────────────────
def test_ev_km_is_the_raw_difference_of_the_ev_odometer() -> None:
    assert stats.ev_km(drive(start_ev_odometer=6600, end_ev_odometer=6612)) == 12
    # the two counters tick about half a km apart: an all-electric trip can give one more or one less, kept as is for the sums
    assert stats.ev_km(drive(distance_km=3.0, start_ev_odometer=6648, end_ev_odometer=6652)) == 4
    assert stats.ev_km(drive(distance_km=3.0, start_ev_odometer=6649, end_ev_odometer=6651)) == 2
    assert stats.ev_km(drive(start_ev_odometer=6600, end_ev_odometer=6650)) is None        # does not fit the trip
    assert stats.ev_km(drive(start_ev_odometer=None, end_ev_odometer=6612)) is None
    assert stats.ev_km(drive(start_ev_odometer=0, end_ev_odometer=0)) is None              # a car that does not report it


def test_electric_only_comes_from_the_tank_not_the_ev_odometer() -> None:
    assert stats.electric_only(drive(fuel_used_l=0.0, start_ev_odometer=100, end_ev_odometer=110)) is True   # 10 of 20: counter noise ignored
    assert stats.electric_only(drive(fuel_used_l=1.2)) is False
    assert stats.electric_only(drive(fuel_used_l=None)) is None


def test_a_drive_with_petrol_does_not_count_for_the_electric_consumption() -> None:
    assert stats.counts_for_ev_consumption(drive()) is True
    assert stats.counts_for_ev_consumption(drive(fuel_used_l=0.7)) is False
    assert stats.counts_for_ev_consumption(drive(distance_km=1.0)) is False


# ───────────────────────── trip detector ─────────────────────────
@pytest.fixture
async def car(db, monkeypatch):
    for d in (td._ACTIVE_DRIVES, td._LAST_MOTION_TIME, td._LAST_IDLE, td._HV_OFF_AT, td._LAST_CLOSED, td._STALE_CABLE):
        d.clear()

    async def no_geofence(session, lat, lon, **kwargs):
        return None

    async def address(lat, lon):
        return "Calle Mayor"

    monkeypatch.setattr(td, "find_geofence", no_geofence)
    monkeypatch.setattr(td, "reverse_geocode", address)
    async with async_session_factory() as s:
        s.add(Vehicle(owner_id=1, vin=VIN, name="Ebro", odometer=10950.0))
        await s.commit()
    yield VIN
    for d in (td._ACTIVE_DRIVES, td._LAST_MOTION_TIME, td._LAST_IDLE, td._HV_OFF_AT, td._LAST_CLOSED, td._STALE_CABLE):
        d.clear()


async def process(telemetry, at):
    with freeze_time(at):
        async with async_session_factory() as s:
            v = (await s.execute(select(Vehicle).where(Vehicle.vin == VIN))).scalar_one()
            await TripDetector.process_snapshot(s, v, telemetry)
            await s.commit()


def reading(speed, odo, ev, pos=(40.0, -3.0)):
    return {"speed_kmh": speed, "odometer_km": odo, "ev_odometer_km": ev, "latitude": pos[0], "longitude": pos[1], "battery_level": 70.0,
            "fuel_level": 50.0, "state": "driving" if speed > 2 else "parked"}


async def test_the_detector_stores_the_ev_odometer_at_both_ends(car) -> None:
    t0 = D(2026, 11, 8, 17, 30)
    await process(reading(0, 10950, 6600), t0)
    await process(reading(50, 10952, 6602, (40.01, -3.0)), t0 + datetime.timedelta(minutes=3))   # detected moving: start from the parked reading
    await process(reading(0, 10965, 6611, (40.1, -3.0)), t0 + datetime.timedelta(minutes=20))
    await process(reading(0, 10965, 6611, (40.1, -3.0)), t0 + datetime.timedelta(minutes=40))
    async with async_session_factory() as s:
        (d,) = (await s.execute(select(Drive))).scalars().all()
    assert (d.start_odometer, d.start_ev_odometer) == (10950, 6600)
    assert (d.end_odometer, d.end_ev_odometer) == (10965, 6611)
    assert stats.ev_km(d) == 11


# ───────────────────────── API ─────────────────────────
class _Req:
    client = type("C", (), {"host": "127.0.0.1"})()
    headers = {"user-agent": "t"}


@pytest.fixture
async def web(db):
    now = D.utcnow().replace(minute=0, second=0, microsecond=0)
    month = (now.replace(day=1, hour=12) - datetime.timedelta(days=1)).replace(day=15)      # middle of last month
    async with async_session_factory() as s:
        s.add(Vehicle(owner_id=1, vin=VIN, name="Ebro", battery_capacity_kwh=20.0))
        user = AppUser(id=1, role="manager", username="juan", vehicle_limit=-1, password_hash=passwords.hash_password("una-contraseña-larga-1"))
        s.add(user)
        s.add_all([
            # electric: 20 km, 4 kWh → 20 kWh/100 km
            drive(start_time=month, end_time=month + datetime.timedelta(hours=1), start_ev_odometer=1000, end_ev_odometer=1020),
            # hybrid: 40 km, 10 of them electric; the engine charged the battery, so only 2 kWh net. Out of the consumption
            drive(start_time=month + datetime.timedelta(days=1), end_time=month + datetime.timedelta(days=1, hours=1), distance_km=40,
                  energy_consumed_kwh=2.0, fuel_used_l=2.0, start_ev_odometer=1020, end_ev_odometer=1030),
            # without EV odometer (older readings): its km are not split
            drive(start_time=month + datetime.timedelta(days=2), end_time=month + datetime.timedelta(days=2, hours=1), distance_km=10,
                  energy_consumed_kwh=2.0),
        ])
        await s.commit()
        token = await auth.create_session(s, user, _Req())
    c = httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://testserver")
    c.cookies.set(auth.COOKIE_NAME, token)
    yield c
    await c.aclose()


async def test_stats_split_the_km_and_keep_hybrid_drives_out_of_the_consumption(web) -> None:
    d = (await web.get(f"/api/vehicles/{VIN}/stats")).json()
    (m,) = d["monthly"]
    assert m["km"] == 70 and m["ev_km"] == 30 and m["fuel_km"] == 30 and m["ev_pct"] == 50
    assert m["kwh_per_100km"] == 20.0                       # (4 + 2) / (20 + 10): the hybrid drive's 2 kWh / 40 km stay out
    assert d["totals"]["ev_km"] == 30 and d["totals"]["ev_pct"] == 50 and d["totals"]["kwh_per_100km"] == 20.0


async def test_the_trip_list_gives_no_consumption_for_drives_with_petrol_and_no_ev_km_per_trip(web) -> None:
    body = (await web.get(f"/api/vehicles/{VIN}/drives")).json()
    by_km = {x["distance_km"]: x for x in body["drives"]}
    assert all("ev_km" not in x for x in body["drives"])         # too coarse for one trip: only sums show it
    assert by_km[20.0]["electric_only"] is True and by_km[20.0]["consumption_kwh_100km"] == 20.0
    assert by_km[40.0]["electric_only"] is False and by_km[40.0]["consumption_kwh_100km"] is None
    assert by_km[10.0]["electric_only"] is True
    assert body["summary"]["avg_consumption_wh_km"] == 200.0


# ───────────────────────── migration ─────────────────────────
@pytest.fixture
def scratch_url(tmp_path) -> str:
    return f"sqlite+aiosqlite:///{tmp_path}/migrations.db"


async def test_0029_fills_past_drives_from_the_stored_readings(scratch_url) -> None:
    from alembic import command
    await asyncio.to_thread(command.upgrade, migrate.alembic_config(scratch_url), "0028_user_notifications_seen")
    engine = create_async_engine(scratch_url)
    try:
        async with engine.begin() as conn:
            await conn.execute(text(f"INSERT INTO vehicles (vin, name) VALUES ('{VIN}', 'Coche')"))
            await conn.execute(text(
                "INSERT INTO drives (id, vin, start_time, end_time, distance_km, start_odometer, end_odometer) VALUES "
                f"(1, '{VIN}', '2026-11-08 17:30:00', '2026-11-08 17:50:00', 15, 10950, 10965), "     # readings at both ends
                f"(2, '{VIN}', '2026-11-09 07:00:00', '2026-11-09 07:20:00', 10, 10965, 10975), "     # no reading at the end
                f"(3, '{VIN}', '2026-11-09 11:00:00', '2026-11-09 11:20:00', 5, 10975, 10980)"))      # readings that do not fit
            await conn.execute(text(
                "INSERT INTO telemetry_snapshots (vin, timestamp, odometer_km, raw_data) VALUES "
                f"('{VIN}', '2026-11-08 17:29:00', 10950, '{{\"electricRange\": \"6600\"}}'), "
                f"('{VIN}', '2026-11-08 17:51:00', 10965, '{{\"electricRange\": \"6611\"}}'), "
                f"('{VIN}', '2026-11-09 06:59:00', 10965, '{{\"electricRange\": \"6611\"}}'), "
                f"('{VIN}', '2026-11-09 10:59:00', 10975, '{{\"electricRange\": \"6620\"}}'), "
                f"('{VIN}', '2026-11-09 11:21:00', 10980, '{{\"electricRange\": \"6640\"}}')"))
        await migrate.upgrade_to_head(scratch_url)
        async with engine.connect() as conn:
            rows = (await conn.execute(text("SELECT id, start_ev_odometer, end_ev_odometer FROM drives ORDER BY id"))).all()
    finally:
        await engine.dispose()
    assert [tuple(r) for r in rows] == [(1, 6600.0, 6611.0), (2, None, None), (3, None, None)]


# ───────────────────────── car book ─────────────────────────
async def test_the_car_book_carries_the_battery_health_and_the_ev_share(db, monkeypatch) -> None:
    from app.db.models import ChargePoint, ChargingSession
    from app.services import battery_capacity, car_book

    t0 = D(2026, 10, 1, 20)
    async with async_session_factory() as s:
        s.add(Vehicle(owner_id=1, vin=VIN, name="Ebro", battery_capacity_kwh=34.5))
        s.add(drive(start_time=t0, end_time=t0 + datetime.timedelta(hours=1), start_odometer=10950, end_odometer=10970,
                    start_ev_odometer=6600, end_ev_odometer=6620))
        s.add(ChargingSession(id=7, vin=VIN, start_time=t0 + datetime.timedelta(hours=2), end_time=t0 + datetime.timedelta(hours=8),
                              start_battery_pct=20, end_battery_pct=90, kwh_added=24.0))
        s.add(ChargePoint(charging_session_id=7, timestamp=t0 + datetime.timedelta(hours=3), battery_pct=40.0, power_kw=3.6))
        await s.commit()
    monkeypatch.setattr(battery_capacity, "measured_capacity_kwh", lambda points: 33.8 if points else None)
    async with async_session_factory() as s:
        vehicle = (await s.execute(select(Vehicle).where(Vehicle.vin == VIN))).scalar_one()
        data = await car_book.collect(s, vehicle, ["drives", "charges"])
        pdf = await car_book.build(s, vehicle, ["drives", "charges"])
    assert data["battery_summary"] == {"nominal_kwh": 34.5, "measured_kwh": 33.8, "charges": 1, "pct_of_nominal": 98.0}
    assert pdf.startswith(b"%PDF")
    flow = car_book._battery_health(data, 500) + car_book._drives(data, 500)
    assert any("Salud de la batería" in getattr(f, "text", "") for f in flow)


async def test_0030_takes_the_reading_after_the_stop_not_the_last_one_on_the_move(scratch_url) -> None:
    """The real case of 11-11-2026: odometer 10983 reached at 14:29:59 on the move (EV 6651), the car stopped at 14:31 (EV 6652)."""
    from alembic import command
    await asyncio.to_thread(command.upgrade, migrate.alembic_config(scratch_url), "0029_drive_ev_odometer")
    engine = create_async_engine(scratch_url)
    try:
        async with engine.begin() as conn:
            await conn.execute(text(f"INSERT INTO vehicles (vin, name) VALUES ('{VIN}', 'Coche')"))
            await conn.execute(text(
                "INSERT INTO drives (id, vin, start_time, end_time, distance_km, start_odometer, end_odometer, start_ev_odometer, end_ev_odometer) "
                f"VALUES (1, '{VIN}', '2026-11-11 13:20:00', '2026-11-11 13:31:00', 3, 10980, 10983, 6648, 6651)"))
            await conn.execute(text(
                "INSERT INTO telemetry_snapshots (vin, timestamp, odometer_km, raw_data) VALUES "
                f"('{VIN}', '2026-11-11 12:40:02', 10980, '{{\"electricRange\": \"6648\"}}'), "
                f"('{VIN}', '2026-11-11 13:21:02', 10980, '{{\"electricRange\": \"6649\"}}'), "
                f"('{VIN}', '2026-11-11 13:29:59', 10983, '{{\"electricRange\": \"6651\"}}'), "
                f"('{VIN}', '2026-11-11 13:31:00', 10983, '{{\"electricRange\": \"6652\"}}')"))
        await migrate.upgrade_to_head(scratch_url)
        async with engine.connect() as conn:
            row = (await conn.execute(text("SELECT start_ev_odometer, end_ev_odometer FROM drives WHERE id = 1"))).one()
    finally:
        await engine.dispose()
    assert tuple(row) == (6648.0, 6652.0)       # the parked reading before the start and the one at the stop: 4 EV km for 3 km
