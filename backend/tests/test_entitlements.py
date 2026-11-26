"""app/entitlements.py: the question «may this account use this area?». Without a rule of the installation the answer is always yes."""
from __future__ import annotations

import httpx
import pytest

from app import entitlements
from app.db.models import AppUser, Vehicle
from app.db.session import async_session_factory
from app.main import app
from app.security import auth, passwords

VIN = "LSJA0000000000051"


class _Req:
    client = type("C", (), {"host": "127.0.0.1"})()
    headers = {"user-agent": "t"}


@pytest.fixture
async def ana(db, data_dir):
    """Ana, a manager with one car, signed in. The rule in force before the test (none, or the installation's own) is put back after."""
    before = entitlements._checker
    entitlements.set_checker(None)
    async with async_session_factory() as s:
        user = AppUser(id=2, username="ana", role="manager", password_hash=passwords.hash_password("una-contraseña-larga-1"), vehicle_limit=2, member_limit=2)
        s.add_all([user, Vehicle(owner_id=2, vin=VIN, name="Coche de Ana")])
        await s.commit()
        token = await auth.create_session(s, user, _Req())
    c = httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://testserver")
    c.cookies.set(auth.COOKIE_NAME, token)
    yield c
    await c.aclose()
    entitlements.set_checker(before)


AREAS = [("get", f"/api/vehicles/{VIN}/stats", "stats"), ("get", f"/api/vehicles/{VIN}/costs", "costs"),
         ("get", f"/api/vehicles/{VIN}/expenses", "costs"), ("get", f"/api/vehicles/{VIN}/maintenance", "maintenance"),
         ("get", f"/api/vehicles/{VIN}/maintenance/plan", "maintenance"), ("get", f"/api/vehicles/{VIN}/maintenance/advice", "maintenance"),
         ("get", "/api/members", "members"), ("get", f"/api/vehicles/{VIN}/book", "book"), ("get", f"/api/vehicles/{VIN}/traccar", "integrations"),
         ("get", "/api/account/mqtt", "integrations")]
ALWAYS = [("get", "/api/vehicles"), ("get", f"/api/vehicles/{VIN}/charge/plan"), ("get", f"/api/vehicles/{VIN}/drives"),
          ("get", f"/api/vehicles/{VIN}/climate-schedules"), ("get", "/api/account/export"), ("get", "/api/commands/catalog")]


async def test_without_a_rule_every_account_has_everything(ana) -> None:
    status = (await ana.get("/api/auth/status")).json()
    assert status["disabled_features"] == [] and status["history_days"] == 30
    for method, path, _area in AREAS:
        r = await getattr(ana, method)(path)
        assert r.status_code == 200 and entitlements.HEADER not in r.headers, (path, r.status_code)


async def test_an_installation_can_switch_areas_off_and_nothing_else_is_touched(ana) -> None:
    off = {"stats", "costs", "maintenance", "members", "book", "integrations", "automation"}
    asked = []

    async def rule(session, user, feature):
        asked.append((user.username, feature))
        return feature not in off

    entitlements.set_checker(rule)
    status = (await ana.get("/api/auth/status")).json()
    assert set(status["disabled_features"]) == off                        # the page is told, so it can hide what is not there
    for method, path, area in AREAS:
        r = await getattr(ana, method)(path)
        assert r.status_code == 403 and r.headers[entitlements.HEADER] == area and "no incluye" in r.json()["detail"], path
    for method, path in ALWAYS:
        r = await getattr(ana, method)(path)                                   # the car, its orders, its history, the account's data
        assert r.status_code != 403 and entitlements.HEADER not in r.headers, (path, r.status_code)
    # what EVbro does by itself on a timetable: setting it is the area; looking at it and removing it are not
    assert (await ana.put(f"/api/vehicles/{VIN}/charge/target", json={"target_pct": 80})).status_code == 403
    assert (await ana.post(f"/api/vehicles/{VIN}/climate-schedules", json={})).status_code == 403
    # an account that loses an area can still remove what it had in it
    assert (await ana.delete(f"/api/vehicles/{VIN}/traccar")).status_code != 403
    assert (await ana.delete("/api/account/mqtt")).status_code != 403
    assert (await ana.delete("/api/maintenance/999")).status_code != 403
    assert ("ana", "stats") in asked


async def test_a_rule_that_fails_never_locks_anybody_out(ana) -> None:
    async def broken(session, user, feature):
        raise RuntimeError("the installation's own code is wrong")

    entitlements.set_checker(broken)
    assert (await ana.get(f"/api/vehicles/{VIN}/stats")).status_code == 200
    assert (await ana.get("/api/auth/status")).json()["disabled_features"] == []


def test_only_known_areas_can_be_asked_for() -> None:
    with pytest.raises(AssertionError):
        entitlements.require("something-else")
