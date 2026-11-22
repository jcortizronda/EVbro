"""A manager deletes its own account from Ajustes → Cuenta: the whole space goes (cars and their history, member accounts, the
Ebro account and its session, channels, files and what the server kept in memory), and nobody else's.

It cannot be undone, so the route asks for the username typed by hand and for the password.
"""
from __future__ import annotations

import datetime
import os

import httpx
import pytest
from sqlalchemy import func, select

from app.api import routes_auth
from app.config import settings
from app.db.models import (
    AppUser,
    AuthSession,
    Base,
    CatalogEntry,
    DeviceAccess,
    Drive,
    EbroAccount,
    Geofence,
    MqttLink,
    NotificationConfig,
    NotificationLog,
    PushSubscription,
    Refuel,
    TelemetrySnapshot,
    Vehicle,
)
from app.db.session import async_session_factory
from app.main import app
from app.security import auth, passwords
from app.services import trip_detector, uploads
from app.services.car_events import CarEventHub
from app.services.ebro_sessions import account_session_path, store_session
from app.services.poller import VehiclePoller

VIN, OTHER_VIN = "LSJD0000000000041", "LSJD0000000000042"
PW = "una-contraseña-larga-1"
PDF = b"%PDF-1.4\n%\xe2\xe3\xcf\xd3\n1 0 obj<<>>endobj\ntrailer<<>>\n%%EOF\n"
T = datetime.datetime(2026, 11, 20, 10, 0)


class _Req:
    client = type("C", (), {"host": "127.0.0.1"})()
    headers = {"user-agent": "t"}


@pytest.fixture
async def world(db, data_dir, monkeypatch):
    """Manager 1 (juan) with a car, a member (3), history, channels and files; manager 2 (otro) with its own. {user id: client}."""
    monkeypatch.setattr(routes_auth, "ip_throttle", routes_auth.LoginThrottle(max_failures=8))
    monkeypatch.setattr("app.api.routes_account.ip_throttle", routes_auth.ip_throttle)
    h = passwords.hash_password(PW)
    async with async_session_factory() as s:
        s.add_all([AppUser(id=1, role="manager", username="juan", password_hash=h), AppUser(id=2, role="manager", username="otro", password_hash=h),
                   AppUser(id=3, role="member", username="miembro", parent_id=1, password_hash=h)])
        s.add_all([Vehicle(owner_id=1, vin=VIN, name="Ebro"), Vehicle(owner_id=2, vin=OTHER_VIN, name="Otro")])
        await s.flush()
        for uid, vin in ((1, VIN), (2, OTHER_VIN)):
            s.add_all([
                Drive(vin=vin, start_time=T, end_time=T + datetime.timedelta(minutes=20), distance_km=12),
                Refuel(vin=vin, timestamp=T, liters=30, detected=False),
                TelemetrySnapshot(vin=vin, timestamp=T, state="parked", latitude=37.0, longitude=-6.0),
                Geofence(owner_id=uid, name="Casa", latitude=37.0, longitude=-6.0),
                EbroAccount(id=uid, user_id=uid, phone=f"60000000{uid}", password="secreta", is_active=True),
                MqttLink(user_id=uid, host="broker.example", password="clave"),
                NotificationConfig(user_id=uid, telegram_enabled=True, telegram_bot_token="token"),
                NotificationLog(user_id=uid, category="security", title="Aviso", message="Texto", channel="app"),
                PushSubscription(endpoint_hash=str(uid) * 64, user_id=uid, subscription={"endpoint": f"https://push.example/{uid}", "keys": {}}),
                CatalogEntry(model="S900 PHEV", category="oil", name=f"Aceite {uid}", status="approved", proposed_by=uid),
            ])
        s.add(DeviceAccess(user_id=3, vin=VIN, level="view", granted_by=1))
        s.add(NotificationLog(user_id=3, category="security", title="Aviso", message="Texto", channel="app"))
        await s.commit()
        clients = {}
        for uid in (1, 2, 3):
            token = await auth.create_session(s, await s.get(AppUser, uid), _Req())
            c = httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://testserver")
            c.cookies.set(auth.COOKIE_NAME, token)
            clients[uid] = c
    for uid in (1, 2):                                        # the Ebro session of each account and the «channel stopped» mark
        store_session(account_session_path(uid), {"access_token": "x", "refresh_token": "y"})
        os.makedirs(os.path.join(settings.DATA_DIR, "car_channel"), exist_ok=True)
        open(os.path.join(settings.DATA_DIR, "car_channel", f"stopped-{uid}"), "w").close()
    for c, vin in ((clients[1], VIN), (clients[2], OTHER_VIN)):
        r = await c.post(f"/api/vehicles/{vin}/documents", data={"kind": "purchase_invoice"}, files={"file": ("factura.pdf", PDF, "application/pdf")})
        assert r.status_code == 200, r.text
    poller = VehiclePoller.get_instance()
    monkeypatch.setattr(VehiclePoller, "_contexts", {}, raising=False)
    monkeypatch.setattr(VehiclePoller, "_latest_snapshots", {VIN: {"latitude": 37.0}, OTHER_VIN: {"latitude": 37.1}}, raising=False)
    monkeypatch.setattr(trip_detector, "_ACTIVE_DRIVES", {VIN: 1, OTHER_VIN: 2})
    poller._get_or_create_ctx(VIN).pin = "1234"
    poller._get_or_create_ctx(OTHER_VIN)
    monkeypatch.setattr(CarEventHub.get_instance(), "_services", {})
    yield clients
    for c in clients.values():
        await c.aclose()


