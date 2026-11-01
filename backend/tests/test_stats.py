"""Statistics of the history: months, consumption buckets, observed capacity, places and idle drain."""
from __future__ import annotations

import datetime

import httpx
import pytest

from app.db.models import AppUser, ChargePoint, ChargingSession, Drive, Expense, Refuel, Vehicle
from app.db.session import async_session_factory
from app.main import app
from app.security import auth, passwords

VIN = "LSJA0000000000051"
D = datetime.datetime


class _Req:
    client = type("C", (), {"host": "127.0.0.1"})()
    headers = {"user-agent": "t"}


@pytest.fixture
async def web(db):
    now = D.utcnow().replace(minute=0, second=0, microsecond=0)
    m1 = now - datetime.timedelta(days=40)          # last month
    m0 = now - datetime.timedelta(days=5)           # this month
    async with async_session_factory() as s:
        s.add(Vehicle(owner_id=1, vin=VIN, name="Ebro", battery_capacity_kwh=20.0))
        user = AppUser(id=1, role="manager", username="juan", vehicle_limit=-1, password_hash=passwords.hash_password("una-contraseña-larga-1"))
        s.add(user)
        s.add_all([
            # month 1: two drives (cold, fast), a big charge at home, a refuel
            Drive(vin=VIN, start_time=m1, end_time=m1 + datetime.timedelta(hours=1), distance_km=50, energy_consumed_kwh=10, fuel_used_l=0,
                  outside_temp_avg=4.0, speed_avg_kmh=95, start_battery_pct=90, end_battery_pct=40),
            ChargingSession(id=1, vin=VIN, start_time=m1 + datetime.timedelta(hours=2), end_time=m1 + datetime.timedelta(hours=6), address="Casa",
                            kwh_added=10.0, charge_cost=1.5, start_battery_pct=40, end_battery_pct=90),
            Drive(vin=VIN, start_time=m1 + datetime.timedelta(days=1), end_time=m1 + datetime.timedelta(days=1, hours=1), distance_km=20,
                  energy_consumed_kwh=3, fuel_used_l=1.0, outside_temp_avg=22.0, speed_avg_kmh=35, start_battery_pct=85, end_battery_pct=70),
            Refuel(vin=VIN, timestamp=m1 + datetime.timedelta(days=2), liters=30, total_cost=45),
            # month 0: a drive two days after the last one of month 1? no: a long idle gap without charge → drain
            Drive(vin=VIN, start_time=m0, end_time=m0 + datetime.timedelta(hours=1), distance_km=30, energy_consumed_kwh=5, fuel_used_l=0,
                  outside_temp_avg=23.0, speed_avg_kmh=40, start_battery_pct=60, end_battery_pct=35),
            ChargingSession(vin=VIN, start_time=m0 + datetime.timedelta(hours=3), end_time=m0 + datetime.timedelta(hours=5), address="Trabajo",
                            kwh_added=2.0, charge_cost=0.0, start_battery_pct=35, end_battery_pct=45),      # too small for the capacity estimate
            Expense(vin=VIN, date=m0, category="tax", title="IVTM", amount=90),
            # an old drive, outside 12 months
            Drive(vin=VIN, start_time=now - datetime.timedelta(days=400), end_time=now - datetime.timedelta(days=400) + datetime.timedelta(hours=1),
                  distance_km=100, energy_consumed_kwh=15, outside_temp_avg=10, speed_avg_kmh=80),
        ])
        await s.flush()
        # its curve: a point every 2 min for 4 h at a steady 2.85 kW, from 40 to 90 % → 11.4 kWh into the pack for 50 points
        for i in range(121):
            s.add(ChargePoint(charging_session_id=1, timestamp=m1 + datetime.timedelta(hours=2, minutes=2 * i), battery_pct=40 + 50 * i / 120, power_kw=2.85))
        await s.commit()
        token = await auth.create_session(s, user, _Req())
    c = httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://testserver")
    c.cookies.set(auth.COOKIE_NAME, token)
    yield c
    await c.aclose()


async def test_stats_summarise_months_buckets_capacity_places_and_drain(web) -> None:
    r = await web.get(f"/api/vehicles/{VIN}/stats")
    assert r.status_code == 200
    d = r.json()
    assert d["period"] == "12m" and len(d["monthly"]) == 2 and d["totals"]["drives"] == 3 and d["totals"]["charges"] == 2
    first, last = d["monthly"]
    assert first["km"] == 70 and first["kwh_charged"] == 10 and first["liters"] == 30 and first["cost_total"] == 46.5
    assert first["kwh_per_100km"] == round(13 / 70 * 100, 1) and first["cost_per_km"] == round(46.5 / 70, 3)
    assert last["km"] == 30 and last["cost_total"] == 90 and last["cost_per_km"] == 3.0
    assert d["totals"]["km"] == 100 and d["totals"]["cost_total"] == 136.5 and d["totals"]["cost_per_km"] == round(136.5 / 100, 3)
    assert d["totals"]["kwh_per_100km"] == 18.0            # (10 + 3 + 5) kWh over (50 + 20 + 30) km: what the Energía tab shows for its 30 days
    # consumption buckets by speed: the 90–100 km/h bucket holds the 50 km drive at 20 kWh/100 km (no by-temperature chart: the car never reports it)
    assert "consumption_by_temp" not in d
    speeds = {b["label"]: b for b in d["consumption_by_speed"]}
    assert speeds["90–100 km/h"]["kwh_per_100km"] == 20.0
    # capacity MEASURED from the curve of the 50-point charge (not the estimated 10 kWh, which would just give the sheet's 20 back):
    # 11.4 kWh / 50 % → 22.8 kWh, 114 % of the sheet; the 10-point charge has no curve and is too small anyway
    assert len(d["battery_health"]) == 1 and d["battery_health"][0]["capacity_kwh"] == 22.8
    assert d["battery_capacity"] == {"nominal_kwh": 20.0, "measured_kwh": 22.8, "charges": 1, "pct_of_nominal": 114.0}
    # places sorted by energy
    assert [p["address"] for p in d["charges_by_place"]] == ["Casa", "Trabajo"] and d["charges_by_place"][0]["count"] == 1
    # idle drain: from the end of the second drive (70 %) to the start of the month-0 drive (60 %) with no charge between
    assert len(d["idle_drain"]) == 1 and d["idle_drain"][0]["stops"] == 1 and 0 < d["idle_drain"][0]["pct_per_day"] < 1
    # the whole history includes the old drive
    assert (await web.get(f"/api/vehicles/{VIN}/stats", params={"period": "all"})).json()["totals"]["drives"] == 4
    assert (await web.get(f"/api/vehicles/{VIN}/stats", params={"period": "5y"})).status_code == 422
    assert (await web.get("/api/vehicles/NOPE/stats")).status_code == 404
