"""History lists: paged trips with month totals, charge totals over every charge and no made-up trip efficiency."""
from __future__ import annotations

import datetime

import httpx
import pytest

from app.db.models import AppUser, ChargingSession, Drive, Vehicle
from app.db.session import async_session_factory
from app.main import app
from app.security import auth, passwords

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


async def _drives() -> None:
    async with async_session_factory() as s:
        # September: 2 trips; October: 3 trips (one of them at 23:30 UTC on 31 Oct = 1 Nov in Madrid)
        for day, km in ((10, 10.0), (20, 20.0)):
            s.add(Drive(vin=VIN, start_time=D(2026, 9, day, 8), end_time=D(2026, 9, day, 9), distance_km=km, energy_consumed_kwh=2.0,
                        start_battery_pct=80.0, end_battery_pct=70.0, battery_used_pct=10.0))
        for day in (1, 2, 3):
            s.add(Drive(vin=VIN, start_time=D(2026, 10, day, 8), end_time=D(2026, 10, day, 9), distance_km=5.0, energy_consumed_kwh=1.0))
        s.add(Drive(vin=VIN, start_time=D(2026, 10, 31, 23, 30), end_time=D(2026, 11, 1, 0, 10), distance_km=7.0))
        s.add(Drive(vin=VIN, start_time=D(2026, 10, 5, 8), end_time=None, distance_km=3.0))           # incomplete: not in months
        await s.commit()


async def test_trips_are_paged_with_has_more_and_months_cover_every_trip(web) -> None:
    await _drives()
    first = (await web.get(f"/api/vehicles/{VIN}/drives?limit=2&offset=0")).json()
    assert len(first["drives"]) == 2 and first["has_more"] is True
    last = (await web.get(f"/api/vehicles/{VIN}/drives?limit=2&offset=4")).json()
    assert len(last["drives"]) == 2 and last["has_more"] is False

    months = {m["month"]: m for m in first["months"]}
    assert list(months) == ["2026-11", "2026-10", "2026-09"]                # newest first, local time (Europe/Madrid)
    assert months["2026-11"]["count"] == 1
    assert months["2026-10"] == {"month": "2026-10", "count": 3, "distance_km": 15.0, "energy_kwh": 3.0, "fuel_l": 0.0}
    assert months["2026-09"]["distance_km"] == 30.0 and first["summary"]["count"] == 6


async def test_a_trip_reports_consumption_and_no_invented_efficiency(web) -> None:
    await _drives()
    d = (await web.get(f"/api/vehicles/{VIN}/drives?limit=50")).json()["drives"][-1]          # 10 km, 2 kWh
    assert "efficiency_pct" not in d
    assert d["consumption_wh_km"] == 200.0 and d["consumption_kwh_100km"] == 20.0 and d["battery_used_pct"] == 10.0


async def test_period_summary_consumption_matches_stats_and_ignores_short_or_unmeasured_trips(web) -> None:
    """The «Resumen del periodo» average must be the same figure the Energía and Estadísticas screens show for the
    same period: only drives of ≥ 2 km with measured energy count (a 7 km drive without kWh and a 1.5 km one do not)."""
    await _drives()
    async with async_session_factory() as s:
        s.add(Drive(vin=VIN, start_time=D(2026, 10, 6, 8), end_time=D(2026, 10, 6, 8, 10), distance_km=1.5, energy_consumed_kwh=0.9))
        s.add(Drive(vin=VIN, start_time=D(2026, 10, 7, 8), end_time=D(2026, 10, 7, 8, 5), distance_km=0.0, energy_consumed_kwh=0.2))
        await s.commit()
    summary = (await web.get(f"/api/vehicles/{VIN}/drives?period=all&limit=50")).json()["summary"]
    assert summary["total_distance_km"] == 53.5 and summary["total_energy_kwh"] == 8.1            # every complete drive still adds up
    assert summary["avg_consumption_wh_km"] == 155.6                                           # 7 kWh over 45 km, not 8.1 over 54
    totals = (await web.get(f"/api/vehicles/{VIN}/stats?period=all")).json()["totals"]
    assert totals["kwh_per_100km"] == round(summary["avg_consumption_wh_km"] / 10, 1) == 15.6


async def test_charge_summary_adds_up_every_charge_by_month(web) -> None:
    async with async_session_factory() as s:
        for i in range(35):                                                      # more than one page of the list
            s.add(ChargingSession(vin=VIN, start_time=D(2026, 10, 1 + i % 28, 20), kwh_added=2.0, charge_cost=0.5, duration_seconds=3600))
        s.add(ChargingSession(vin=VIN, start_time=D(2026, 9, 15, 20), kwh_added=10.0, charge_cost=None, duration_seconds=3600))
        s.add(ChargingSession(vin="OTHER000000000001", start_time=D(2026, 9, 15, 20), kwh_added=99.0, duration_seconds=1))
        await s.commit()
    r = await web.get(f"/api/vehicles/{VIN}/charges/summary")
    body = r.json()
    assert r.status_code == 200
    assert body["count"] == 36 and body["kwh_added"] == 80.0 and body["cost"] == 17.5
    assert body["months"] == [
        {"month": "2026-10", "count": 35, "kwh_added": 70.0, "cost": 17.5},
        {"month": "2026-09", "count": 1, "kwh_added": 10.0, "cost": 0.0},
    ]


async def test_charge_summary_of_a_car_without_charges(web) -> None:
    body = (await web.get(f"/api/vehicles/{VIN}/charges/summary")).json()
    assert body == {"count": 0, "kwh_added": 0.0, "cost": 0.0, "dc_count": 0, "dc_kwh_added": 0, "months": []}