async def count(model, **where) -> int:
    async with async_session_factory() as s:
        stmt = select(func.count()).select_from(model)
        for column, value in where.items():
            stmt = stmt.where(getattr(model, column) == value)
        return (await s.execute(stmt)).scalar_one()


def files(folder: str) -> list:
    path = os.path.join(settings.DATA_DIR, folder)
    return sorted(os.listdir(path)) if os.path.isdir(path) else []


async def test_the_account_is_only_deleted_with_the_username_and_the_password(world) -> None:
    juan = world[1]
    assert (await juan.post("/api/account/delete", json={"confirm": "borrar", "password": PW})).status_code == 409
    assert (await juan.post("/api/account/delete", json={"confirm": "juan", "password": "no-es-esta"})).status_code == 403
    assert (await juan.post("/api/account/delete", json={"confirm": "juan"})).status_code == 422
    assert await count(AppUser) == 3 and await count(Vehicle) == 2 and len(files(uploads.DOCUMENTS_FOLDER)) == 2
    assert (await juan.get("/api/vehicles")).status_code == 200                       # still signed in, nothing touched


async def test_wrong_passwords_are_throttled_like_a_login(world) -> None:
    juan = world[1]
    for _ in range(8):
        assert (await juan.post("/api/account/delete", json={"confirm": "juan", "password": "no-es-esta"})).status_code == 403
    r = await juan.post("/api/account/delete", json={"confirm": "juan", "password": PW})
    assert r.status_code == 429 and await count(AppUser) == 3                         # not even the right one, for a while


async def test_a_member_account_and_a_visitor_cannot_delete_anything(world) -> None:
    assert (await world[3].post("/api/account/delete", json={"confirm": "miembro", "password": PW})).status_code == 403
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://testserver") as nobody:
        assert (await nobody.post("/api/account/delete", json={"confirm": "juan", "password": PW})).status_code == 401
    assert await count(AppUser) == 3 and await count(Vehicle) == 2


async def test_deleting_the_account_removes_its_whole_space_and_nobody_elses(world) -> None:
    juan, otro = world[1], world[2]
    r = await juan.post("/api/account/delete", json={"confirm": " Juan ", "password": PW})
    assert r.status_code == 200, r.text
    assert r.json()["removed"] == {"vehicles": 1, "members": 1}

    # The account, its member and every row keyed by them or by their car
    async with async_session_factory() as s:
        assert [u for (u,) in (await s.execute(select(AppUser.username))).all()] == ["otro"]
        for table in Base.metadata.sorted_tables:
            if "vin" in table.c:
                left = (await s.execute(select(table.c.vin).where(table.c.vin == VIN))).all()
                assert left == [], f"{table.name} keeps rows of the deleted car"
            if "user_id" in table.c:
                left = (await s.execute(select(table.c.user_id).where(table.c.user_id.in_((1, 3))))).all()
                assert left == [], f"{table.name} keeps rows of the deleted accounts"
        assert (await s.execute(select(Geofence.owner_id))).scalars().all() == [2]
        # what it proposed to the shared catalogue stays, without its author
        assert sorted((e.name, e.proposed_by) for e in (await s.execute(select(CatalogEntry))).scalars().all()) == [("Aceite 1", None), ("Aceite 2", 2)]

    # The other manager keeps everything
    for model in (Drive, Refuel, TelemetrySnapshot):
        assert await count(model, vin=OTHER_VIN) == 1
    for model in (EbroAccount, MqttLink, NotificationConfig, NotificationLog, PushSubscription, AuthSession):
        assert await count(model, user_id=2) == 1, model.__name__
    assert (await otro.get("/api/vehicles")).status_code == 200 and len((await otro.get(f"/api/vehicles/{OTHER_VIN}/documents")).json()) == 1

    # Files: the document, the car's folder, the Ebro session and the channel mark
    assert len(files(uploads.DOCUMENTS_FOLDER)) == 1
    assert not os.path.exists(account_session_path(1)) and os.path.exists(account_session_path(2))
    assert files("car_channel") == ["stopped-2"]

    # Memory: no session context with its PIN, no last reading, no open trip of the deleted car
    poller = VehiclePoller.get_instance()
    assert list(poller._contexts) == [OTHER_VIN] and list(poller._latest_snapshots) == [OTHER_VIN]
    assert trip_detector._ACTIVE_DRIVES == {OTHER_VIN: 2}

    # And the session is over: the cookie is cleared and neither the old one nor the password opens anything
    assert auth.COOKIE_NAME in r.headers.get("set-cookie", "")
    assert (await juan.get("/api/vehicles")).status_code == 401
    assert (await world[3].get("/api/vehicles")).status_code == 401
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://testserver") as c:
        assert (await c.post("/api/auth/login", json={"username": "juan", "password": PW})).status_code == 401
