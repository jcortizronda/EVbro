"""Hardening from the October 2026 audit: page and API security policy, request size limits, per-car PIN retry limit, orphan rows and
the MQTT publishing scope. Each test guards a hole that was open before."""
from __future__ import annotations

import httpx
import pytest
from sqlalchemy import select

from app.api import routes_auth
from app.config import settings
from app.db.models import AppUser, NotificationConfig, NotificationLog
from app.db.session import async_session_factory
from app.main import MAX_JSON_BODY_BYTES, app
from app.security import passwords
from app.services import command_policy, tenant

USER = "juan"
PASSWORD = "una-contraseña-larga-1"


@pytest.fixture(autouse=True)
def _clean_throttles():
    routes_auth.ip_throttle._fails.clear()
    routes_auth.pair_throttle._fails.clear()
    command_policy.pin_throttle._fails.clear()
    yield
    command_policy.pin_throttle._fails.clear()


@pytest.fixture
async def client(db):
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as c:
        yield c


async def _manager(client, username=USER, password=PASSWORD) -> None:
    async with async_session_factory() as s:
        s.add(AppUser(id=1, username=username, role="manager", password_hash=passwords.hash_password(password), vehicle_limit=2, member_limit=5))
        await s.commit()
    assert (await client.post("/api/auth/login", json={"username": username, "password": password})).status_code == 200


# ───────────────────────── Content Security Policy ─────────────────────────
async def test_the_page_carries_a_content_security_policy(client) -> None:
    csp = (await client.get("/")).headers["content-security-policy"]
    assert "default-src 'self'" in csp and "frame-ancestors 'none'" in csp and "object-src 'none'" in csp and "base-uri 'self'" in csp
    assert "connect-src 'self' ws://testserver" in csp              # the WebSocket origin is completed per request
    script_src = next(part for part in csp.split(";") if part.strip().startswith("script-src"))
    assert "unsafe-inline" not in script_src and "unsafe-eval" not in script_src     # no inline script, ever
    style_src = next(part for part in csp.split(";") if part.strip().startswith("style-src"))
    assert "unsafe-inline" not in style_src                                            # no style="…" either (tools/inline_styles.py)
    assert "https://tile.openstreetmap.org" in csp and "https://unpkg.com" in csp and "https://fonts.gstatic.com" in csp


async def test_api_answers_carry_a_data_only_policy(client) -> None:
    r = await client.get("/api/auth/status")
    assert r.headers["content-security-policy"] == "default-src 'none'; frame-ancestors 'self'"


async def test_static_files_are_not_given_the_page_policy(client) -> None:
    r = await client.get("/static/js/core/auth.js")
    assert r.status_code == 200 and "content-security-policy" not in r.headers


# ───────────────────────── request size limits ─────────────────────────
async def test_an_oversized_json_body_is_refused_before_any_work(client) -> None:
    body = b'{"username": "juan", "password": "' + b"x" * (MAX_JSON_BODY_BYTES + 10) + b'"}'
    r = await client.post("/api/auth/login", content=body, headers={"Content-Type": "application/json"})
    assert r.status_code == 413


async def test_a_binary_body_on_a_json_route_is_a_422_that_does_not_echo_it(client) -> None:
    """Seen in production: a multipart body on `/api/auth/login` ended in a 500 because FastAPI's default handler could not encode the
    raw bytes it echoes back as `input`."""
    r = await client.post("/api/auth/login", files={"file": ("blob.bin", b"\x00\xff" * 500, "application/octet-stream")})
    assert r.status_code == 422
    body = r.json()["detail"]
    assert body and body[0]["loc"] == ["body"] and "input" not in body[0]


async def test_login_fields_are_bounded(client) -> None:
    r = await client.post("/api/auth/login", json={"username": USER, "password": "x" * (passwords.MAX_LENGTH + 1)})
    assert r.status_code == 422
    r = await client.post("/api/auth/login", json={"username": "u" * 65, "password": PASSWORD})
    assert r.status_code == 422


async def test_command_history_limit_is_bounded(client) -> None:
    await _manager(client)
    vin = (await client.post("/api/vehicles", json={"name": "Coche"})).json()["vin"]
    assert (await client.get(f"/api/vehicles/{vin}/commands/history?limit=50")).status_code == 200
    assert (await client.get(f"/api/vehicles/{vin}/commands/history?limit=100000")).status_code == 422


