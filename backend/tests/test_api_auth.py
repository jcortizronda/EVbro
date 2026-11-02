"""API access: first user, login, sessions, CSRF, WebSocket and unprotected routes."""
from __future__ import annotations

import asyncio
import datetime
import re

import httpx
import pytest
from sqlalchemy import select, update
from starlette.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from app.api import routes_auth
from app.config import settings
from app.db.models import AuthSession
from app.db.session import async_session_factory
from app.main import app
from app.security import auth, passwords
from tests.paths import SRC

SETUP_CODE = "codigo-de-prueba"
USER = "juan"
PASSWORD = "una-contraseña-larga-1"


@pytest.fixture(autouse=True)
def _clean_state(monkeypatch, data_dir):
    monkeypatch.setattr(settings, "SETUP_CODE", SETUP_CODE)
    auth.reset_setup_code()
    routes_auth.ip_throttle._fails.clear()
    routes_auth.pair_throttle._fails.clear()
    yield
    auth.reset_setup_code()


@pytest.fixture
async def client(db):
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as c:
        yield c


async def create_user(client, username=USER, password=PASSWORD):
    """A manager account created straight in the database, signed in through the login (the app has no first-user setup: the
    administrator creates accounts from the admin console)."""
    from app.db.models import AppUser
    async with async_session_factory() as s:
        s.add(AppUser(id=1, username=username, role="manager", password_hash=passwords.hash_password(password), vehicle_limit=2, member_limit=5))
        await s.commit()
    r = await client.post("/api/auth/login", json={"username": username, "password": password})
    assert r.status_code == 200, r.text
    return r


# ───────────────────────── status and doors ─────────────────────────
async def test_status_without_accounts_says_so(client) -> None:
    r = await client.get("/api/auth/status")
    assert r.json() == {"authenticated": False, "username": None, "role": None, "language": None, "vehicle_limit": None, "member_limit": None,
                        "no_accounts": True, "registration_open": True}


async def test_the_app_no_longer_has_setup_or_admin_routes(client) -> None:
    assert (await client.post("/api/auth/setup", json={"username": USER, "password": PASSWORD, "setup_code": SETUP_CODE})).status_code in (404, 405)
    await create_user(client)
    for url in ("/api/admin/users", "/api/admin/invites", "/api/settings/retention", "/api/vehicles/polling/config", "/api/server/polling"):
        assert (await client.get(url)).status_code == 404, url          # they only exist in the admin console


async def test_correct_login_returns_the_full_status(client) -> None:
    await create_user(client)
    assert (await client.get("/api/auth/status")).json() == {
        "authenticated": True, "username": USER, "role": "manager", "language": "es", "vehicle_limit": 2, "member_limit": 5,
        "no_accounts": False, "registration_open": True}


async def test_protected_routes_without_session_return_401(client) -> None:
    await create_user(client)
    client.cookies.clear()
    for method, path in [("GET", "/api/vehicles"), ("GET", "/api/account/status"),
                         ("GET", "/api/notifications/config"), ("GET", "/api/geofences"),
                         ("POST", "/api/vehicles/LSJA/commands")]:
        r = await client.request(method, path, json={"command": "bloquear"} if method == "POST" else None)
        assert r.status_code == 401, (method, path, r.status_code)


async def test_correct_login_and_access(client) -> None:
    await create_user(client)
    client.cookies.clear()
    r = await client.post("/api/auth/login", json={"username": "  JUAN ", "password": PASSWORD})
    assert r.status_code == 200
    assert (await client.get("/api/vehicles")).status_code == 200


async def test_wrong_login_does_not_tell_user_from_password(client) -> None:
    await create_user(client)
    bad_pass = await client.post("/api/auth/login", json={"username": USER, "password": "x" * 12})
    bad_user = await client.post("/api/auth/login", json={"username": "nadie", "password": PASSWORD})
    assert bad_pass.status_code == bad_user.status_code == 401
    assert bad_pass.json() == bad_user.json()


async def test_five_failures_lock_that_user_from_that_ip(client) -> None:
    await create_user(client)
    for _ in range(5):
        await client.post("/api/auth/login", json={"username": USER, "password": "x" * 12})
    locked = await client.post("/api/auth/login", json={"username": USER, "password": PASSWORD})
    assert locked.status_code == 429
    assert int(locked.headers["Retry-After"]) > 0
    # another user from the same IP is not locked by the pair (only the per-IP limit counts)
    other = await client.post("/api/auth/login", json={"username": "otro", "password": "x" * 12})
    assert other.status_code == 401


async def test_logout_invalidates_the_session(client) -> None:
    await create_user(client)
    token = client.cookies.get(auth.COOKIE_NAME)
    assert (await client.post("/api/auth/logout")).status_code == 200
    client.cookies.set(auth.COOKIE_NAME, token)                      # reuse the old cookie
    assert (await client.get("/api/vehicles")).status_code == 401


