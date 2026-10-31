"""Repostajes as a section: real consumption between full tanks, gauge estimate, price per litre and the summary route."""
from __future__ import annotations

import datetime
from types import SimpleNamespace

import httpx
import pytest

from app.config import settings
from app.db.models import AppUser, Refuel, Vehicle
from app.db.session import async_session_factory
from app.main import app
from app.security import auth, passwords
from app.services import refuel_stats

VIN = "LSJA0000000000002"
D = datetime.datetime


def R(i, day, odo, liters, full=True, cost=None, before=None, after=None):
    return SimpleNamespace(id=i, timestamp=D(2026, 1, day, 12), odometer=odo, liters=liters, full_tank=full, total_cost=cost,
                           fuel_before_pct=before, fuel_after_pct=after)


def test_consumption_is_measured_between_full_tanks_including_partial_refuels() -> None:
    rows = [R(1, 1, 10000, 40.0), R(2, 8, 10500, 20.0, full=False), R(3, 15, 11000, 25.0)]      # 45 L over 1000 km
    s = refuel_stats.compute(rows, 50.0)
    assert s["l_per_100km"] == 4.5 and s["l_per_100km_segments"] == 1
    assert s["per_refuel"][3]["l_per_100km"] == 4.5 and s["per_refuel"][3]["km_since_full"] == 1000.0
    assert s["per_refuel"][2]["l_per_100km"] is None                     # a partial refuel never closes a segment
    assert s["per_refuel"][2]["km_since_previous"] == 500.0
    assert s["avg_km_between_refuels"] == 500.0 and s["full_tank_range_km"] == round(50 * 100 / 4.5)


def test_without_full_tanks_the_gauge_estimate_is_used_and_flagged() -> None:
    rows = [R(1, 1, 10000, 20.0, full=None, before=20, after=60), R(2, 10, 10400, 20.0, full=None, before=20, after=60)]
    s = refuel_stats.compute(rows, 50.0)
    assert s["l_per_100km"] is None                                       # 60 % is not a full tank
    assert s["gauge_l_per_100km"] == 5.0                                  # (60 - 20) % × 50 L = 20 L over 400 km
    full = [R(1, 1, 10000, 20.0, full=None, before=50, after=96), R(2, 10, 10400, 20.0, full=None, before=50, after=97)]
    assert refuel_stats.compute(full, 50.0)["l_per_100km"] == 5.0          # >= 95 % counts as full when not stated


def test_price_cost_per_100km_and_implausible_segments() -> None:
    rows = [R(1, 1, 10000, 40.0, cost=60.0), R(2, 8, 10010, 30.0, cost=48.0), R(3, 20, 11010, 40.0)]
    s = refuel_stats.compute(rows, 50.0)
    assert s["avg_price_per_liter"] == round(108 / 70, 3) and s["unpriced"] == 1 and s["total_cost"] == 108.0
    # 10 km between the first two full tanks is noise (not measured), but the second full tank is still the anchor of the next segment
    assert s["per_refuel"][2]["l_per_100km"] is None and s["per_refuel"][3]["l_per_100km"] == 4.0
    assert s["fuel_cost_per_100km"] == round(round(108 / 70, 3) * 4.0, 2)
    assert [m["month"] for m in s["months"]] == ["2026-01"] and s["months"][0]["liters"] == 110.0


def test_period_filter_keeps_the_previous_refuel_for_the_segment() -> None:
    rows = [R(1, 1, 10000, 40.0), R(2, 20, 11000, 50.0)]
    s = refuel_stats.compute(rows, 50.0, since=D(2026, 1, 10))
    assert s["count"] == 1 and s["l_per_100km"] == 5.0 and s["total_liters"] == 50.0


class _Req:
    client = type("C", (), {"host": "127.0.0.1"})()
    headers = {"user-agent": "t"}


@pytest.fixture
async def web(db, tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "DATA_DIR", str(tmp_path))
    async with async_session_factory() as s:
        s.add(Vehicle(owner_id=1, vin=VIN, name="Ebro", odometer=15000.0, fuel_tank_liters=50.0))
        user = AppUser(id=1, role="manager", username="juan", vehicle_limit=-1, password_hash=passwords.hash_password("una-contraseña-larga-1"))
        s.add(user)
        await s.commit()
        token = await auth.create_session(s, user, _Req())
    c = httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://testserver")
    c.cookies.set(auth.COOKIE_NAME, token)
    yield c
    await c.aclose()


async def test_the_summary_route_returns_stats_and_the_rows_with_their_consumption(web) -> None:
    for day, odo, liters, price in ((1, 10000, 40, 1.5), (15, 11000, 45, 1.6)):
        r = await web.post(f"/api/vehicles/{VIN}/refuels", json={"liters": liters, "price_per_liter": price, "odometer": odo,
                                                                 "timestamp": f"2026-01-{day:02d}T12:00:00"})
        assert r.status_code == 200 and r.json()["full_tank"] is True and r.json()["full_tank_stated"] is True
    s = (await web.get(f"/api/vehicles/{VIN}/refuels/summary")).json()
    assert s["count"] == 2 and s["l_per_100km"] == 4.5 and s["tank_liters"] == 50.0 and "per_refuel" not in s
    assert s["refuels"][0]["l_per_100km"] == 4.5 and s["refuels"][0]["km_since_previous"] == 1000.0 and s["refuels"][1]["l_per_100km"] is None
    assert (await web.get(f"/api/vehicles/{VIN}/refuels/summary?period=year")).json()["period"] == "year"
    assert (await web.get(f"/api/vehicles/{VIN}/refuels/summary?period=nope")).status_code == 422
    assert (await web.get("/api/vehicles/NOEXISTE/refuels/summary")).status_code == 404
    # a partial refuel, then the odometer and the flag are editable
    rid = s["refuels"][0]["id"]
    d = (await web.put(f"/api/refuels/{rid}", json={"full_tank": False, "odometer": 11200})).json()
    assert d["full_tank"] is False and d["odometer"] == 11200.0
    assert (await web.get(f"/api/vehicles/{VIN}/refuels/summary")).json()["l_per_100km"] is None


async def test_a_detected_refuel_counts_as_full_only_from_95_percent(web) -> None:
    async with async_session_factory() as s:
        s.add(Refuel(vin=VIN, timestamp=D(2026, 1, 1), odometer=10000, liters=30, fuel_before_pct=30, fuel_after_pct=90, detected=True))
        s.add(Refuel(vin=VIN, timestamp=D(2026, 1, 9), odometer=10500, liters=35, fuel_before_pct=25, fuel_after_pct=96, detected=True))
        await s.commit()
    rows = (await web.get(f"/api/vehicles/{VIN}/refuels")).json()
    by_day = {r["timestamp"][:10]: r for r in rows}
    assert by_day["2026-01-01"]["full_tank"] is False and by_day["2026-01-09"]["full_tank"] is True
    assert all(r["full_tank_stated"] is False for r in rows)
