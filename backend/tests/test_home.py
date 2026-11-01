"""Home page: do not claim what is unknown (lock, doors, windows) and the real state of the alarm and scheduled charging."""
from __future__ import annotations

import httpx
import pytest

from app.core import commands
from app.core.telemetry import parse_telemetry_data
from app.db.models import AppUser, Vehicle
from app.db.session import async_session_factory
from app.main import app
from app.security import auth, passwords
from app.services.poller import VehiclePoller

VIN = "LSJA0000000000001"


# ───────────────────────── the parser does not make up «closed» ─────────────────────────
def test_without_data_the_car_is_not_assumed_locked() -> None:
    t = parse_telemetry_data({})
    assert t["is_locked"] is None
    assert all(v is None for v in t["doors"].values())
    assert all(v is None for v in t["windows"].values())
    assert t["appointment_charge_on"] is None


def test_with_data_it_is_read_as_before() -> None:
    closed = parse_telemetry_data({"doorLock": "0", "trunkDoor": "0", "frontLeftWindowState": "0"})
    assert closed["is_locked"] is True and closed["doors"]["trunk"] == 0 and closed["windows"]["fl"] == 0
    opened = parse_telemetry_data({"doorLock": "1", "trunkDoor": "1", "sunroofState": "1"})
    assert opened["is_locked"] is False and opened["doors"]["trunk"] == 1 and opened["windows"]["sunroof"] == 1
    # what has not arrived stays unknown even if other fields did arrive
    assert opened["doors"]["fl"] is None and opened["windows"]["fl"] is None


@pytest.mark.parametrize("raw,expected", [("0", False), ("1", True), ("2", True), (None, None), ("", None)])
def test_scheduled_charging_on(raw, expected) -> None:
    data = {} if raw is None else {"appointmentChargeState": raw}
    assert parse_telemetry_data(data)["appointment_charge_on"] is expected


def test_unknown_alarm_or_lock_does_not_trigger_security_alerts() -> None:
    """The poller alerts «sin cerrar» only if is_locked is False; None (no data) must not count."""
    t = parse_telemetry_data({"engineState": "0"})
    assert t["is_locked"] is None and (t.get("is_locked") is False) is False


# ───────────────────────── alarm state ─────────────────────────
class _Req:
    client = type("C", (), {"host": "127.0.0.1"})()
    headers = {"user-agent": "t"}


async def _client(db):
    async with async_session_factory() as s:
        s.add(Vehicle(owner_id=1, vin=VIN, name="Ebro"))
        user = AppUser(id=1, role="manager", username="juan", password_hash=passwords.hash_password("una-contraseña-larga-1"))
        s.add(user)
        await s.commit()
        token = await auth.create_session(s, user, _Req())
    c = httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://testserver")
    c.cookies.set(auth.COOKIE_NAME, token)
    return c


@pytest.mark.parametrize("returned,expected", [(1, True), (0, False), (None, None)])
async def test_alarm_state(db, monkeypatch, returned, expected) -> None:
    monkeypatch.setattr(commands, "query_theft_switch", lambda ctx: returned)
    VehiclePoller._contexts = {}
    async with await _client(db) as c:
        r = await c.get(f"/api/vehicles/{VIN}/alarm")
    assert r.status_code == 200 and r.json() == {"enabled": expected}


async def test_alarm_of_an_unknown_vehicle_gives_404_and_requires_a_session(db) -> None:
    async with await _client(db) as c:
        assert (await c.get("/api/vehicles/LSJA9999999999999/alarm")).status_code == 404
        c.cookies.clear()
        assert (await c.get(f"/api/vehicles/{VIN}/alarm")).status_code == 401