async def test_expired_session_is_rejected_and_deleted(client) -> None:
    await create_user(client)
    async with async_session_factory() as s:
        await s.execute(update(AuthSession).values(
            expires_at=datetime.datetime.utcnow() - datetime.timedelta(minutes=1)))
        await s.commit()
    assert (await client.get("/api/vehicles")).status_code == 401
    async with async_session_factory() as s:
        assert (await s.execute(select(AuthSession))).first() is None


async def test_only_the_token_hash_is_stored_in_the_db(client) -> None:
    await create_user(client)
    token = client.cookies.get(auth.COOKIE_NAME)
    async with async_session_factory() as s:
        (session,) = (await s.execute(select(AuthSession))).scalars().all()
    assert session.token_hash == auth.hash_token(token) and token not in session.token_hash


async def test_password_change_closes_the_other_sessions(client) -> None:
    await create_user(client)
    other = httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://testserver")
    async with other:
        assert (await other.post("/api/auth/login", json={"username": USER, "password": PASSWORD})).status_code == 200
        assert (await other.get("/api/vehicles")).status_code == 200

        new_password = "otra-contraseña-larga-2"
        bad = await client.post("/api/auth/password", json={"current_password": "x" * 12, "new_password": new_password})
        assert bad.status_code == 403
        ok = await client.post("/api/auth/password", json={"current_password": PASSWORD, "new_password": new_password})
        assert ok.status_code == 200

        assert (await client.get("/api/vehicles")).status_code == 200      # the current one stays
        assert (await other.get("/api/vehicles")).status_code == 401        # the other one is closed
    client.cookies.clear()
    assert (await client.post("/api/auth/login", json={"username": USER, "password": PASSWORD})).status_code == 401
    assert (await client.post("/api/auth/login", json={"username": USER, "password": new_password})).status_code == 200


# ───────────────────────── CSRF / origin ─────────────────────────
async def test_a_write_request_from_another_origin_is_rejected(client) -> None:
    await create_user(client)
    r = await client.post("/api/vehicles/discover", headers={"Origin": "https://malo.example"})
    assert r.status_code == 403
    r = await client.post("/api/auth/login", json={"username": USER, "password": PASSWORD},
                          headers={"Origin": "https://malo.example"})
    assert r.status_code == 403                                       # «login CSRF»


async def test_own_origin_and_sec_fetch_site(client) -> None:
    await create_user(client)
    ok = await client.post("/api/auth/logout", headers={"Origin": "http://testserver"})
    assert ok.status_code == 200
    cross = await client.post("/api/auth/logout", headers={"Sec-Fetch-Site": "cross-site"})
    assert cross.status_code == 403


async def test_extra_origins_allowed_by_configuration(client, monkeypatch) -> None:
    monkeypatch.setattr(settings, "ALLOWED_ORIGINS", "https://ebro.midominio.es")
    await create_user(client)
    r = await client.post("/api/auth/logout", headers={"Origin": "https://ebro.midominio.es"})
    assert r.status_code == 200


# ───────────────────────── public surface ─────────────────────────
# /api/auth/register is public on purpose: it needs a single-use invitation token issued by the administrator
PUBLIC = {"/api/auth/status", "/api/auth/login", "/api/auth/logout", "/api/auth/register",
          "/api/auth/passkeys/login/options", "/api/auth/passkeys/login/verify",
          # anonymous by design: other installations send their daily report; off (404) unless TELEMETRY_RECEIVER (docs/telemetry.md)
          "/api/telemetry/v1/report", "/api/catalog/online/signup"}
_METHODS = ("get", "post", "put", "patch", "delete")


def _api_routes() -> list[tuple[str, str]]:
    """(method, path) of the WHOLE API, read from the OpenAPI schema (FastAPI's public API).

    It used to walk `app.routes`, but newer FastAPI versions keep included routers as lazy
    objects and the list came out empty: the test passed without checking anything."""
    paths = app.openapi()["paths"]
    return [(m.upper(), p) for p, ops in paths.items() if p.startswith("/api") for m in ops if m in _METHODS]


async def test_no_api_route_is_left_unprotected(client) -> None:
    routes = _api_routes()
    # Guard against the empty pass: if the schema were misread, this fails instead of passing.
    assert len(routes) >= 40, f"Only {len(routes)} routes found; did the OpenAPI schema change?"
    assert ("POST", "/api/vehicles/{vin}/commands") in routes

    unprotected = []
    for method, route in routes:
        if route in PUBLIC:
            continue
        url = re.sub(r"\{[^}]+\}", "1", route)
        kwargs = {} if method in ("GET", "DELETE") else {"json": {}}
        r = await client.request(method, url, **kwargs)
        if r.status_code != 401:
            unprotected.append(f"{method} {route} -> {r.status_code}")
    assert not unprotected, f"/api routes that do not require a session: {unprotected}"


def test_routes_are_not_hidden_from_the_schema() -> None:
    """The previous test only sees what OpenAPI lists: nobody may take routes out of it."""
    api_dir = SRC / "api"
    hidden = [f.name for f in api_dir.glob("*.py") if "include_in_schema" in f.read_text(encoding="utf-8")]
    assert not hidden, f"Routes outside the OpenAPI schema in {hidden}: they cannot be checked"


