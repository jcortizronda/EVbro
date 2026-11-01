"""A geofence created after the fact names the past charges and drives inside it, and never changes their prices."""
from __future__ import annotations

import datetime

import httpx
import pytest
from sqlalchemy import select

from app.db.models import AppUser, ChargingSession, Drive, Geofence, Vehicle
from app.db.session import async_session_factory
from app.main import app
from app.security import auth, passwords

VIN = "LSJA0000000000003"
HOME = (37.3800, -5.9800)
D = datetime.datetime


class _Req:
    client = type("C", (), {"host": "127.0.0.1"})()
    headers = {"user-agent": "t"}


@pytest.fixture
async def web(db):
    async with async_session_factory() as s:
        s.add(AppUser(id=1, role="manager", username="juan", vehicle_limit=-1, password_hash=passwords.hash_password("una-contraseña-larga-1")))
        s.add(Vehicle(owner_id=1, vin=VIN, name="Ebro", battery_capacity_kwh=20.0))
        other = Geofence(owner_id=1, name="Trabajo", latitude=40.0, longitude=-3.0, radius_meters=100)
        s.add(other)
        await s.flush()
        near = (HOME[0] + 0.0003, HOME[1])                                 # ~33 m from the centre
        s.add_all([
            ChargingSession(id=1, vin=VIN, start_time=D(2026, 10, 1, 22), end_time=D(2026, 10, 2, 2), latitude=near[0], longitude=near[1],
                            address="Calle Tal, 3", kwh_added=10.0, charge_cost=0.0, effective_cost_per_kwh=0.0, cost_mode="default"),
            ChargingSession(id=2, vin=VIN, start_time=D(2026, 10, 5, 22), end_time=D(2026, 10, 6, 2), latitude=near[0], longitude=near[1],
                            address="Calle Tal, 3", kwh_added=8.0, charge_cost=1.2, effective_cost_per_kwh=0.15, cost_mode="manual"),
            ChargingSession(id=3, vin=VIN, start_time=D(2026, 10, 7, 22), end_time=D(2026, 10, 8, 2), latitude=near[0], longitude=near[1],
                            address="Trabajo", geofence_id=other.id, kwh_added=5.0, charge_cost=0.5, cost_mode="fixed"),
            ChargingSession(id=4, vin=VIN, start_time=D(2026, 10, 9, 22), end_time=D(2026, 10, 10, 2), latitude=HOME[0] + 0.01, longitude=HOME[1],
                            address="Lejos", kwh_added=5.0, charge_cost=0.0, cost_mode="default"),
            Drive(id=1, vin=VIN, start_time=D(2026, 10, 3, 9), end_time=D(2026, 10, 3, 9, 30), start_lat=near[0], start_lon=near[1],
                  start_address="Calle Tal, 3", end_lat=HOME[0] + 0.05, end_lon=HOME[1], end_address="Centro"),
        ])
        await s.commit()
        token = await auth.create_session(s, await s.get(AppUser, 1), _Req())
    c = httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://testserver", headers={"Origin": "http://testserver"})
    c.cookies.set(auth.COOKIE_NAME, token)
    yield c
    await c.aclose()


async def test_a_new_geofence_names_the_past_but_keeps_every_price(web) -> None:
    r = await web.post("/api/geofences", json={"name": "Casa", "latitude": HOME[0], "longitude": HOME[1], "radius_meters": 100,
                                               "is_charging_spot": True, "cost_mode": "fixed", "cost_per_kwh": 0.12})
    assert r.status_code == 200 and r.json()["applied"] == {"charges": 2, "drives": 1}
    gid = r.json()["id"]
    async with async_session_factory() as s:
        c = {x.id: x for x in (await s.execute(select(ChargingSession))).scalars()}
        d = await s.get(Drive, 1)
    assert (c[1].geofence_id, c[1].address, c[1].charge_cost, c[1].cost_mode) == (gid, "Casa", 0.0, "default")     # price unchanged
    assert (c[2].geofence_id, c[2].charge_cost, c[2].cost_mode) == (gid, 1.2, "manual")
    assert c[3].address == "Trabajo" and c[3].geofence_id != gid          # already another geofence's: kept
    assert c[4].geofence_id is None and c[4].address == "Lejos"           # outside
    assert (d.start_geofence_id, d.start_address, d.end_geofence_id, d.end_address) == (gid, "Casa", None, "Centro")


async def test_editing_the_tariff_later_does_not_reprice_the_past(web) -> None:
    gid = (await web.post("/api/geofences", json={"name": "Casa", "latitude": HOME[0], "longitude": HOME[1], "radius_meters": 100,
                                                  "cost_mode": "fixed", "cost_per_kwh": 0.12})).json()["id"]
    r = await web.put(f"/api/geofences/{gid}", json={"cost_per_kwh": 0.30})
    assert r.status_code == 200 and r.json()["applied"] == {"charges": 0, "drives": 0}
    async with async_session_factory() as s:
        assert (await s.get(ChargingSession, 1)).charge_cost == 0.0


async def test_a_geofence_that_is_not_a_charging_spot_only_takes_drives(web) -> None:
    r = await web.post("/api/geofences", json={"name": "Casa", "latitude": HOME[0], "longitude": HOME[1], "radius_meters": 100, "is_charging_spot": False})
    assert r.json()["applied"] == {"charges": 0, "drives": 1}
