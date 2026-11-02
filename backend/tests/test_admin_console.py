"""The admin console: a separate app on its own port. First administrator, sign-in, the two doors, manager accounts, invitations,
limits, deleting a space, and server settings. The administrator owns no cars and never sees a manager's data."""
from __future__ import annotations

import datetime

import httpx
import pytest
from sqlalchemy import select, update

from app.admin_app import admin_app
from app.api import routes_admin_auth, routes_auth
from app.config import settings
from app.db.models import AppUser, AuthSession, Invite
from app.db.session import async_session_factory
from app.main import app
from app.security import access, auth, passwords

SETUP_CODE = "test-setup-code"
ADMIN, ADMIN_PW = "juan", "una-contraseña-larga-1"
MANAGER, MANAGER_PW = "maria", "otra-contraseña-larga-2"


@pytest.fixture(autouse=True)
def _clean(monkeypatch, data_dir):
    monkeypatch.setattr(settings, "SETUP_CODE", SETUP_CODE)
    auth.reset_setup_code()
    for t in (routes_auth.ip_throttle, routes_auth.pair_throttle, routes_admin_auth.ip_throttle, routes_admin_auth.pair_throttle):
        t._fails.clear()
    yield
    auth.reset_setup_code()


class _Client(httpx.AsyncClient):
    """httpx refuses to enter a client twice; here a client that already made requests can still be used in `async with`."""

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        await self.aclose()


def console():
    return _Client(transport=httpx.ASGITransport(app=admin_app), base_url="http://testserver")


def the_app():
    return _Client(transport=httpx.ASGITransport(app=app), base_url="http://testserver")


@pytest.fixture
async def admin(db):
    """A console client signed in as the first administrator (created through the setup flow)."""
    c = console()
    r = await c.post("/api/auth/setup", json={"username": ADMIN, "password": ADMIN_PW, "setup_code": SETUP_CODE})
    assert r.status_code == 200, r.text
    yield c
    await c.aclose()


async def make_manager(admin, name=MANAGER, **kw):
    r = await admin.post("/api/admin/managers", json={"username": name, "password": MANAGER_PW, **kw})
    assert r.status_code == 200, r.text
    return r.json()


# ───────────────────────── first administrator ─────────────────────────
async def test_the_console_asks_for_the_first_administrator(db) -> None:
    async with console() as c:
        assert (await c.get("/api/auth/status")).json() == {"authenticated": False, "username": None, "setup_required": True}


async def test_setup_needs_the_code_and_a_strong_password_and_only_works_once(db) -> None:
    async with console() as c:
        bad = {"username": ADMIN, "password": ADMIN_PW}
        assert (await c.post("/api/auth/setup", json={**bad, "setup_code": "mal"})).status_code == 403
        assert (await c.post("/api/auth/setup", json={**bad, "password": "corta", "setup_code": SETUP_CODE})).status_code == 422
        assert (await c.post("/api/auth/setup", json={**bad, "username": "ab", "setup_code": SETUP_CODE})).status_code == 422
        r = await c.post("/api/auth/setup", json={**bad, "setup_code": SETUP_CODE})
        assert r.status_code == 200 and "evbro_admin_session" in r.headers["set-cookie"]
        assert (await c.get("/api/auth/status")).json() == {"authenticated": True, "username": ADMIN, "setup_required": False}
        assert (await c.post("/api/auth/setup", json={**bad, "username": "otro", "setup_code": SETUP_CODE})).status_code == 409


async def test_wrong_setup_codes_are_throttled(db) -> None:
    async with console() as c:
        for _ in range(8):
            await c.post("/api/auth/setup", json={"username": ADMIN, "password": ADMIN_PW, "setup_code": "mal"})
        r = await c.post("/api/auth/setup", json={"username": ADMIN, "password": ADMIN_PW, "setup_code": SETUP_CODE})
        assert r.status_code == 429


async def test_the_administrator_owns_nothing(admin) -> None:
    async with async_session_factory() as s:
        a = (await s.execute(select(AppUser).where(AppUser.role == "admin"))).scalar_one()
        assert (a.is_admin, a.vehicle_limit, a.member_limit) == (True, 0, 0)
        assert await access.within_vehicle_limit(s, a.id) is False          # an administrator can never own a car