async def test_interactive_docs_are_disabled(client) -> None:
    for path in ("/docs", "/redoc", "/openapi.json"):
        assert (await client.get(path)).status_code == 404


async def test_security_headers(client) -> None:
    r = await client.get("/api/auth/status")
    assert r.headers["x-content-type-options"] == "nosniff"
    assert r.headers["x-frame-options"] == "DENY"
    assert r.headers["cache-control"] == "no-store"
    # OpenStreetMap tiles require a Referer: with `same-origin`/`no-referrer` the map fills with «Access blocked»
    assert r.headers["referrer-policy"] == "strict-origin-when-cross-origin"


async def test_health_and_the_web_stay_public(client) -> None:
    assert (await client.get("/health")).status_code == 200
    assert (await client.get("/")).status_code == 200
    assert (await client.get("/manifest.json")).status_code == 200


# ───────────────────────── nothing sensitive in the responses ─────────────────────────
async def test_the_api_does_not_return_secrets(client, db) -> None:
    from app.db.models import EbroAccount, NotificationConfig
    async with async_session_factory() as s:
        s.add(EbroAccount(phone="600123456", password="pass-ebro", pin="4321", is_active=True))
        s.add(NotificationConfig(user_id=1, telegram_enabled=True, telegram_bot_token="123456789:AAH-token-muy-secreto",
                                 pushover_api_token="pushover-secreto-largo", webhook_url="https://h.example/x/abc"))
        await s.commit()
    await create_user(client)

    account = (await client.get("/api/account/status")).text
    assert "600123456" not in account and "pass-ebro" not in account and "4321" not in account

    notif = await client.get("/api/notifications/config")
    assert "AAH-token-muy-secreto" not in notif.text
    assert "pushover-secreto-largo" not in notif.text
    assert "/x/abc" not in notif.text
    assert notif.json()["telegram_bot_token_set"] is True


async def test_saving_the_masked_value_does_not_erase_the_secret(client, db) -> None:
    from app.db.models import NotificationConfig
    async with async_session_factory() as s:
        s.add(NotificationConfig(user_id=1, telegram_bot_token="123456789:AAH-token-muy-secreto"))
        await s.commit()
    await create_user(client)
    cfg = (await client.get("/api/notifications/config")).json()
    saved = await client.put("/api/notifications/config", json={"telegram_bot_token": cfg["telegram_bot_token"]})
    assert saved.status_code == 200
    async with async_session_factory() as s:
        row = (await s.execute(select(NotificationConfig))).scalar_one()
        assert row.telegram_bot_token == "123456789:AAH-token-muy-secreto"


# ───────────────────────── passwords ─────────────────────────
def test_password_hashing_and_verification() -> None:
    h = passwords.hash_password("una-clave-de-prueba")
    assert h.startswith("scrypt$") and "una-clave" not in h
    assert passwords.verify_password("una-clave-de-prueba", h)
    assert not passwords.verify_password("otra", h)
    assert h != passwords.hash_password("una-clave-de-prueba")          # different salt
    for broken in (None, "", "texto", "scrypt$1$2", "bcrypt$1$2$3$4$5"):
        assert passwords.verify_password("x", broken) is False


# ───────────────────────── WebSocket ─────────────────────────
@pytest.fixture
def ws_client():
    from app.db.models import Base
    from app.db.session import engine
    from tests.conftest import ensure_test_database

    ensure_test_database(engine)

    async def _reset():
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.drop_all)
            await conn.run_sync(Base.metadata.create_all)
    asyncio.run(_reset())
    with TestClient(app) as c:                      # starts the lifespan (poller included)
        yield c
    asyncio.run(engine.dispose())


def test_websocket_without_session_is_rejected(ws_client) -> None:
    with pytest.raises(WebSocketDisconnect) as exc:
        with ws_client.websocket_connect("/api/ws"):
            pass
    assert exc.value.code == 4401


def test_websocket_with_session_connects_and_from_another_origin_does_not(ws_client) -> None:
    import asyncio as _a

    from app.db.models import AppUser
    from app.db.session import async_session_factory as _f

    async def _mk():
        async with _f() as s:
            s.add(AppUser(id=1, username=USER, role="manager", password_hash=passwords.hash_password(PASSWORD), vehicle_limit=2, member_limit=5))
            await s.commit()
    _a.run(_mk())
    r = ws_client.post("/api/auth/login", json={"username": USER, "password": PASSWORD})
    assert r.status_code == 200
    with ws_client.websocket_connect("/api/ws") as ws:
        ws.send_text("ping")
        assert ws.receive_text() == "pong"
    with pytest.raises(WebSocketDisconnect) as exc:
        with ws_client.websocket_connect("/api/ws", headers={"Origin": "https://malo.example"}):
            pass
    assert exc.value.code == 4401
