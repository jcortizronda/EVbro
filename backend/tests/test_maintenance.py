"""Maintenance: EBRO's official plan (next service, steps, deadlines) and the custom plan."""
from __future__ import annotations

import datetime

import httpx
import pytest

from app.db.models import AppUser, MaintenanceItem, MaintenancePlan, Vehicle
from app.db.session import async_session_factory
from app.main import app
from app.security import auth, passwords
from app.services import maintenance_official as mo
from app.services.maintenance_alerts import collect_alerts

VIN = "LSJA0000000000001"
DELIVERY = datetime.datetime(2025, 3, 31)


# ───────────────────────── pure logic ─────────────────────────
def test_the_first_step_is_15000_and_then_it_advances_from_the_last_recorded() -> None:
    assert mo.next_step(None)["km"] == 15000
    assert mo.next_step(15000)["km"] == 30000
    assert mo.next_step(30000)["km"] == 45000
    assert mo.next_step(90000) is None
    # if one is skipped, the next counts from the highest recorded
    assert mo.next_step(45000)["km"] == 60000


def test_due_on_whichever_comes_first_km_or_date() -> None:
    today = datetime.date(2026, 2, 1)
    by_km = mo.official_next(14800, today - datetime.timedelta(days=200), None, DELIVERY, 0.0, "S900 PHEV")
    assert by_km["km"] == 15000 and by_km["status"] == "warning"                 # 200 km: already «upcoming»
    by_date = mo.official_next(5000, datetime.date(2026, 3, 25), None, DELIVERY, 0.0, "S900 PHEV")
    assert by_date["status"] == "warning" and by_date["remaining_days"] == 6   # few km, but almost 12 months
    overdue = mo.official_next(5000, datetime.date(2026, 4, 2), None, DELIVERY, 0.0, "S900 PHEV")
    assert overdue["status"] == "due" and overdue["remaining_days"] < 0            # overdue by date with 5,000 km
    ok = mo.official_next(3000, datetime.date(2025, 6, 1), None, DELIVERY, 0.0, "S900 PHEV")
    assert ok["status"] == "ok"


def test_without_a_delivery_date_only_kilometres_are_watched() -> None:
    n = mo.official_next(9000, datetime.date(2030, 1, 1), None, None, 0.0, None)
    assert n["due_date"] is None and n["remaining_days"] is None and n["progress_time_pct"] is None
    assert n["status"] == "ok" and n["remaining_km"] == 6000


def test_calendar_months_and_end_of_month() -> None:
    assert mo.add_months(datetime.datetime(2025, 3, 31), 12) == datetime.datetime(2026, 3, 31)
    assert mo.add_months(datetime.datetime(2025, 3, 31), 11) == datetime.datetime(2026, 2, 28)
    assert mo.add_months(datetime.datetime(2024, 1, 31), 1) == datetime.datetime(2024, 2, 29)     # leap year


def test_the_60000_step_detail_includes_coolant_and_transmission() -> None:
    phev = mo.step_detail(60000, "S900 PHEV")
    names = [i["name"] for i in phev["replace_items"]]
    assert "Anticongelante" in names and any("Transmisión" in n for n in names)
    assert phev["extra_labor_hours"] == 0.5 and phev["labor_hours"] == 2.3
    assert mo.step_detail(60000, "S800 HEV")["extra_labor_hours"] == 0.3
    assert mo.step_detail(15000, "S900 PHEV")["extra_labor_hours"] == 0.0


def test_suggestions_are_not_created_on_their_own_and_have_an_interval() -> None:
    assert all(s["interval_km"] or s["interval_months"] for s in mo.SUGGESTIONS)


# ───────────────────────── API ─────────────────────────
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


async def test_the_default_plan_is_official_and_does_not_invent_tasks(web) -> None:
    r = await web.get(f"/api/vehicles/{VIN}/maintenance/plan")
    assert r.status_code == 200
    d = r.json()
    assert d["mode"] == "official" and d["baseline"]["configured"] is False
    assert d["custom"]["items"] == [] and len(d["custom"]["suggestions"]) == len(mo.SUGGESTIONS)
    assert d["official"]["next"]["km"] == 15000 and d["official"]["next"]["remaining_km"] == 1000.0
    assert (await web.get(f"/api/vehicles/{VIN}/maintenance")).json() == []          # 6 tasks are no longer created on their own


async def test_set_delivery_and_change_mode(web) -> None:
    r = await web.put(f"/api/vehicles/{VIN}/maintenance/plan",
                      json={"mode": "custom", "baseline_date": "2025-03-31T00:00:00", "baseline_odometer": 12})
    d = r.json()
    assert d["mode"] == "custom" and d["baseline"]["configured"] and d["baseline"]["odometer"] == 12
    assert d["official"]["next"]["due_date"].startswith("2026-03-31")
    assert (await web.put(f"/api/vehicles/{VIN}/maintenance/plan", json={"mode": "otro"})).status_code == 422
    r = await web.put(f"/api/vehicles/{VIN}/maintenance/plan", json={"clear_baseline_date": True})
    assert r.json()["baseline"]["configured"] is False and r.json()["mode"] == "custom"   # the mode is kept


