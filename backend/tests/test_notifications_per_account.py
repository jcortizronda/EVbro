"""Notifications per account: each account chooses what it receives and through which channels; car settings belong to the car."""
from __future__ import annotations

import socket

import httpx
import pytest
from sqlalchemy import select

from app.config import settings
from app.db.models import AppUser, DeviceAccess, NotificationLog, PushSubscription, Vehicle
from app.db.session import async_session_factory
from app.main import app
from app.security import auth, passwords
from app.services import poller as poller_mod
from app.services.notification_service import NotificationService, webhook_url_problem
from app.services.poller import VehiclePoller

PW = "una-contraseña-larga-1"
VIN_A, VIN_B = "LSJAAAAAAAAAAAAAA", "LSJBBBBBBBBBBBBBB"


class _Req:
    client = type("C", (), {"host": "127.0.0.1"})()
    headers = {"user-agent": "t"}


@pytest.fixture
async def world(db):
    """Ana (manager, owns car A), Lucia (member linked to A), Pablo (member of Ana, NOT linked), Beto (manager, owns car B)."""
    h = passwords.hash_password(PW)
    async with async_session_factory() as s:
        s.add_all([
            AppUser(id=1, username="admin", role="admin", is_admin=True, password_hash=h, vehicle_limit=0, member_limit=0),
            AppUser(id=2, username="ana", role="manager", password_hash=h, vehicle_limit=2, member_limit=5),
            AppUser(id=3, username="beto", role="manager", password_hash=h, vehicle_limit=2, member_limit=5),
            AppUser(id=4, username="lucia", role="member", parent_id=2, password_hash=h),
            AppUser(id=5, username="pablo", role="member", parent_id=2, password_hash=h),
        ])
        s.add_all([Vehicle(owner_id=2, vin=VIN_A, name="Coche de Ana"), Vehicle(owner_id=3, vin=VIN_B, name="Coche de Beto")])
        await s.flush()
        s.add(DeviceAccess(user_id=4, vin=VIN_A, level="view", granted_by=2))
        for uid in (1, 2, 3, 4, 5):
            s.add(PushSubscription(endpoint_hash=f"{uid}" * 64, user_id=uid, subscription={"endpoint": f"https://push.example/u{uid}", "keys": {}}))
        await s.commit()


@pytest.fixture
def pushed(monkeypatch):
    sent = []

    def fake(cls, subscription, payload):
        sent.append(subscription["endpoint"].rsplit("/", 1)[1])
        return True, None, False

    monkeypatch.setattr(NotificationService, "_webpush_blocking", classmethod(fake))
    return sent


async def client_for(user_id):
    async with async_session_factory() as s:
        user = await s.get(AppUser, user_id)
        token = await auth.create_session(s, user, _Req())
    c = httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://testserver")
    c.cookies.set(auth.COOKIE_NAME, token)
    return c


async def set_flag(user_id, **flags):
    async with async_session_factory() as s:
        cfg = await NotificationService.get_or_create_config(s, user_id)
        for k, v in flags.items():
            setattr(cfg, k, v)
        await s.commit()


# ───────────────────────── who receives a car's notifications ─────────────────────────
async def test_a_notification_goes_to_the_owner_and_the_linked_members_only(world, pushed) -> None:
    await NotificationService.dispatch("security", "Alarma", "Puerta", vin=VIN_A)
    assert sorted(pushed) == ["u2", "u4"]                    # Ana and Lucia; not Pablo (no link), Beto (other space) or the admin
    pushed.clear()
    await NotificationService.dispatch("security", "Alarma", "Puerta", vin=VIN_B)
    assert pushed == ["u3"]


async def test_each_account_chooses_what_it_receives(world, pushed) -> None:
    await set_flag(4, notify_security_alerts=False)         # Lucia does not want security alerts, Ana does
    await NotificationService.dispatch("security", "Alarma", "Puerta", vin=VIN_A)
    assert pushed == ["u2"]
    pushed.clear()
    await NotificationService.dispatch("maintenance", "Revisión", "Toca", vin=VIN_A)      # but she still gets the rest
    assert sorted(pushed) == ["u2", "u4"]