# ───────────────────────── the two doors ─────────────────────────
async def test_an_admin_account_cannot_sign_in_to_the_app_and_a_session_of_one_door_is_worthless_at_the_other(admin) -> None:
    await make_manager(admin)
    async with the_app() as a:
        assert (await a.post("/api/auth/login", json={"username": ADMIN, "password": ADMIN_PW})).status_code == 401
        assert (await a.post("/api/auth/login", json={"username": MANAGER, "password": MANAGER_PW})).status_code == 200
        assert (await a.get("/api/vehicles")).status_code == 200
        # the manager's app cookie does nothing in the console
        cookie = a.cookies.get(auth.COOKIE_NAME)
    async with console() as c:
        c.cookies.set(auth.ADMIN_COOKIE_NAME, cookie)
        assert (await c.get("/api/admin/users")).status_code == 401
        c.cookies.clear()
        c.cookies.set(auth.COOKIE_NAME, cookie)
        assert (await c.get("/api/admin/users")).status_code == 401
    # and the administrator's console cookie does nothing in the app
    token = admin.cookies.get(auth.ADMIN_COOKIE_NAME)
    async with the_app() as a:
        a.cookies.set(auth.COOKIE_NAME, token)
        assert (await a.get("/api/vehicles")).status_code == 401


async def test_the_console_only_accepts_administrators(admin) -> None:
    await make_manager(admin)
    async with console() as c:
        assert (await c.post("/api/auth/login", json={"username": MANAGER, "password": MANAGER_PW})).status_code == 401
        assert (await c.post("/api/auth/login", json={"username": ADMIN, "password": "x" * 12})).status_code == 401
        assert (await c.post("/api/auth/login", json={"username": ADMIN, "password": ADMIN_PW})).status_code == 200


async def test_the_same_person_can_have_an_admin_and_a_manager_account_with_the_same_username_and_password(admin) -> None:
    async with the_app() as a:                                                          # manager named like the admin, same password
        r = await admin.post("/api/admin/managers", json={"username": ADMIN, "password": ADMIN_PW})
        assert r.status_code == 200
        assert (await a.post("/api/auth/login", json={"username": ADMIN, "password": ADMIN_PW})).status_code == 200
        assert (await a.get("/api/auth/status")).json()["role"] == "manager"
    async with console() as c:
        assert (await c.post("/api/auth/login", json={"username": ADMIN, "password": ADMIN_PW})).status_code == 200
        assert (await c.get("/api/admin/users")).status_code == 200
    # the name is still unique within a kind
    assert (await admin.post("/api/admin/managers", json={"username": ADMIN, "password": ADMIN_PW})).status_code == 409


async def test_the_admin_routes_do_not_exist_in_the_app_and_need_a_session_in_the_console(db) -> None:
    async with the_app() as a:
        for url in ("/api/admin/users", "/api/admin/invites", "/api/settings/retention", "/api/server/polling"):
            assert (await a.get(url)).status_code in (401, 404), url
    async with console() as c:
        for url in ("/api/admin/users", "/api/admin/invites", "/api/settings/retention", "/api/server/polling"):
            assert (await c.get(url)).status_code == 401, url
        for url in ("/api/vehicles", "/api/members", "/api/notifications/config"):
            assert (await c.get(url)).status_code == 404, url                          # the app's routes are not in the console


async def test_every_console_route_requires_the_admin_session(db) -> None:
    public = {"/api/auth/status", "/api/auth/setup", "/api/auth/login", "/api/auth/logout"}
    async with console() as c:
        for route in admin_app.routes:
            path = getattr(route, "path", "")
            if not path.startswith("/api/") or path in public:
                continue
            for method in getattr(route, "methods", set()) - {"HEAD", "OPTIONS"}:
                url = path.replace("{invite_id}", "1").replace("{user_id}", "1")
                r = await c.request(method, url, json={} if method in ("POST", "PUT", "PATCH") else None)
                assert r.status_code == 401, (method, path, r.status_code)


async def test_the_console_serves_its_page_with_strict_headers(db) -> None:
    async with console() as c:
        r = await c.get("/")
        assert r.status_code == 200
        assert "default-src 'self'" in r.headers["content-security-policy"] and r.headers["x-frame-options"] == "DENY"


