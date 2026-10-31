"""One period helper for every list and statistic: presets, custom month/day ranges, and the routes that honour them."""
from __future__ import annotations

import datetime

import httpx
import pytest
from fastapi import HTTPException

from app.db.models import AppUser, ChargingSession, Drive, Expense, Refuel, Vehicle
from app.db.session import async_session_factory
from app.main import app
from app.security import auth, passwords
from app.services import periods

VIN = "LSJA0000000000071"


class _Req:
    client = type("C", (), {"host": "127.0.0.1"})()
    headers = {"user-agent": "t"}


def test_presets_and_custom_ranges_follow_the_local_calendar() -> None:
    now = datetime.datetime(2026, 3, 15, 12, 0)                         # UTC; Europe/Madrid is UTC+1 in March
    assert periods.resolve("all", None, None, now) == (None, None)
    start, end = periods.resolve("month", None, None, now)
    assert start == datetime.datetime(2026, 2, 28, 23, 0) and end is None   # 1 March 00:00 local
    start, end = periods.resolve("last_month", None, None, now)
    assert (start, end) == (datetime.datetime(2026, 1, 31, 23, 0), datetime.datetime(2026, 2, 28, 23, 0))
    start, end = periods.resolve("last_year", None, None, now)
    assert start.year == 2024 and start.month == 12 and start.day == 31 and end == datetime.datetime(2025, 12, 31, 23, 0)
    assert periods.resolve("12m", None, None, now)[0] == now - datetime.timedelta(days=365)
    # custom: whole months, `to` inclusive; days too
    start, end = periods.resolve("custom", "2025-11", "2026-01", now)
    assert start == datetime.datetime(2025, 10, 31, 23, 0) and end == datetime.datetime(2026, 1, 31, 23, 0)
    start, end = periods.resolve(None, "2026-02-10", "2026-02-10", now)        # one day
    assert start == datetime.datetime(2026, 2, 9, 23, 0) and end == datetime.datetime(2026, 2, 10, 23, 0)
    assert periods.resolve(None, "2026-02", None, now)[1] is None
    assert periods.inside(datetime.datetime(2026, 2, 10, 10), (start, end)) and not periods.inside(datetime.datetime(2026, 2, 11, 10), (start, end))
    for bad in [("nope", None, None), ("custom", "2026-13", None), (None, "2026-02", "2026-01"), (None, "hoy", None)]:
        with pytest.raises(HTTPException):
            periods.resolve(*bad, now)
    assert periods.is_everything("all", None, None) and not periods.is_everything("all", "2026-01", None)


@pytest.fixture
async def history(db):
    async with async_session_factory() as s:
        user = AppUser(id=1, role="manager", username="juan", vehicle_limit=-1, password_hash=passwords.hash_password("una-contraseña-larga-1"))
        s.add(user)
        s.add(Vehicle(owner_id=1, vin=VIN, name="Rango", odometer=20000.0, purchase_odometer=0.0))
        for month, km in ((1, 100.0), (2, 200.0), (3, 300.0)):
            t = datetime.datetime(2026, month, 10, 9, 0)
            s.add(Drive(vin=VIN, start_time=t, end_time=t + datetime.timedelta(hours=1), distance_km=km, energy_consumed_kwh=km / 10))
            s.add(ChargingSession(vin=VIN, start_time=t, end_time=t + datetime.timedelta(hours=2), kwh_added=km / 10, charge_cost=km / 100))
            s.add(Refuel(vin=VIN, timestamp=t, odometer=1000.0 * month, liters=30.0, price_per_liter=1.5, total_cost=45.0, full_tank=True))
            s.add(Expense(vin=VIN, date=t, title=f"Peaje {month}", category="tolls", amount=float(month)))
        await s.commit()
        token = await auth.create_session(s, user, _Req())
    c = httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://testserver", headers={"Origin": "http://testserver"})
    c.cookies.set(auth.COOKIE_NAME, token)
    yield c
    await c.aclose()


async def test_every_list_and_statistic_takes_the_same_from_to(history) -> None:
    c = history
    costs = (await c.get(f"/api/vehicles/{VIN}/costs?from=2026-02&to=2026-02")).json()
    assert costs["totals"]["charges"] == 2.0 and costs["totals"]["expenses"] == 2.0 and costs["custom_from"] == "2026-02"
    assert (await c.get(f"/api/vehicles/{VIN}/costs?period=all")).json()["totals"]["charges"] == 6.0
    stats = (await c.get(f"/api/vehicles/{VIN}/stats?period=custom&from=2026-01&to=2026-02")).json()
    assert [m["month"] for m in stats["monthly"]] == ["2026-01", "2026-02"]
    drives = (await c.get(f"/api/vehicles/{VIN}/drives?period=custom&from=2026-03&to=2026-03")).json()
    assert [d["distance_km"] for d in drives["drives"]] == [300.0]
    assert len((await c.get(f"/api/vehicles/{VIN}/drives?period=7d")).json()["drives"]) == 0
    charges = (await c.get(f"/api/vehicles/{VIN}/charges?from=2026-02&to=2026-03")).json()
    assert sorted(ch["kwh_added"] for ch in charges) == [20.0, 30.0]
    expenses = (await c.get(f"/api/vehicles/{VIN}/expenses?from=2026-01&to=2026-01")).json()
    assert [e["title"] for e in expenses] == ["Peaje 1"]
    refuels = (await c.get(f"/api/vehicles/{VIN}/refuels/summary?from=2026-02&to=2026-03")).json()
    assert len(refuels["refuels"]) == 2 and refuels["from"] == "2026-02"
    tl = (await c.get(f"/api/vehicles/{VIN}/timeline?from=2026-02&to=2026-02")).json()
    assert tl["events"] and all(e["timestamp"].startswith("2026-02") for e in tl["events"])
    assert (await c.get(f"/api/vehicles/{VIN}/costs?period=siglo")).status_code == 422
    assert (await c.get(f"/api/vehicles/{VIN}/stats?from=2026-03&to=2026-01")).status_code == 422