async def test_channels_are_per_account(world, pushed, monkeypatch) -> None:
    calls = []

    async def telegram(cls, token, chat, title, message):
        calls.append((token, chat))
        return True, None

    monkeypatch.setattr(NotificationService, "_send_telegram", classmethod(telegram))
    await set_flag(2, telegram_enabled=True, telegram_bot_token="tok-ana", telegram_chat_id="111")
    await set_flag(4, telegram_enabled=True, telegram_bot_token="tok-lucia", telegram_chat_id="222")
    await NotificationService.dispatch("maintenance", "Revisión", "Toca", vin=VIN_A)
    assert sorted(calls) == [("tok-ana", "111"), ("tok-lucia", "222")]
    async with async_session_factory() as s:
        rows = (await s.execute(select(NotificationLog.user_id, NotificationLog.channel).where(NotificationLog.channel == "telegram"))).all()
    assert sorted(rows) == [(2, "telegram"), (4, "telegram")]                           # every log belongs to the account it was sent to


async def test_a_notification_without_car_or_recipient_is_not_broadcast(world, pushed) -> None:
    assert await NotificationService.dispatch("security", "x", "y") == []
    assert pushed == []
    assert await NotificationService.dispatch("security", "x", "y", vin="NOEXISTE") == []


async def test_inactive_accounts_and_members_of_a_frozen_manager_receive_nothing(world, pushed) -> None:
    async with async_session_factory() as s:
        (await s.get(AppUser, 4)).is_active = False
        await s.commit()
    await NotificationService.dispatch("security", "Alarma", "Puerta", vin=VIN_A)
    assert pushed == ["u2"]
    async with async_session_factory() as s:
        (await s.get(AppUser, 4)).is_active = True
        (await s.get(AppUser, 2)).is_active = False
        await s.commit()
    pushed.clear()
    await NotificationService.dispatch("security", "Alarma", "Puerta", vin=VIN_A)
    assert pushed == []                                                                  # the owner is frozen, so is the member


async def test_a_user_id_sends_only_to_that_account(world, pushed) -> None:
    await NotificationService.dispatch("test", "Prueba", "Hola", user_id=4)
    assert pushed == ["u4"]


# ───────────────────────── the configuration routes ─────────────────────────
async def test_every_account_edits_only_its_own_configuration(world) -> None:
    ana, lucia = await client_for(2), await client_for(4)
    try:
        assert (await lucia.put("/api/notifications/config", json={"notify_geofence_enter": False, "telegram_chat_id": "999"})).status_code == 200
        assert (await lucia.get("/api/notifications/config")).json()["notify_geofence_enter"] is False
        assert (await ana.get("/api/notifications/config")).json()["notify_geofence_enter"] is True              # Ana's is untouched
        assert (await ana.get("/api/notifications/config")).json()["telegram_chat_id"] == ""
        cfg = (await lucia.get("/api/notifications/config")).json()
        assert "fuel_low_threshold" not in cfg and "low_battery_threshold" not in cfg                             # those are car settings now
    finally:
        await ana.aclose()
        await lucia.aclose()


async def test_history_and_test_notifications_are_per_account(world, pushed) -> None:
    ana, lucia = await client_for(2), await client_for(4)
    try:
        assert (await lucia.post("/api/notifications/test", json={"channel": "webpush"})).status_code == 200
        assert pushed == ["u4"]
        assert [x["category"] for x in (await lucia.get("/api/notifications/logs")).json()] == ["test"]
        assert (await ana.get("/api/notifications/logs")).json() == []
        await ana.delete("/api/notifications/logs")
        assert len((await lucia.get("/api/notifications/logs")).json()) == 1                                     # clearing mine does not clear hers
    finally:
        await ana.aclose()
        await lucia.aclose()