# ───────────────────────── managers ─────────────────────────
async def test_the_administrator_creates_managers_with_default_or_chosen_limits(admin) -> None:
    m = await make_manager(admin)
    assert (m["role"], m["vehicle_limit"], m["member_limit"]) == ("manager", settings.DEFAULT_VEHICLE_LIMIT, settings.DEFAULT_MEMBER_LIMIT)
    m2 = await make_manager(admin, "beto", vehicle_limit=1, member_limit=0)
    assert (m2["vehicle_limit"], m2["member_limit"]) == (1, 0)
    assert (await admin.post("/api/admin/managers", json={"username": MANAGER, "password": MANAGER_PW})).status_code == 409
    assert (await admin.post("/api/admin/managers", json={"username": "x", "password": MANAGER_PW})).status_code == 422
    assert (await admin.post("/api/admin/managers", json={"username": "carla", "password": "corta"})).status_code == 422
    async with the_app() as a:
        assert (await a.post("/api/auth/login", json={"username": MANAGER, "password": MANAGER_PW})).status_code == 200


async def test_the_admin_sees_counts_and_health_but_never_the_cars(admin) -> None:
    await make_manager(admin)
    users = (await admin.get("/api/admin/users")).json()
    assert {u["username"] for u in users} == {ADMIN, MANAGER}
    row = next(u for u in users if u["username"] == MANAGER)
    assert row["vehicle_count"] == 0 and row["member_count"] == 0 and row["ebro_connected"] is False
    assert not ({"vin", "vehicles", "license_plate"} & set(row))


async def test_limits_expiry_deactivation_and_password_reset(admin) -> None:
    m = await make_manager(admin)
    d = (await admin.patch(f"/api/admin/users/{m['id']}", json={"vehicle_limit": 5, "member_limit": -1, "read_only": True, "expires_at": "2027-01-01T00:00:00"})).json()
    assert (d["vehicle_limit"], d["member_limit"], d["read_only"], d["expires_at"][:10]) == (5, -1, True, "2027-01-01")
    assert (await admin.patch(f"/api/admin/users/{m['id']}", json={"expires_at": None})).json()["expires_at"] is None
    for body in ({"vehicle_limit": -2}, {"member_limit": 1001}):
        assert (await admin.patch(f"/api/admin/users/{m['id']}", json=body)).status_code == 422
    async with the_app() as a:
        await a.post("/api/auth/login", json={"username": MANAGER, "password": MANAGER_PW})
        assert (await a.get("/api/vehicles")).status_code == 200
        await admin.patch(f"/api/admin/users/{m['id']}", json={"is_active": False})
        assert (await a.get("/api/vehicles")).status_code == 401                         # the session dies at once
        assert (await a.post("/api/auth/login", json={"username": MANAGER, "password": MANAGER_PW})).status_code == 401
        await admin.patch(f"/api/admin/users/{m['id']}", json={"is_active": True, "new_password": "una-clave-nueva-larga-3"})
        assert (await a.post("/api/auth/login", json={"username": MANAGER, "password": MANAGER_PW})).status_code == 401      # old password
        assert (await a.post("/api/auth/login", json={"username": MANAGER, "password": "una-clave-nueva-larga-3"})).status_code == 200
    assert (await admin.patch(f"/api/admin/users/{m['id']}", json={"new_password": "x"})).status_code == 422


async def test_an_expired_manager_cannot_sign_in_or_use_a_session(admin) -> None:
    m = await make_manager(admin)
    async with the_app() as a:
        await a.post("/api/auth/login", json={"username": MANAGER, "password": MANAGER_PW})
        await admin.patch(f"/api/admin/users/{m['id']}", json={"expires_at": "2020-01-01T00:00:00"})
        assert (await a.get("/api/vehicles")).status_code == 401
        assert (await a.post("/api/auth/login", json={"username": MANAGER, "password": MANAGER_PW})).status_code == 401


async def test_the_last_administrator_cannot_be_deactivated_and_members_are_not_managed_here(admin) -> None:
    me = (await admin.get("/api/admin/users")).json()[0]
    assert (await admin.patch(f"/api/admin/users/{me['id']}", json={"is_active": False})).status_code == 409
    assert (await admin.delete(f"/api/admin/users/{me['id']}?confirm={ADMIN}")).status_code == 404          # only managers are deleted
    assert (await admin.patch("/api/admin/users/999", json={"is_active": False})).status_code == 404
    async with async_session_factory() as s:
        s.add(AppUser(id=50, username="miembro", role="member", parent_id=999, password_hash="x"))
        await s.commit()
    assert (await admin.patch("/api/admin/users/50", json={"is_active": False})).status_code == 404          # a member belongs to its manager


