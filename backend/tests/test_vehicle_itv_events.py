"""Vehicle settings (dates), ITV from the registration date and one-off maintenance jobs."""
from __future__ import annotations

import datetime

import httpx
import pytest

from app.db.models import AppUser, Vehicle
from app.db.session import async_session_factory
from app.main import app
from app.security import auth, passwords
from app.services import maintenance_official as mo
from app.services.maintenance_alerts import collect_alerts

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


# ───────────────────────── ITV pure logic ─────────────────────────
def test_first_itv_4_years_after_registration() -> None:
    n = mo.itv_next_due(D(2023, 5, 10), None)
    assert n["due_date"] == D(2027, 5, 10) and n["basis"] == "registration"


def test_after_a_previous_itv_every_2_years_and_every_year_from_10() -> None:
    assert mo.itv_next_due(D(2023, 5, 10), D(2027, 5, 2))["due_date"] == D(2029, 5, 2)
    old = mo.itv_next_due(D(2012, 1, 1), D(2023, 3, 1))                  # 11 years old when it passed
    assert old["due_date"] == D(2024, 3, 1)
    assert mo.itv_next_due(D(2013, 1, 1), D(2023, 3, 1))["due_date"] == D(2024, 3, 1)       # exactly 10 years and 2 months


def test_the_sticker_wins_over_the_computation() -> None:
    n = mo.itv_next_due(D(2023, 5, 10), D(2027, 5, 2), valid_until=D(2029, 4, 30))
    assert n["due_date"] == D(2029, 4, 30) and n["basis"] == "valid_until"


def test_without_registration_there_is_no_computed_date() -> None:
    assert mo.itv_next_due(None, None) == {"due_date": None, "basis": "unknown"}
    assert mo.itv_status(None, datetime.date(2026, 1, 1))["status"] == "unknown"


def test_itv_states() -> None:
    today = datetime.date(2026, 6, 1)
    assert mo.itv_status(D(2026, 5, 31), today)["status"] == "due"
    assert mo.itv_status(D(2026, 7, 20), today)["status"] == "warning"
    assert mo.itv_status(D(2026, 9, 1), today)["status"] == "ok"


# ───────────────────────── vehicle settings ─────────────────────────
async def test_dates_and_capacity_are_saved_and_cleared_with_null(web) -> None:
    r = await web.put(f"/api/vehicles/{VIN}", json={"registration_date": "2023-05-10T00:00:00", "delivery_date": "2023-06-01T00:00:00",
                                                    "battery_capacity_kwh": 18.5})
    d = r.json()
    assert r.status_code == 200 and d["registration_date"].startswith("2023-05-10") and d["delivery_date"].startswith("2023-06-01")
    assert d["battery_capacity_kwh"] == 18.5
    r = await web.put(f"/api/vehicles/{VIN}", json={"delivery_date": None})
    d = r.json()
    assert d["delivery_date"] is None and d["registration_date"].startswith("2023-05-10")
    assert d["battery_capacity_kwh"] == 18.5                                        # what is not sent is not touched


async def test_settings_validations(web) -> None:
    assert (await web.put(f"/api/vehicles/{VIN}", json={"battery_capacity_kwh": 0})).status_code == 422
    assert (await web.get("/api/vehicles")).json()[0]["battery_capacity_kwh"] != 0              # the 422 left nothing half-done
    # no order is imposed between registration and delivery (a second-hand car is delivered after its registration)
    r = await web.put(f"/api/vehicles/{VIN}", json={"registration_date": "2019-01-01T00:00:00", "delivery_date": "2023-01-01T00:00:00"})
    assert r.status_code == 200 and r.json()["delivery_date"].startswith("2023-01-01")


async def test_the_official_plan_uses_the_vehicle_delivery_date(web) -> None:
    await web.put(f"/api/vehicles/{VIN}", json={"delivery_date": "2025-03-31T00:00:00"})
    d = (await web.get(f"/api/vehicles/{VIN}/maintenance/plan")).json()
    assert d["baseline"]["configured"] and d["official"]["next"]["due_date"].startswith("2026-03-31")


async def test_the_old_plan_endpoint_writes_the_date_on_the_vehicle(web) -> None:
    await web.put(f"/api/vehicles/{VIN}/maintenance/plan", json={"baseline_date": "2025-03-31T00:00:00"})
    assert (await web.get("/api/vehicles")).json()[0]["delivery_date"].startswith("2025-03-31")


