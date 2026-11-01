"""Expenses (insurance, taxes…), their weight in the Costs balance and the due-date alerts."""
from __future__ import annotations

import datetime

import httpx
import pytest

from app.db.models import AppUser, Vehicle
from app.db.session import async_session_factory
from app.main import app
from app.security import auth, passwords
from app.services.renewal_alerts import collect_renewals

VIN = "LSJA0000000000001"
D = datetime.datetime


class _Req:
    client = type("C", (), {"host": "127.0.0.1"})()
    headers = {"user-agent": "t"}


@pytest.fixture
async def web(db):
    async with async_session_factory() as s:
        s.add(Vehicle(owner_id=1, vin=VIN, name="Ebro", model="S900 PHEV", odometer=15000.0))
        user = AppUser(id=1, role="manager", username="juan", password_hash=passwords.hash_password("una-contraseña-larga-1"))
        s.add(user)
        await s.commit()
        token = await auth.create_session(s, user, _Req())
    c = httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://testserver")
    c.cookies.set(auth.COOKIE_NAME, token)
    yield c
    await c.aclose()


async def add_expense(web, **kw):
    body = {"date": "2026-03-01T00:00:00", "category": "tax", "title": "IVTM 2026", "amount": 85.5, **kw}
    r = await web.post(f"/api/vehicles/{VIN}/expenses", json=body)
    assert r.status_code == 200, r.text
    return r.json()


async def test_expenses_create_list_edit_and_delete(web) -> None:
    e = await add_expense(web, next_due_date="2027-03-01T00:00:00")
    assert e["category_label"] == "Impuestos" and e["amount"] == 85.5 and e["next_due_date"].startswith("2027-03-01")
    other = await add_expense(web, category="insurance", title="Seguro anual", amount=420, date="2025-06-01T00:00:00")
    assert [x["id"] for x in (await web.get(f"/api/vehicles/{VIN}/expenses")).json()] == [e["id"], other["id"]]      # most recent first
    assert [x["id"] for x in (await web.get(f"/api/vehicles/{VIN}/expenses?year=2025")).json()] == [other["id"]]
    r = await web.put(f"/api/expenses/{e['id']}", json={"amount": 90, "next_due_date": None, "notes": "pagado en el banco"})
    assert r.json()["amount"] == 90 and r.json()["next_due_date"] is None and r.json()["notes"] == "pagado en el banco"
    assert (await web.delete(f"/api/expenses/{e['id']}")).status_code == 200
    assert (await web.put(f"/api/expenses/{e['id']}", json={"amount": 1})).status_code == 404


async def test_expense_validations(web) -> None:
    base = {"date": "2026-03-01T00:00:00", "category": "tax", "title": "x", "amount": 1}
    for change in ({"category": "magia"}, {"amount": -1}, {"title": ""}, {"next_due_date": "2026-02-01T00:00:00"}):
        assert (await web.post(f"/api/vehicles/{VIN}/expenses", json={**base, **change})).status_code == 422, change
    assert (await web.post("/api/vehicles/NOEXISTE/expenses", json=base)).status_code == 404
    cats = {c["key"] for c in (await web.get("/api/expense-categories")).json()}
    assert {"insurance", "tax", "parking", "tolls", "fines", "wash", "accessories", "other"} <= cats


async def test_the_cost_balance_includes_expenses_and_their_breakdown(web) -> None:
    await web.put(f"/api/vehicles/{VIN}", json={"purchase_price": 30000, "delivery_date": "2025-01-10T00:00:00"})
    await add_expense(web, date="2026-03-01T00:00:00", amount=85.5)
    await add_expense(web, category="insurance", title="Seguro", amount=420, date="2026-06-01T00:00:00")
    await add_expense(web, category="insurance", title="Seguro 2", amount=30, date="2026-07-01T00:00:00")
    d = (await web.get(f"/api/vehicles/{VIN}/costs")).json()
    assert d["totals"]["expenses"] == 535.5 and d["totals"]["running"] == 535.5 and d["totals"]["all"] == 30535.5
    assert d["by_category"] == [{"key": "insurance", "label": "Seguro", "amount": 450.0}, {"key": "tax", "label": "Impuestos", "amount": 85.5}]
    assert any(x["type"] == "expense" and x["detail"] == "Seguro" for x in d["recent"])
    assert {m["month"]: m["expenses"] for m in d["monthly"]} == {"2026-03": 85.5, "2026-06": 420.0, "2026-07": 30.0}