async def test_deleting_a_managers_space_needs_confirmation(admin, tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(settings, "DATA_DIR", str(tmp_path))
    m = await make_manager(admin)
    assert (await admin.delete(f"/api/admin/users/{m['id']}")).status_code == 409
    assert (await admin.delete(f"/api/admin/users/{m['id']}?confirm=otro")).status_code == 409
    r = await admin.delete(f"/api/admin/users/{m['id']}?confirm={MANAGER.upper()}")
    assert r.status_code == 200 and r.json()["removed"] == {"vehicles": 0, "members": 0}
    async with async_session_factory() as s:
        assert await s.get(AppUser, m["id"]) is None


# ───────────────────────── invitations (registration is always open, with a token) ─────────────────────────
async def new_invite(admin, **body):
    r = await admin.post("/api/admin/invites", json=body)
    assert r.status_code == 200, r.text
    return r.json()


async def register(token, username=MANAGER, password=MANAGER_PW):
    c = the_app()
    r = await c.post("/api/auth/register", json={"username": username, "password": password, "invite_token": token, "accept_privacy": True})
    if r.status_code != 200:
        await c.aclose()
    return r, c


async def test_an_invitation_registers_one_manager_exactly_once(admin) -> None:
    inv = await new_invite(admin, note="Maria (beta)", vehicle_limit=1, member_limit=3)
    assert inv["token"].startswith("ebm_") and inv["state"] == "active"
    r, c = await register(inv["token"])
    assert r.status_code == 200
    async with c:
        s = (await c.get("/api/auth/status")).json()
        assert (s["role"], s["vehicle_limit"], s["member_limit"]) == ("manager", 1, 3)
    r2, _ = await register(inv["token"], username="otro-usuario")
    assert r2.status_code == 403
    listed = (await admin.get("/api/admin/invites")).json()
    assert listed[0]["state"] == "used" and listed[0]["used_by"] == MANAGER and "token" not in listed[0]


async def test_an_invitation_without_limits_uses_the_server_defaults(admin) -> None:
    r, c = await register((await new_invite(admin))["token"])
    async with c:
        s = (await c.get("/api/auth/status")).json()
    assert (s["vehicle_limit"], s["member_limit"]) == (settings.DEFAULT_VEHICLE_LIMIT, settings.DEFAULT_MEMBER_LIMIT) == (2, 5)


async def test_only_the_hash_of_the_token_is_stored(admin) -> None:
    token = (await new_invite(admin))["token"]
    async with async_session_factory() as s:
        (inv,) = (await s.execute(select(Invite))).scalars().all()
    assert token not in inv.token_hash and inv.token_hash == auth.hash_token(token)


async def test_expired_revoked_and_unknown_tokens_are_refused(admin) -> None:
    expired = await new_invite(admin, days=1)
    async with async_session_factory() as s:
        await s.execute(update(Invite).where(Invite.id == expired["id"]).values(expires_at=datetime.datetime.utcnow() - datetime.timedelta(minutes=1)))
        await s.commit()
    revoked = await new_invite(admin)
    assert (await admin.delete(f"/api/admin/invites/{revoked['id']}")).status_code == 200
    for token in (expired["token"], revoked["token"], "ebm_" + "x" * 32, ""):
        r, _ = await register(token)
        assert r.status_code == 403, token
    states = {i["id"]: i["state"] for i in (await admin.get("/api/admin/invites")).json()}
    assert states[expired["id"]] == "expired" and states[revoked["id"]] == "revoked"
    assert (await admin.delete(f"/api/admin/invites/{revoked['id']}")).status_code == 409
    assert (await admin.delete("/api/admin/invites/999")).status_code == 404


async def test_registration_validates_the_name_and_password_and_keeps_the_invitation_on_failure(admin) -> None:
    token = (await new_invite(admin))["token"]
    await make_manager(admin, "ocupado")
    for username, pw, code in (("ab", MANAGER_PW, 422), (MANAGER, "corta", 422), ("ocupado", MANAGER_PW, 409)):
        r, _ = await register(token, username=username, password=pw)
        assert r.status_code == code, (username, r.text)
    r, _ = await register(token)
    assert r.status_code == 200


async def test_registration_attempts_are_throttled_per_ip(db) -> None:
    codes = [(await register("ebm_" + str(i)))[0].status_code for i in range(12)]
    assert 429 in codes and codes[0] == 403


async def test_invitation_lifetime_defaults_and_limits(admin) -> None:
    inv = await new_invite(admin)
    days = (datetime.datetime.fromisoformat(inv["expires_at"]) - datetime.datetime.utcnow()).days
    assert days in (settings.INVITE_DEFAULT_DAYS - 1, settings.INVITE_DEFAULT_DAYS)
    for body in ({"days": 0}, {"days": 91}, {"note": "x" * 121}, {"vehicle_limit": -2}):
        assert (await admin.post("/api/admin/invites", json=body)).status_code == 422


# ───────────────────────── server settings (only here) ─────────────────────────
async def test_retention_and_polling_are_server_settings_of_the_console(admin) -> None:
    r = await admin.get("/api/settings/retention")
    assert r.status_code == 200 and r.json()["config"]["snapshots_days"] == 365
    assert (await admin.put("/api/settings/retention", json={"snapshots_days": 30})).json()["config"]["snapshots_days"] == 30
    assert (await admin.post("/api/settings/retention/run")).status_code == 200
    p = await admin.get("/api/server/polling")
    assert p.status_code == 200 and "poll_interval_parked_min" in p.json()


async def test_change_password_signs_out_the_other_sessions_but_not_this_one(admin) -> None:
    async with console() as other:
        assert (await other.post("/api/auth/login", json={"username": ADMIN, "password": ADMIN_PW})).status_code == 200
        r = await admin.post("/api/auth/password", json={"current_password": ADMIN_PW, "new_password": "una-clave-nueva-larga-9"})
        assert r.status_code == 200
        assert (await admin.get("/api/admin/users")).status_code == 200
        assert (await other.get("/api/admin/users")).status_code == 401
    assert (await admin.post("/api/auth/password", json={"current_password": "mal", "new_password": "una-clave-nueva-larga-9"})).status_code == 403


async def test_sessions_are_stored_hashed(admin) -> None:
    async with async_session_factory() as s:
        rows = (await s.execute(select(AuthSession.token_hash))).scalars().all()
    assert rows and admin.cookies.get(auth.ADMIN_COOKIE_NAME) not in rows and all(len(h) == 64 for h in rows)


async def test_the_login_throttle_of_the_console_is_independent_of_the_app(admin) -> None:
    async with console() as c:
        for _ in range(5):
            await c.post("/api/auth/login", json={"username": ADMIN, "password": "x" * 12})
        assert (await c.post("/api/auth/login", json={"username": ADMIN, "password": ADMIN_PW})).status_code == 429
    async with the_app() as a:
        assert (await a.post("/api/auth/login", json={"username": ADMIN, "password": "x" * 12})).status_code == 401      # not locked out there


async def test_the_console_cookie_follows_the_real_scheme_not_the_global_cookie_secure(db, monkeypatch) -> None:
    """With COOKIE_SECURE=true (the public HTTPS app) the console, which is served over HTTP on the LAN, must still set a usable cookie:
    a Secure cookie over HTTP is dropped by the browser and every login would bounce back to the sign-in screen."""
    monkeypatch.setattr(settings, "COOKIE_SECURE", True)
    async with console() as c:
        r = await c.post("/api/auth/setup", json={"username": ADMIN, "password": ADMIN_PW, "setup_code": SETUP_CODE})
        assert r.status_code == 200
        cookie = r.headers["set-cookie"].lower()
        assert "evbro_admin_session" in cookie and "secure" not in cookie and "httponly" in cookie and "samesite=lax" in cookie
        r = await c.put("/api/auth/language", json={"language": "en"})        # the language selector reloads the page in it
        assert r.status_code == 200
        cookies = r.headers.get_list("set-cookie")
        assert cookies and all("secure" not in v.lower() for v in cookies)
    await make_manager_directly()
    async with the_app() as a:
        r = await a.post("/api/auth/login", json={"username": MANAGER, "password": MANAGER_PW})
        assert "secure" in r.headers["set-cookie"].lower()                      # the public app keeps the Secure flag


async def make_manager_directly():
    async with async_session_factory() as s:
        s.add(AppUser(username=MANAGER, role="manager", password_hash=passwords.hash_password(MANAGER_PW), vehicle_limit=2, member_limit=5))
        await s.commit()


# ───────────────────────── statistics ─────────────────────────
async def test_the_statistics_count_accounts_cars_usage_and_data_without_leaking_anything_private(admin, tmp_path, monkeypatch) -> None:
    from app.db.models import CommandAudit, Drive, EbroAccount, Expense, NotificationLog, Vehicle
    monkeypatch.setattr(settings, "DATA_DIR", str(tmp_path))
    (tmp_path / "x.bin").write_bytes(b"0" * 1000)
    ana = await make_manager(admin, "ana")
    beto = await make_manager(admin, "beto")
    now = datetime.datetime.utcnow()
    async with async_session_factory() as s:
        s.add_all([AppUser(id=70, username="miembro-secreto", role="member", parent_id=ana["id"], password_hash="x", read_only=True),
                   AppUser(id=71, username="otro-miembro", role="member", parent_id=ana["id"], password_hash="x", is_active=False)])
        s.add_all([Vehicle(owner_id=ana["id"], vin="LSJSECRETO00000001", name="Coche secreto de Ana", is_manual=False),
                   Vehicle(owner_id=ana["id"], vin="MANSECRETO0000002", name="Manual de Ana", is_manual=True),
                   Vehicle(owner_id=beto["id"], vin="LSJSECRETO00000003", name="Coche de Beto", is_manual=False)])
        s.add(EbroAccount(user_id=ana["id"], phone="600111222", password="x", is_active=True))
        await s.flush()
        s.add_all([CommandAudit(vin="LSJSECRETO00000001", command="lock", success=True, timestamp=now - datetime.timedelta(hours=2)),
                   CommandAudit(vin="LSJSECRETO00000001", command="unlock", success=False, timestamp=now - datetime.timedelta(days=3)),
                   CommandAudit(vin="LSJSECRETO00000003", command="lock", success=True, timestamp=now - datetime.timedelta(days=40)),
                   NotificationLog(user_id=ana["id"], category="security", title="t", message="m", channel="webpush", success=False, timestamp=now),
                   Drive(vin="LSJSECRETO00000001", start_time=now - datetime.timedelta(days=1)),
                   Expense(vin="LSJSECRETO00000001", date=now, category="tax", title="x", amount=1)])
        await s.commit()
    await new_invite(admin)
    d = (await admin.get("/api/admin/stats")).json()
    assert d["accounts"]["managers"] == 2 and d["accounts"]["members"] == 2 and d["accounts"]["total"] == 4 and d["accounts"]["admins"] == 1
    assert d["accounts"]["inactive"] == 1 and d["accounts"]["read_only"] == 1 and d["accounts"]["new_30d"] == 4
    assert d["vehicles"] == {"total": 3, "connected": 2, "manual": 1, "ebro_accounts": 1}
    assert d["invitations"] == {"active": 1, "used": 0, "expired": 0, "revoked": 0}
    assert (d["usage"]["commands_24h"], d["usage"]["commands_7d"], d["usage"]["commands_failed_7d"]) == (1, 2, 1)
    assert d["usage"]["notifications_24h"] == 1 and d["usage"]["notifications_failed_24h"] == 1 and d["usage"]["trips_7d"] == 1
    assert d["data"]["trips"] == 1 and d["data"]["expenses"] == 1 and d["storage"]["files_bytes"] >= 1000
    assert d["storage"]["database_bytes"] is None or d["storage"]["database_bytes"] > 0
    assert sum(w["new_accounts"] for w in d["growth_weekly"]) == 4 and len(d["growth_weekly"]) == 8
    row = {m["username"]: m for m in d["managers"]}
    assert (row["ana"]["vehicles"], row["ana"]["members"], row["ana"]["commands_7d"]) == (2, 2, 2) and (row["beto"]["vehicles"], row["beto"]["commands_7d"]) == (1, 0)
    blob = str(d)
    for private in ("LSJSECRETO", "MANSECRETO", "Coche secreto", "Manual de Ana", "miembro-secreto", "otro-miembro", "600111222"):
        assert private not in blob, private


async def test_the_statistics_work_on_an_empty_server(admin) -> None:
    d = (await admin.get("/api/admin/stats")).json()
    assert d["accounts"]["total"] == 0 and d["vehicles"]["total"] == 0 and d["managers"] == [] and d["data"]["oldest_snapshot"] is None