# ───────────────────────── ITV ─────────────────────────
async def test_the_itv_counts_from_registration_and_is_recorded(web) -> None:
    d = (await web.get(f"/api/vehicles/{VIN}/maintenance/plan")).json()
    assert d["itv"]["status"] == "unknown" and d["itv"]["next_due_date"] is None                   # no registration, no date
    await web.put(f"/api/vehicles/{VIN}", json={"registration_date": "2023-05-10T00:00:00"})
    itv = (await web.get(f"/api/vehicles/{VIN}/maintenance/plan")).json()["itv"]
    assert itv["next_due_date"].startswith("2027-05-10") and itv["basis"] == "registration"

    r = await web.post(f"/api/vehicles/{VIN}/maintenance/itv/complete", json={"service_date": "2027-05-02T00:00:00", "cost": 45.5})
    assert r.status_code == 200 and r.json()["next_due_date"].startswith("2029-05-02")
    itv = (await web.get(f"/api/vehicles/{VIN}/maintenance/plan")).json()["itv"]
    assert itv["last_date"].startswith("2027-05-02") and itv["next_due_date"].startswith("2029-05-02")

    await web.post(f"/api/vehicles/{VIN}/maintenance/itv/complete", json={"service_date": "2029-04-20T00:00:00", "valid_until": "2031-04-30T00:00:00"})
    itv = (await web.get(f"/api/vehicles/{VIN}/maintenance/plan")).json()["itv"]
    assert itv["next_due_date"].startswith("2031-04-30") and itv["basis"] == "valid_until"
    logs = (await web.get(f"/api/vehicles/{VIN}/maintenance/logs")).json()
    assert [x["item_name"] for x in logs] == ["ITV", "ITV"] and logs[0]["category"] == "Legal"
    assert (await web.get(f"/api/vehicles/{VIN}/maintenance")).json() == []                        # it is not a custom task


async def test_itv_alert_in_both_modes(web) -> None:
    await web.put(f"/api/vehicles/{VIN}", json={"registration_date": "2023-05-10T00:00:00"})
    for mode in ("official", "custom"):
        await web.put(f"/api/vehicles/{VIN}/maintenance/plan", json={"mode": mode})
        async with async_session_factory() as s:
            alerts = await collect_alerts(s, VIN, 14000.0, D(2027, 4, 20))                       # 20 days before
        itv = [a for a in alerts if a["key"].startswith("itv:")]
        assert len(itv) == 1 and itv[0]["remaining_days"] == 20 and not itv[0]["is_overdue"], mode
    async with async_session_factory() as s:
        overdue = [a for a in await collect_alerts(s, VIN, 14000.0, D(2027, 6, 1)) if a["key"].startswith("itv:")]
    assert overdue[0]["is_overdue"] and overdue[0]["key"] == "itv:due"


# ───────────────────────── one-off jobs ─────────────────────────
async def test_one_off_job_with_title_category_and_cost(web) -> None:
    r = await web.post(f"/api/vehicles/{VIN}/maintenance/events", json={
        "title": "Pastillas de freno delanteras", "category": "Frenos", "service_date": "2026-09-01T00:00:00",
        "odometer": 13500, "cost": 120.0, "workshop": "Taller X", "notes": "Discos en buen estado"})
    assert r.status_code == 200
    logs = (await web.get(f"/api/vehicles/{VIN}/maintenance/logs")).json()
    assert len(logs) == 1
    e = logs[0]
    assert e["item_name"] == "Pastillas de freno delanteras" and e["category"] == "Frenos" and e["origin"] == "oneoff"
    assert e["odometer"] == 13500 and e["cost"] == 120.0 and e["workshop"] == "Taller X"
    assert (await web.get(f"/api/vehicles/{VIN}/maintenance")).json() == []                        # does not create a task with an interval
    d = (await web.get(f"/api/vehicles/{VIN}/maintenance/plan")).json()
    assert d["custom"]["items"] == []


async def test_one_off_job_validates_and_uses_the_current_odometer_by_default(web) -> None:
    assert (await web.post(f"/api/vehicles/{VIN}/maintenance/events", json={"title": "x"})).status_code == 422
    r = await web.post(f"/api/vehicles/{VIN}/maintenance/events", json={"title": "Reparación de rueda", "category": "Neumáticos"})
    assert r.status_code == 200
    log = (await web.get(f"/api/vehicles/{VIN}/maintenance/logs")).json()[0]
    assert log["odometer"] == 14000.0


async def test_the_new_routes_require_a_session(db) -> None:
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://testserver") as c:
        assert (await c.post(f"/api/vehicles/{VIN}/maintenance/events", json={"title": "xx"})).status_code == 401
        assert (await c.post(f"/api/vehicles/{VIN}/maintenance/itv/complete", json={})).status_code == 401