async def test_the_profile_stores_renewal_coverage_and_identity_data(web) -> None:
    r = await web.put(f"/api/vehicles/{VIN}", json={
        "insurance_renewal_date": "2026-12-01T00:00:00", "insurance_coverage": "Todo riesgo", "insurance_assistance_phone": "900 123 456",
        "trim": "Luxury", "color": "Gris", "env_label": "ECO"})
    d = r.json()
    assert r.status_code == 200 and d["insurance_renewal_date"].startswith("2026-12-01") and d["insurance_coverage"] == "Todo riesgo"
    assert (d["trim"], d["color"], d["env_label"], d["insurance_assistance_phone"]) == ("Luxury", "Gris", "ECO", "900 123 456")
    d = (await web.put(f"/api/vehicles/{VIN}", json={"insurance_renewal_date": None, "color": ""})).json()
    assert d["insurance_renewal_date"] is None and d["color"] is None and d["trim"] == "Luxury"


# ───────────────────────── alerts ─────────────────────────
async def renewals_at(now):
    async with async_session_factory() as s:
        return await collect_renewals(s, VIN, now)


async def test_insurance_renewal_alert_by_stages(web) -> None:
    await web.put(f"/api/vehicles/{VIN}", json={"insurance_renewal_date": "2026-12-01T00:00:00"})
    assert await renewals_at(D(2026, 10, 1)) == []                                            # 61 days left
    (a,) = await renewals_at(D(2026, 11, 10))                                                  # 21 days
    assert a["key"] == "insurance:warning" and a["remaining_days"] == 21 and a["repeat_hours"] == 168
    (a,) = await renewals_at(D(2026, 11, 28))
    assert a["key"] == "insurance:urgent" and a["repeat_hours"] == 24
    (a,) = await renewals_at(D(2026, 12, 4))
    assert a["key"] == "insurance:overdue" and a["is_overdue"] and a["remaining_days"] == -3


async def test_an_expense_with_a_next_date_alerts_until_the_next_year_one_is_recorded(web) -> None:
    await add_expense(web, next_due_date="2027-03-01T00:00:00")
    (a,) = await renewals_at(D(2027, 2, 15))
    assert a["remaining_days"] == 14 and "IVTM 2026" in a["label"]
    await add_expense(web, date="2027-02-20T00:00:00", title="IVTM 2026", amount=88, next_due_date="2028-03-01T00:00:00")   # already paid: the new one rules
    assert await renewals_at(D(2027, 2, 25)) == []


async def test_insurance_dated_in_the_profile_is_not_alerted_twice(web) -> None:
    await web.put(f"/api/vehicles/{VIN}", json={"insurance_renewal_date": "2026-12-01T00:00:00"})
    await add_expense(web, category="insurance", title="Seguro", amount=400, next_due_date="2026-12-01T00:00:00", date="2025-12-01T00:00:00")
    assert len(await renewals_at(D(2026, 11, 20))) == 1


async def test_the_due_dates_switch_is_in_the_alert_settings(web) -> None:
    assert (await web.get("/api/notifications/config")).json()["notify_renewals"] is True
    r = await web.put("/api/notifications/config", json={"notify_renewals": False})
    assert r.status_code == 200 and (await web.get("/api/notifications/config")).json()["notify_renewals"] is False


async def test_expense_routes_require_a_session(db) -> None:
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://testserver") as c:
        for url in (f"/api/vehicles/{VIN}/expenses", "/api/expense-categories"):
            assert (await c.get(url)).status_code == 401