async def test_recording_a_step_advances_to_the_next_and_stays_in_the_history(web) -> None:
    r = await web.post(f"/api/vehicles/{VIN}/maintenance/official/complete",
                       json={"step_km": 15000, "odometer": 15210, "cost": 120.5, "workshop": "Taller X"})
    assert r.status_code == 200 and r.json()["log_id"]
    d = (await web.get(f"/api/vehicles/{VIN}/maintenance/plan")).json()
    assert d["official"]["next"]["km"] == 30000
    assert [h["step_km"] for h in d["official"]["history"]] == [15000]
    assert d["official"]["matrix"]["columns"][0]["is_done"] and d["official"]["matrix"]["columns"][1]["is_next"]
    logs = (await web.get(f"/api/vehicles/{VIN}/maintenance/logs")).json()
    assert logs[0]["revision_km"] == 15000 and logs[0]["origin"] == "official" and logs[0]["cost"] == 120.5
    # it does not show up as a custom task
    assert (await web.get(f"/api/vehicles/{VIN}/maintenance")).json() == []
    # repeating it is a clear error; a made-up step, 422
    dup = await web.post(f"/api/vehicles/{VIN}/maintenance/official/complete", json={"step_km": 15000})
    assert dup.status_code == 409
    assert (await web.post(f"/api/vehicles/{VIN}/maintenance/official/complete", json={"step_km": 20000})).status_code == 422


async def test_completing_every_step_closes_the_plan(web) -> None:
    for st in mo.STEPS:
        assert (await web.post(f"/api/vehicles/{VIN}/maintenance/official/complete", json={"step_km": st["km"]})).status_code == 200
    d = (await web.get(f"/api/vehicles/{VIN}/maintenance/plan")).json()
    assert d["official"]["finished"] is True and d["official"]["next"] is None


async def test_custom_tasks_and_their_suggestions(web) -> None:
    r = await web.post(f"/api/vehicles/{VIN}/maintenance", json={"name": "Rotación de neumáticos", "category": "Neumáticos",
                                                                 "interval_km": 10000, "interval_months": 12})
    assert r.status_code == 200 and r.json()["status"] == "ok"
    d = (await web.get(f"/api/vehicles/{VIN}/maintenance/plan")).json()
    assert [i["name"] for i in d["custom"]["items"]] == ["Rotación de neumáticos"]
    assert "Rotación de neumáticos" not in [s["name"] for s in d["custom"]["suggestions"]]     # no longer suggested
    item_id = d["custom"]["items"][0]["id"]
    done = await web.post(f"/api/maintenance/{item_id}/complete", json={"odometer": 14100, "cost": 30})
    assert done.status_code == 200 and done.json()["next_due_odometer"] == 24100.0


async def test_changing_a_task_interval_recomputes_its_due_point(web) -> None:
    r = await web.post(f"/api/vehicles/{VIN}/maintenance", json={"name": "Aceite", "interval_km": 10000, "interval_months": 12,
                                                                 "last_service_odometer": 12000})
    item_id = r.json()["id"]
    assert r.json()["next_due_odometer"] == 22000
    r = await web.put(f"/api/maintenance/{item_id}", json={"interval_km": 15000, "interval_months": 6})
    assert r.json()["next_due_odometer"] == 27000 and r.json()["interval_months"] == 6 and r.json()["next_due_date"]
    r = await web.put(f"/api/maintenance/{item_id}", json={"interval_km": None})
    assert r.json()["next_due_odometer"] is None                                           # by months only


async def test_the_plan_routes_require_a_session(db) -> None:
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://testserver") as c:
        assert (await c.get(f"/api/vehicles/{VIN}/maintenance/plan")).status_code == 401
        assert (await c.put(f"/api/vehicles/{VIN}/maintenance/plan", json={})).status_code == 401
        assert (await c.post(f"/api/vehicles/{VIN}/maintenance/official/complete", json={"step_km": 15000})).status_code == 401


async def test_missing_vehicle_gives_404(web) -> None:
    assert (await web.get("/api/vehicles/NOEXISTE/maintenance/plan")).status_code == 404