# ───────────────────────── PIN retries are per car ─────────────────────────
def test_pin_retry_limit_of_one_car_does_not_lock_another() -> None:
    for _ in range(5):
        with pytest.raises(command_policy.CommandRejected) as exc:
            command_policy.authorize("desbloquear", "0000", "1234", vin="CAR-A")
        assert exc.value.reason == "pin_invalid"
    with pytest.raises(command_policy.CommandRejected) as exc:
        command_policy.authorize("desbloquear", "1234", "1234", vin="CAR-A")
    assert exc.value.reason == "pin_locked"
    assert command_policy.authorize("desbloquear", "1234", "1234", vin="CAR-B") == "1234"      # another manager's car is untouched


# ───────────────────────── deleting an account leaves no rows behind ─────────────────────────
async def test_deleting_an_account_removes_its_notification_channels_and_history(db) -> None:
    async with async_session_factory() as s:
        s.add(AppUser(id=7, username="ana", role="member", parent_id=1, password_hash="x"))
        s.add(NotificationConfig(user_id=7, telegram_bot_token="123:secret"))
        s.add(NotificationLog(user_id=7, category="security", title="t", message="m", channel="telegram", success=True))
        await s.commit()
        await tenant.delete_member(s, 7)
        await s.commit()
        assert (await s.execute(select(NotificationConfig).where(NotificationConfig.user_id == 7))).first() is None
        assert (await s.execute(select(NotificationLog).where(NotificationLog.user_id == 7))).first() is None
        assert (await s.execute(select(AppUser).where(AppUser.id == 7))).first() is None


# ───────────────────────── geofence alerts obey each geofence's switches ─────────────────────────
async def test_geofence_alerts_follow_the_switches_of_each_geofence(db, monkeypatch) -> None:
    from app.db.models import Geofence, Vehicle
    from app.services.notification_service import NotificationService
    from app.services.poller import VehiclePoller

    sent = []

    async def _enter(*args, **kwargs):
        sent.append("enter")

    async def _exit(*args, **kwargs):
        sent.append("exit")

    monkeypatch.setattr(NotificationService, "notify_geofence_enter", _enter)
    monkeypatch.setattr(NotificationService, "notify_geofence_exit", _exit)
    poller = VehiclePoller.get_instance()
    poller._last_geofence_by_vin.clear()
    async with async_session_factory() as s:
        s.add(AppUser(id=1, username="ana", role="manager", password_hash="x"))
        s.add(Vehicle(vin="GF-CAR", owner_id=1, name="Coche"))
        s.add(Geofence(name="Casa", latitude=41.0, longitude=-1.0, radius_meters=300, owner_id=1, notify_enter=False, notify_exit=True))
        await s.commit()
        vehicle = (await s.execute(select(Vehicle).where(Vehicle.vin == "GF-CAR"))).scalar_one()
        base = {"state": "parked", "battery_level": 50, "odometer_km": 1000}
        await poller._check_and_dispatch_alerts(s, vehicle, {**base, "latitude": 42.0, "longitude": -2.0})   # far away: just remembers
        await poller._check_and_dispatch_alerts(s, vehicle, {**base, "latitude": 41.0, "longitude": -1.0})   # enters: switched off
        await poller._check_and_dispatch_alerts(s, vehicle, {**base, "latitude": 42.0, "longitude": -2.0})   # leaves: switched on
    assert sent == ["exit"]


# ───────────────────────── MQTT scope ─────────────────────────
def test_mqtt_publishes_only_the_server_owner_cars_by_default() -> None:
    assert settings.MQTT_PUBLISH_SCOPE == "owner"


async def test_the_app_refuses_to_start_when_the_schema_cannot_be_migrated(monkeypatch) -> None:
    """A rolled-back migration must not leave the app running on the old schema (it did, on 2026-10-30)."""
    from app import main as m

    async def boom():
        raise RuntimeError("relation does not exist")
    monkeypatch.setattr(m, "init_db", boom)
    with pytest.raises(RuntimeError, match="no se pudo migrar"):
        async with m.lifespan(m.app):
            pass
