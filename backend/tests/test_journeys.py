"""Journeys: a named span of days adds up the drives, charges, refuels, expenses and workshop visits recorded inside it."""
from __future__ import annotations

import datetime

import httpx
import pytest

from app.db.models import AppUser, ChargingSession, Drive, Expense, Refuel, Vehicle
from app.db.session import async_session_factory
from app.main import app
from app.security import auth, passwords

VIN = "LSJA0000000000081"
D = datetime.datetime


class _Req:
    client = type("C", (), {"host": "127.0.0.1"})()
    headers = {"user-agent": "t"}


@pytest.fixture
async def holiday(db):
    t0 = D(2026, 8, 1, 6, 0)
    async with async_session_factory() as s:
        user = AppUser(id=1, role="manager", username="juan", vehicle_limit=-1, password_hash=passwords.hash_password("una-contraseña-larga-1"))
        s.add(user)
        s.add(Vehicle(owner_id=1, vin=VIN, name="Vacaciones"))
        s.add_all([
            Drive(vin=VIN, start_time=t0, end_time=t0 + datetime.timedelta(hours=4), duration_seconds=4 * 3600, distance_km=400.0, energy_consumed_kwh=20.0,
                  fuel_used_l=18.0, start_address="Madrid", end_address="Gijón", speed_max_kmh=125.0),
            Drive(vin=VIN, start_time=t0 + datetime.timedelta(days=1), end_time=t0 + datetime.timedelta(days=1, hours=1), duration_seconds=3600, distance_km=30.0,
                  energy_consumed_kwh=5.0, fuel_used_l=0.0, start_address="Gijón", end_address="Cudillero"),
            Drive(vin=VIN, start_time=t0 - datetime.timedelta(days=3), end_time=t0 - datetime.timedelta(days=3) + datetime.timedelta(hours=1), distance_km=10.0),   # before
            ChargingSession(vin=VIN, start_time=t0 + datetime.timedelta(hours=5), end_time=t0 + datetime.timedelta(hours=6), kwh_added=25.0, charge_cost=12.5, cost_mode="fixed", charge_type="dc"),
            ChargingSession(vin=VIN, start_time=t0 + datetime.timedelta(days=1, hours=5), end_time=t0 + datetime.timedelta(days=1, hours=9), kwh_added=15.0, charge_cost=None),
            Refuel(vin=VIN, timestamp=t0 + datetime.timedelta(hours=2), liters=40.0, total_cost=64.0),
            Expense(vin=VIN, date=t0 + datetime.timedelta(hours=1), category="tolls", title="Peaje AP-6", amount=12.4),
            Expense(vin=VIN, date=t0 + datetime.timedelta(days=1), category="parking", title="Parking playa", amount=6.0),
            Expense(vin=VIN, date=t0 + datetime.timedelta(days=20), category="tax", title="IVTM", amount=90.0),                       # after
        ])
        await s.commit()
        token = await auth.create_session(s, user, _Req())
    c = httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://testserver", headers={"Origin": "http://testserver"})
    c.cookies.set(auth.COOKIE_NAME, token)
    yield c
    await c.aclose()


async def test_a_journey_adds_up_everything_between_its_dates(holiday) -> None:
    c = holiday
    body = {"name": "Asturias 2026", "start_time": "2026-07-31T22:00:00Z", "end_time": "2026-08-03T21:59:59Z", "notes": "Con la familia"}
    prev = (await c.post(f"/api/vehicles/{VIN}/journeys/preview", json=body)).json()["summary"]
    assert prev["drives"]["count"] == 2 and prev["drives"]["km"] == 430.0 and prev["totals"]["cost"] == 94.9      # 12.5 + 64 + 12.4 + 6
    r = await c.post(f"/api/vehicles/{VIN}/journeys", json=body)
    assert r.status_code == 201, r.text
    j = r.json()
    s = j["summary"]
    assert j["days"] == 3 and s["drives"]["first_address"] == "Madrid" and s["drives"]["last_address"] == "Cudillero"
    assert s["charges"] == {"count": 2, "kwh": 40.0, "cost": 12.5, "dc_count": 1, "unpriced": 1}
    assert s["refuels"]["liters"] == 40.0 and s["expenses"]["by_category"] == {"parking": 6.0, "tolls": 12.4}
    assert s["totals"]["cost_per_km"] == round(94.9 / 430, 3) and s["totals"]["cost_per_day"] == round(94.9 / 3, 2)
    assert s["drives"]["kwh_per_100km"] == round(25 / 430 * 100, 1) and s["drives"]["l_per_100km"] == 4.5 and s["drives"]["max_speed_kmh"] == 125.0
    assert [d["day"] for d in s["per_day"]] == ["2026-08-01", "2026-08-02"] and len(s["items"]["drives"]) == 2 and len(s["items"]["expenses"]) == 2
    # list, detail, edit, delete
    lst = (await c.get(f"/api/vehicles/{VIN}/journeys")).json()
    assert len(lst) == 1 and lst[0]["summary"]["drives"]["km"] == 430.0 and "items" not in lst[0]["summary"]
    assert (await c.get(f"/api/vehicles/{VIN}/journeys/{j['id']}")).json()["name"] == "Asturias 2026"
    r = await c.put(f"/api/vehicles/{VIN}/journeys/{j['id']}", json={"end_time": "2026-08-01T21:59:59Z"})
    assert r.json()["summary"]["drives"]["count"] == 1 and r.json()["days"] == 1
    assert (await c.put(f"/api/vehicles/{VIN}/journeys/{j['id']}", json={"end_time": "2026-07-01T00:00:00"})).status_code == 422
    assert (await c.post(f"/api/vehicles/{VIN}/journeys", json={**body, "end_time": "2028-01-01T00:00:00"})).status_code == 422
    assert (await c.delete(f"/api/vehicles/{VIN}/journeys/{j['id']}")).json() == {"ok": True}
    assert (await c.get(f"/api/vehicles/{VIN}/journeys")).json() == []
    assert (await c.get(f"/api/vehicles/{VIN}/journeys/{j['id']}")).status_code == 404


async def test_the_form_sends_utc_and_the_days_are_counted_in_local_time(holiday) -> None:
    """05-11-2026: «from 17:00» left out a trip at 17:07 because the local time was stored as if it were UTC."""
    c = holiday
    # 1 August 08:00 → 2 August 23:59 in Madrid; the first drive starts at 08:00 there (06:00 UTC)
    body = {"name": "Norte", "start_time": "2026-08-01T06:00:00.000Z", "end_time": "2026-08-02T21:59:00.000Z"}
    r = await c.post(f"/api/vehicles/{VIN}/journeys", json=body)
    j = r.json()
    assert r.status_code in (200, 201) and j["start_time"] == "2026-08-01T06:00:00" and j["days"] == 2
    assert j["summary"]["drives"]["count"] == 2
    # 2 August 00:00 in Madrid is still 1 August in UTC: two local days, not three
    r = await c.post(f"/api/vehicles/{VIN}/journeys", json={"name": "Medianoche", "start_time": "2026-08-01T22:00:00.000Z", "end_time": "2026-08-03T21:59:00.000Z"})
    assert r.json()["days"] == 2