# ───────────────────────── alerts ─────────────────────────
async def test_alerts_in_official_mode_use_the_next_step(db) -> None:
    async with async_session_factory() as s:
        s.add(Vehicle(owner_id=1, vin=VIN, name="Ebro", model="S900 PHEV", odometer=14600.0))
        s.add(MaintenancePlan(vin=VIN, mode="official", baseline_date=DELIVERY, baseline_odometer=0.0))
        await s.commit()
    async with async_session_factory() as s:
        a = await collect_alerts(s, VIN, 14600.0, datetime.datetime(2025, 9, 1))
    assert len(a) == 1 and a[0]["key"] == "official:15000:warning" and not a[0]["is_overdue"]
    assert "Aceite (4,5 Litros) 0W-20" in a[0]["items"]
    async with async_session_factory() as s:
        a = await collect_alerts(s, VIN, 5000.0, datetime.datetime(2026, 5, 1))            # overdue by date
    assert a[0]["is_overdue"] and a[0]["key"] == "official:15000:due"


async def test_alerts_in_custom_mode_ignore_the_official_plan_and_look_at_the_tasks(db) -> None:
    async with async_session_factory() as s:
        s.add(Vehicle(owner_id=1, vin=VIN, name="Ebro", model="S900 PHEV", odometer=30000.0))
        s.add(MaintenancePlan(vin=VIN, mode="custom", baseline_date=DELIVERY))
        s.add(MaintenanceItem(vin=VIN, name="Aceite", category="Motor", origin="custom", interval_km=10000.0,
                              last_service_odometer=19000.0, next_due_odometer=29000.0))
        s.add(MaintenanceItem(vin=VIN, name="Frenos", category="Frenos", origin="custom", interval_km=30000.0,
                              last_service_odometer=20000.0, next_due_odometer=50000.0))
        await s.commit()
    async with async_session_factory() as s:
        a = await collect_alerts(s, VIN, 30000.0, datetime.datetime(2026, 1, 1))
    assert [x["label"] for x in a] == ["Aceite"] and a[0]["is_overdue"]


async def test_the_alert_text_says_km_or_days_as_appropriate(monkeypatch) -> None:
    from app.services.notification_service import NotificationService

    sent = []

    async def fake_dispatch(cls, category, title, message, tags=None, priority="default", **kwargs):
        sent.append((str(title), str(message)))          # `Message`s render in Spanish by default
        return []

    monkeypatch.setattr(NotificationService, "dispatch", classmethod(fake_dispatch))
    await NotificationService.notify_maintenance_alert("Ebro", "revisión oficial de 15.000 km", 800.0, False, ["Aceite"], 1.0, remaining_days=20)
    assert "800 km o 20 días" in sent[0][1] and "lo que antes llegue" in sent[0][1] and sent[0][0].startswith("Próximo")
    await NotificationService.notify_maintenance_alert("Ebro", "revisión oficial de 15.000 km", 2000.0, True, [], None, remaining_days=-9)
    assert "9 días" in sent[1][1] and "km" not in sent[1][1].split("desde hace")[1] and sent[1][0].startswith("Mantenimiento vencido")


# ───────────────────────── the official plan is EBRO's ─────────────────────────
def test_is_ebro_ignores_case_and_spaces() -> None:
    assert mo.is_ebro("EBRO") and mo.is_ebro("Ebro") and mo.is_ebro(" ebro ")
    assert not mo.is_ebro("Toyota") and not mo.is_ebro(None) and not mo.is_ebro("")


async def test_another_brand_has_no_official_plan_but_keeps_the_itv(web) -> None:
    assert (await web.put(f"/api/vehicles/{VIN}", json={"brand": "Toyota", "registration_date": "2020-01-10T00:00:00"})).status_code == 200
    d = (await web.get(f"/api/vehicles/{VIN}/maintenance/plan")).json()
    assert d["official_available"] is False and d["mode"] == "custom"
    assert d["itv"]["next_due_date"] is not None
    r = await web.put(f"/api/vehicles/{VIN}/maintenance/plan", json={"mode": "official"})
    assert r.status_code == 409 and "Ebro" in r.json()["detail"]
    assert (await web.put(f"/api/vehicles/{VIN}/maintenance/plan", json={"mode": "custom"})).status_code == 200
    # back to EBRO: the official plan is available again
    await web.put(f"/api/vehicles/{VIN}", json={"brand": "Ebro"})
    d = (await web.put(f"/api/vehicles/{VIN}/maintenance/plan", json={"mode": "official"})).json()
    assert d["official_available"] is True and d["mode"] == "official"


async def test_alerts_of_another_brand_skip_official_steps(db) -> None:
    async with async_session_factory() as s:
        s.add(Vehicle(owner_id=1, vin=VIN, name="Corolla", brand="Toyota", model="Corolla", odometer=14600.0))
        s.add(MaintenancePlan(vin=VIN, mode="official", baseline_date=DELIVERY, baseline_odometer=0.0))
        s.add(MaintenanceItem(vin=VIN, name="Aceite", category="Motor", origin="custom", interval_km=10000.0,
                              last_service_odometer=4000.0, next_due_odometer=14000.0))
        await s.commit()
    async with async_session_factory() as s:
        a = await collect_alerts(s, VIN, 14600.0, datetime.datetime(2025, 9, 1))
    assert [x["label"] for x in a] == ["Aceite"]
