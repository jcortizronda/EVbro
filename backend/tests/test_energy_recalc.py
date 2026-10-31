"""A new battery capacity in the car sheet re-estimates the energy of past drives and charges (route hook and migration 0017)."""
from __future__ import annotations

import asyncio
import datetime

import httpx
import pytest
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import create_async_engine

from app.db import migrate
from app.db.models import AppUser, ChargingSession, Drive, Vehicle
from app.db.session import async_session_factory
from app.main import app
from app.security import auth, passwords

VIN = "LSJA0000000000017"
T0 = datetime.datetime(2026, 9, 1, 8, 0)


class _Req:
    client = type("C", (), {"host": "127.0.0.1"})()
    headers = {"user-agent": "t"}


@pytest.fixture
async def car(db):
    async with async_session_factory() as s:
        user = AppUser(id=1, role="manager", username="juan", vehicle_limit=-1, password_hash=passwords.hash_password("una-contraseña-larga-1"))
        s.add(user)
        s.add(Vehicle(owner_id=1, vin=VIN, name="S900", battery_capacity_kwh=None))              # no capacity yet: rows below used the default
        s.add_all([
            Drive(vin=VIN, start_time=T0, end_time=T0 + datetime.timedelta(hours=1), distance_km=40, battery_used_pct=50.0, energy_consumed_kwh=9.64),
            Drive(vin=VIN, start_time=T0 + datetime.timedelta(days=1), end_time=T0 + datetime.timedelta(days=1, hours=1), distance_km=5,
                  battery_used_pct=0.0, energy_consumed_kwh=0.0),
            # priced per kWh by a geofence tariff: the cost follows the kWh
            ChargingSession(vin=VIN, start_time=T0 + datetime.timedelta(hours=2), end_time=T0 + datetime.timedelta(hours=6), start_battery_pct=40,
                            end_battery_pct=90, kwh_added=9.64, charge_cost=1.45, effective_cost_per_kwh=0.15, cost_mode="fixed"),
            # metered at the wall: the paid energy is known, the cost must not move
            ChargingSession(vin=VIN, start_time=T0 + datetime.timedelta(days=1, hours=2), end_time=T0 + datetime.timedelta(days=1, hours=6),
                            start_battery_pct=20, end_battery_pct=70, kwh_added=9.64, kwh_used=18.2, charge_cost=2.73, effective_cost_per_kwh=0.15, cost_mode="manual"),
            # no battery readings (entered by hand): untouched
            ChargingSession(vin=VIN, start_time=T0 + datetime.timedelta(days=2), end_time=T0 + datetime.timedelta(days=2, hours=1), kwh_added=7.0, charge_cost=1.0),
        ])
        await s.commit()
        token = await auth.create_session(s, user, _Req())
    c = httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://testserver", headers={"Origin": "http://testserver"})
    c.cookies.set(auth.COOKIE_NAME, token)
    yield c
    await c.aclose()


async def _rows():
    async with async_session_factory() as s:
        drives = [(d.energy_consumed_kwh) for d in (await s.execute(select(Drive).where(Drive.vin == VIN).order_by(Drive.start_time))).scalars()]
        charges = [(c.kwh_added, c.charge_cost) for c in (await s.execute(select(ChargingSession).where(ChargingSession.vin == VIN)
                                                                            .order_by(ChargingSession.start_time))).scalars()]
    return drives, charges


async def test_saving_a_new_capacity_reestimates_drives_and_charges(car) -> None:
    r = await car.put(f"/api/vehicles/{VIN}", json={"battery_capacity_kwh": 34.5})
    assert r.status_code == 200, r.text
    drives, charges = await _rows()
    assert drives == [17.25, 0.0]                                   # 50 % × 34.5
    assert charges[0] == (17.25, 2.59)                              # kWh and cost follow the capacity (0.15 €/kWh)
    assert charges[1] == (17.25, 2.73)                              # metered: cost kept
    assert charges[2] == (7.0, 1.0)                                 # no battery readings: untouched
    # saving the sheet again with the same capacity changes nothing more
    await car.put(f"/api/vehicles/{VIN}", json={"battery_capacity_kwh": 34.5, "color": "Gris"})
    assert (await _rows())[0] == [17.25, 0.0]


async def test_0017_reestimates_with_the_capacity_already_in_the_sheet(tmp_path) -> None:
    url = f"sqlite+aiosqlite:///{tmp_path}/recalc.db"
    from alembic import command
    await asyncio.to_thread(command.upgrade, migrate.alembic_config(url), "0016_odometer_distance")
    engine = create_async_engine(url)
    try:
        async with engine.begin() as conn:
            # the column is NOT NULL (19.27 by default), so every car gets re-estimated with whatever its sheet says
            await conn.execute(text("INSERT INTO vehicles (vin, name, battery_capacity_kwh) VALUES ('A', 'S900', 34.5), ('B', 'Otro', 20.0)"))
            await conn.execute(text("INSERT INTO drives (vin, start_time, battery_used_pct, energy_consumed_kwh) VALUES "
                                    "('A', '2026-08-01 06:00:00', 50, 9.64), ('B', '2026-08-01 06:00:00', 50, 9.64)"))
            await conn.execute(text("INSERT INTO charging_sessions (vin, start_time, start_battery_pct, end_battery_pct, kwh_added, kwh_used, charge_cost, effective_cost_per_kwh) VALUES "
                                    "('A', '2026-08-01 08:00:00', 40, 90, 9.64, NULL, 1.45, 0.15), "
                                    "('A', '2026-08-02 08:00:00', 20, 70, 9.64, 18.2, 2.73, 0.15), "
                                    "('A', '2026-08-03 08:00:00', NULL, NULL, 7.0, NULL, 1.0, NULL)"))
        await migrate.upgrade_to_head(url)
        async with engine.connect() as conn:
            drives = (await conn.execute(text("SELECT vin, energy_consumed_kwh FROM drives ORDER BY vin"))).all()
            charges = (await conn.execute(text("SELECT kwh_added, charge_cost FROM charging_sessions ORDER BY start_time"))).all()
    finally:
        await engine.dispose()
    assert [tuple(r) for r in drives] == [("A", 17.25), ("B", 10.0)]
    assert [tuple(r) for r in charges] == [(17.25, 2.59), (17.25, 2.73), (7.0, 1.0)]