# ───────────────────────── webhooks cannot reach the server's own network unless the instance allows it ─────────────────────────
def test_webhook_urls_are_checked(monkeypatch) -> None:
    assert webhook_url_problem("") is None
    assert "http" in webhook_url_problem("ftp://x.example/hook")
    monkeypatch.setattr(settings, "ALLOW_PRIVATE_WEBHOOKS", True)
    assert webhook_url_problem("http://192.168.1.10:8123/api/webhook/x") is None                 # a trusted instance may call its LAN
    monkeypatch.setattr(settings, "ALLOW_PRIVATE_WEBHOOKS", False)

    def resolve(ip):
        return lambda *a, **k: [(socket.AF_INET, socket.SOCK_STREAM, 6, "", (ip, 80))]

    for ip in ("127.0.0.1", "10.0.0.5", "192.168.1.10", "169.254.169.254", "172.16.0.1", "0.0.0.0"):
        monkeypatch.setattr(socket, "getaddrinfo", resolve(ip))
        assert "interna" in webhook_url_problem("https://hook.example/x"), ip
    monkeypatch.setattr(socket, "getaddrinfo", resolve("93.184.216.34"))
    assert webhook_url_problem("https://hook.example/x") is None


async def test_saving_an_internal_webhook_is_refused_by_default(world, monkeypatch) -> None:
    monkeypatch.setattr(settings, "ALLOW_PRIVATE_WEBHOOKS", False)
    monkeypatch.setattr(socket, "getaddrinfo", lambda *a, **k: [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("127.0.0.1", 80))])
    c = await client_for(4)
    try:
        r = await c.put("/api/notifications/config", json={"webhook_enabled": True, "webhook_url": "http://localhost:8080/admin"})
        assert r.status_code == 422 and "interna" in r.json()["detail"]
    finally:
        await c.aclose()


# ───────────────────────── car settings belong to the car ─────────────────────────
async def test_the_low_fuel_threshold_is_a_car_setting_and_alerts_go_per_car(world, monkeypatch) -> None:
    calls = []

    async def fuel(cls, name, pct, rng=None, **kwargs):
        calls.append((name, pct, kwargs.get("vin")))

    monkeypatch.setattr(poller_mod.NotificationService, "notify_fuel_low", classmethod(fuel))
    p = VehiclePoller.get_instance()
    p.__dict__.pop("_last_fuel_alert_time", None)
    async with async_session_factory() as s:
        a = await s.get(Vehicle, 1) if False else (await s.execute(select(Vehicle).where(Vehicle.vin == VIN_A))).scalar_one()
        a.fuel_low_threshold = 30
        b = (await s.execute(select(Vehicle).where(Vehicle.vin == VIN_B))).scalar_one()
        await s.commit()
        await p._check_and_dispatch_alerts(s, a, {"fuel_level": 25.0})         # 25 % <= 30 %: warns
        await p._check_and_dispatch_alerts(s, b, {"fuel_level": 25.0})         # 25 % > the default 15 %: does not
    assert calls == [("Coche de Ana", 25.0, VIN_A)]


async def test_only_the_owner_edits_the_car_thresholds_and_they_are_validated(world) -> None:
    ana, lucia = await client_for(2), await client_for(4)
    try:
        r = await ana.put(f"/api/vehicles/{VIN_A}", json={"fuel_low_threshold": 30, "low_battery_threshold": 25, "tyre_pressure_tolerance_pct": 12})
        assert r.status_code == 200 and (r.json()["fuel_low_threshold"], r.json()["low_battery_threshold"]) == (30, 25)
        assert (await lucia.put(f"/api/vehicles/{VIN_A}", json={"fuel_low_threshold": 99})).status_code == 403
        for bad in ({"fuel_low_threshold": 0}, {"low_battery_threshold": 101}):
            assert (await ana.put(f"/api/vehicles/{VIN_A}", json=bad)).status_code == 422
        assert [v["fuel_low_threshold"] for v in (await lucia.get("/api/vehicles")).json()] == [30]          # the member sees the car's setting
    finally:
        await ana.aclose()
        await lucia.aclose()
