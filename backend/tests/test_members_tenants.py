"""Member accounts, links between members and cars, limits, manual cars, the admin's blind spot and deleting a manager's space."""
from __future__ import annotations

import datetime

import httpx
import pytest
from sqlalchemy import func, select

from app.admin_app import admin_app
from app.config import settings
from app.db.models import AppUser, Drive, Expense, Geofence, PushSubscription, Vehicle, VehiclePart
from app.db.session import async_session_factory
from app.main import app
from app.security import access, auth, passwords

PW = "una-contraseña-larga-1"
VIN_A, VIN_B = "LSJAAAAAAAAAAAAAA", "LSJBBBBBBBBBBBBBB"


class _Req:
    client = type("C", (), {"host": "127.0.0.1"})()
    headers = {"user-agent": "t"}


@pytest.fixture
async def world(db, data_dir):
    h = passwords.hash_password(PW)
    async with async_session_factory() as s:
        s.add_all([
            AppUser(id=1, username="admin", role="admin", is_admin=True, password_hash=h, vehicle_limit=0, member_limit=0),
            AppUser(id=2, username="ana", role="manager", password_hash=h, vehicle_limit=2, member_limit=2),
            AppUser(id=3, username="beto", role="manager", password_hash=h, vehicle_limit=1, member_limit=0),
        ])
        s.add_all([Vehicle(owner_id=2, vin=VIN_A, name="Coche de Ana"), Vehicle(owner_id=3, vin=VIN_B, name="Coche de Beto")])
        await s.commit()


async def client_for(user_id):
    """A client for the door that account uses: the admin console for the administrator, the app for everybody else."""
    async with async_session_factory() as s:
        user = await s.get(AppUser, user_id)
        token = await auth.create_session(s, user, _Req())
    is_admin = user.role == "admin"
    c = httpx.AsyncClient(transport=httpx.ASGITransport(app=admin_app if is_admin else app), base_url="http://testserver")
    c.cookies.set(auth.ADMIN_COOKIE_NAME if is_admin else auth.COOKIE_NAME, token)
    return c


@pytest.fixture
async def ana(world):
    c = await client_for(2)
    yield c
    await c.aclose()


@pytest.fixture
async def beto(world):
    c = await client_for(3)
    yield c
    await c.aclose()


@pytest.fixture
async def admin(world):
    c = await client_for(1)
    yield c
    await c.aclose()


async def new_member(ana, name="lucia", **kw):
    r = await ana.post("/api/members", json={"username": name, "password": PW, **kw})
    assert r.status_code == 200, r.text
    return r.json()


# ───────────────────────── members ─────────────────────────
async def test_a_manager_creates_lists_updates_and_deletes_members(ana) -> None:
    m = await new_member(ana, read_only=True)
    assert m["username"] == "lucia" and m["read_only"] is True and m["access"] == []
    assert [x["id"] for x in (await ana.get("/api/members")).json()] == [m["id"]]
    r = await ana.patch(f"/api/members/{m['id']}", json={"read_only": False, "new_password": "una-clave-nueva-larga-3"})
    assert r.status_code == 200 and r.json()["read_only"] is False
    assert (await ana.delete(f"/api/members/{m['id']}")).status_code == 200
    assert (await ana.get("/api/members")).json() == []


async def test_member_limit_username_and_password_rules(ana, beto) -> None:
    await new_member(ana, "lucia")
    assert (await ana.post("/api/members", json={"username": "lucia", "password": PW})).status_code == 409       # taken
    assert (await ana.post("/api/members", json={"username": "ab", "password": PW})).status_code == 422
    assert (await ana.post("/api/members", json={"username": "pablo", "password": "corta"})).status_code == 422
    await new_member(ana, "pablo")
    assert (await ana.post("/api/members", json={"username": "tercero", "password": PW})).status_code == 409     # limit 2
    assert (await beto.post("/api/members", json={"username": "nadie", "password": PW})).status_code == 409      # limit 0


async def test_a_manager_cannot_touch_another_managers_members(ana, beto) -> None:
    m = await new_member(ana)
    assert (await beto.patch(f"/api/members/{m['id']}", json={"is_active": False})).status_code == 404
    assert (await beto.delete(f"/api/members/{m['id']}")).status_code == 404
    assert (await beto.get("/api/members")).json() == []


async def test_a_member_cannot_create_members_cars_or_connect_an_ebro_account(ana) -> None:
    await new_member(ana)
    member = httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://testserver")
    try:
        assert (await member.post("/api/auth/login", json={"username": "lucia", "password": PW})).status_code == 200
        assert (await member.post("/api/members", json={"username": "otro", "password": PW})).status_code == 403
        assert (await member.post("/api/vehicles", json={"name": "Mi coche"})).status_code == 403
        assert (await member.post("/api/vehicles/discover")).status_code == 403
        assert (await member.post("/api/account/login", json={"phone": "600", "password": "x"})).status_code == 403
        assert (await member.get("/api/admin/users")).status_code == 404       # the admin routes are not in the app
    finally:
        await member.aclose()


# ───────────────────────── links ─────────────────────────
async def test_links_give_a_member_a_car_with_a_level_and_can_be_changed_or_removed(ana) -> None:
    m = await new_member(ana)
    mc = httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://testserver")
    try:
        await mc.post("/api/auth/login", json={"username": "lucia", "password": PW})
        assert (await mc.get("/api/vehicles")).json() == []
        r = await ana.put(f"/api/vehicles/{VIN_A}/access/{m['id']}", json={"level": "view"})
        assert r.status_code == 200 and r.json()["level"] == "view"
        assert [(v["vin"], v["access_level"]) for v in (await mc.get("/api/vehicles")).json()] == [(VIN_A, "view")]
        await ana.put(f"/api/vehicles/{VIN_A}/access/{m['id']}", json={"level": "control"})
        assert (await mc.get("/api/vehicles")).json()[0]["access_level"] == "control"
        assert [(x["username"], x["level"]) for x in (await ana.get(f"/api/vehicles/{VIN_A}/access")).json()] == [("lucia", "control")]
        assert (await ana.get("/api/members")).json()[0]["access"] == [{"vin": VIN_A, "name": "Coche de Ana", "level": "control"}]
        assert (await ana.delete(f"/api/vehicles/{VIN_A}/access/{m['id']}")).status_code == 200
        assert (await mc.get("/api/vehicles")).json() == []
        assert (await mc.get(f"/api/vehicles/{VIN_A}/expenses")).status_code == 404
    finally:
        await mc.aclose()


async def test_links_never_cross_manager_spaces_and_only_owners_manage_them(ana, beto, admin) -> None:
    m = await new_member(ana)
    assert (await ana.put(f"/api/vehicles/{VIN_A}/access/{m['id']}", json={"level": "owner"})).status_code == 422
    assert (await ana.put(f"/api/vehicles/{VIN_A}/access/{m['id']}", json={"level": "nope"})).status_code == 422
    # Beto cannot link Ana's member to his own car, nor touch Ana's car
    assert (await beto.put(f"/api/vehicles/{VIN_B}/access/{m['id']}", json={"level": "view"})).status_code == 404
    assert (await beto.put(f"/api/vehicles/{VIN_A}/access/{m['id']}", json={"level": "view"})).status_code == 404
    # a manager cannot be a link target, nor can the owner link themselves
    assert (await ana.put(f"/api/vehicles/{VIN_A}/access/3", json={"level": "view"})).status_code == 404
    assert (await ana.put(f"/api/vehicles/{VIN_A}/access/2", json={"level": "view"})).status_code == 404
    async with async_session_factory() as s:
        assert (await s.execute(select(func.count()).select_from(__import__("app.db.models", fromlist=["DeviceAccess"]).DeviceAccess))).scalar_one() == 0


async def test_a_member_with_edit_can_work_on_expenses_and_a_member_with_view_cannot(ana) -> None:
    m = await new_member(ana)
    mc = httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://testserver")
    try:
        await mc.post("/api/auth/login", json={"username": "lucia", "password": PW})
        body = {"category": "tax", "title": "IVTM", "amount": 80}
        await ana.put(f"/api/vehicles/{VIN_A}/access/{m['id']}", json={"level": "view"})
        assert (await mc.post(f"/api/vehicles/{VIN_A}/expenses", json=body)).status_code == 403
        await ana.put(f"/api/vehicles/{VIN_A}/access/{m['id']}", json={"level": "edit"})
        assert (await mc.post(f"/api/vehicles/{VIN_A}/expenses", json=body)).status_code == 200
        assert (await mc.post(f"/api/vehicles/{VIN_A}/refuels", json={"liters": 20, "total_cost": 30})).status_code == 200
        assert (await mc.post(f"/api/vehicles/{VIN_A}/commands", json={"command": "x"})).status_code == 403
    finally:
        await mc.aclose()


async def test_deactivating_a_manager_freezes_their_members(admin, ana) -> None:
    await new_member(ana)
    mc = httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://testserver")
    try:
        assert (await mc.post("/api/auth/login", json={"username": "lucia", "password": PW})).status_code == 200
        assert (await mc.get("/api/vehicles")).status_code == 200
        assert (await admin.patch("/api/admin/users/2", json={"is_active": False})).status_code == 200
        assert (await mc.get("/api/vehicles")).status_code == 401                         # the member lives and dies with the manager
        assert (await ana.get("/api/vehicles")).status_code == 401
        assert (await mc.post("/api/auth/login", json={"username": "lucia", "password": PW})).status_code == 401
    finally:
        await mc.aclose()


# ───────────────────────── cars ─────────────────────────
async def test_manual_cars_respect_the_vehicle_limit_and_get_a_generated_vin(ana, beto) -> None:
    r = await beto.post("/api/vehicles", json={"name": "Mi Ebro"})
    assert r.status_code == 409                                                           # Beto already owns 1 of 1
    r = await ana.post("/api/vehicles", json={"name": "Mi Ebro", "model": "S900 PHEV"})
    assert r.status_code == 200 and r.json()["is_manual"] and r.json()["vin"].startswith("MAN") and r.json()["access_level"] == "owner"
    assert (await ana.post("/api/vehicles", json={"name": "Otro"})).status_code == 409    # Ana: 2 of 2


async def test_the_vehicle_limit_helper(world) -> None:
    async with async_session_factory() as s:
        assert await access.within_vehicle_limit(s, 1) is False         # the administrator can never own a car
        assert await access.within_vehicle_limit(s, 2) is True          # 1 of 2
        assert await access.within_vehicle_limit(s, 3) is False         # 1 of 1
        assert await access.within_vehicle_limit(s, 999) is False


async def test_only_the_owner_can_delete_a_car_and_everything_goes_with_it(ana, beto) -> None:
    async with async_session_factory() as s:
        s.add_all([Expense(vin=VIN_A, date=datetime.datetime(2026, 1, 1), category="tax", title="x", amount=1), VehiclePart(vin=VIN_A, category="tyres", name="n"),
                   Drive(vin=VIN_A, start_time=datetime.datetime(2026, 1, 1))])
        await s.commit()
    assert (await beto.delete(f"/api/vehicles/{VIN_A}")).status_code == 404
    assert (await ana.delete(f"/api/vehicles/{VIN_A}")).status_code == 200
    async with async_session_factory() as s:
        for model in (Vehicle, Expense, VehiclePart, Drive):
            assert (await s.execute(select(func.count()).select_from(model).where(model.vin == VIN_A))).scalar_one() == 0
        assert (await s.execute(select(func.count()).select_from(Vehicle).where(Vehicle.vin == VIN_B))).scalar_one() == 1     # Beto's is untouched


# ───────────────────────── geofences and push per space ─────────────────────────
async def test_geofences_are_per_manager_space(ana, beto) -> None:
    body = {"name": "Casa", "latitude": 37.0, "longitude": -6.0, "radius_meters": 100}
    gid = (await ana.post("/api/geofences", json=body)).json()["id"]
    assert [g["name"] for g in (await ana.get("/api/geofences")).json()] == ["Casa"]
    assert (await beto.get("/api/geofences")).json() == []
    assert (await beto.put(f"/api/geofences/{gid}", json={**body, "name": "Mia"})).status_code == 404
    assert (await beto.delete(f"/api/geofences/{gid}")).status_code == 404
    m = await new_member(ana)
    mc = httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://testserver")
    try:
        await mc.post("/api/auth/login", json={"username": "lucia", "password": PW})
        assert [g["name"] for g in (await mc.get("/api/geofences")).json()] == ["Casa"]               # members see their manager's places
        assert (await mc.post("/api/geofences", json=body)).status_code == 403                         # but cannot change them
        assert m["id"]
    finally:
        await mc.aclose()


async def test_geofence_lookup_only_uses_the_cars_owner_space(world) -> None:
    from app.services.geofence import find_geofence
    async with async_session_factory() as s:
        s.add(Geofence(owner_id=2, name="De Ana", latitude=1.0, longitude=1.0, radius_meters=100))
        await s.commit()
    async with async_session_factory() as s:
        assert (await find_geofence(s, 1.0, 1.0, owner_id=2)).name == "De Ana"
        assert await find_geofence(s, 1.0, 1.0, owner_id=3) is None
        assert await find_geofence(s, 1.0, 1.0, owner_id=None) is None


async def test_push_devices_belong_to_the_account_that_subscribed(ana, beto) -> None:
    body = {"endpoint": "https://push.example/abc", "keys": {"p256dh": "k", "auth": "a"}, "label": "Chrome"}
    sid = (await ana.post("/api/notifications/push/subscribe", json=body)).json()["id"]
    assert [d["id"] for d in (await ana.get("/api/notifications/push/devices")).json()] == [sid]
    assert (await beto.get("/api/notifications/push/devices")).json() == []
    assert (await beto.delete(f"/api/notifications/push/devices/{sid}")).status_code == 404
    assert (await ana.delete(f"/api/notifications/push/devices/{sid}")).status_code == 200
    async with async_session_factory() as s:
        assert (await s.execute(select(func.count()).select_from(PushSubscription))).scalar_one() == 0


# ───────────────────────── the admin ─────────────────────────
async def test_the_admin_sees_counts_and_health_but_never_the_cars(admin, ana) -> None:
    await new_member(ana)
    users = (await admin.get("/api/admin/users")).json()
    assert {u["username"] for u in users} == {"admin", "ana", "beto"}                     # members are not listed
    ana_row = next(u for u in users if u["username"] == "ana")
    assert ana_row["vehicle_count"] == 1 and ana_row["member_count"] == 1 and ana_row["ebro_connected"] is False
    blob = str(users)
    for secret in (VIN_A, VIN_B, "Coche de Ana", "lucia"):
        assert secret not in blob
    assert (await admin.get("/api/vehicles")).status_code == 404                           # the car routes are not even in the console
    assert (await admin.patch(f"/api/admin/users/{(await ana.get('/api/members')).json()[0]['id']}", json={"is_active": False})).status_code == 404


async def test_the_admin_can_change_limits_and_expiry(admin) -> None:
    r = await admin.patch("/api/admin/users/3", json={"vehicle_limit": 5, "member_limit": -1, "read_only": True, "expires_at": "2027-01-01T00:00:00"})
    d = r.json()
    assert (d["vehicle_limit"], d["member_limit"], d["read_only"], d["expires_at"][:10]) == (5, -1, True, "2027-01-01")
    assert (await admin.patch("/api/admin/users/3", json={"expires_at": None})).json()["expires_at"] is None
    for body in ({"vehicle_limit": -2}, {"member_limit": 1001}):
        assert (await admin.patch("/api/admin/users/3", json=body)).status_code == 422


async def test_an_expired_account_cannot_use_its_session_or_log_in(admin, ana) -> None:
    await admin.patch("/api/admin/users/2", json={"expires_at": "2020-01-01T00:00:00"})
    assert (await ana.get("/api/vehicles")).status_code == 401
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://testserver") as c:
        assert (await c.post("/api/auth/login", json={"username": "ana", "password": PW})).status_code == 401


async def test_deleting_a_managers_space_needs_confirmation_and_removes_everything(admin, ana, beto, tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(settings, "DATA_DIR", str(tmp_path))
    m = await new_member(ana)
    async with async_session_factory() as s:
        s.add_all([Expense(vin=VIN_A, date=datetime.datetime(2026, 1, 1), category="tax", title="x", amount=1), Geofence(owner_id=2, name="Casa", latitude=1, longitude=1)])
        await s.commit()
    assert (await admin.delete("/api/admin/users/2")).status_code == 409                       # no confirmation
    assert (await admin.delete("/api/admin/users/2?confirm=otro")).status_code == 409
    assert (await admin.delete("/api/admin/users/1?confirm=admin")).status_code == 404         # the admin account itself cannot be deleted
    r = await admin.delete("/api/admin/users/2?confirm=ANA")
    assert r.status_code == 200 and r.json()["removed"] == {"vehicles": 1, "members": 1}
    async with async_session_factory() as s:
        assert await s.get(AppUser, 2) is None and await s.get(AppUser, m["id"]) is None
        for model in (Vehicle, Expense):
            assert (await s.execute(select(func.count()).select_from(model).where(model.vin == VIN_A))).scalar_one() == 0
        assert (await s.execute(select(func.count()).select_from(Geofence).where(Geofence.owner_id == 2))).scalar_one() == 0
        assert (await s.execute(select(func.count()).select_from(Vehicle).where(Vehicle.vin == VIN_B))).scalar_one() == 1     # Beto untouched
    assert (await beto.get("/api/vehicles")).status_code == 200


async def test_an_invitation_creates_a_manager_with_the_limits_of_the_invitation(admin) -> None:
    inv = (await admin.post("/api/admin/invites", json={"vehicle_limit": 1, "member_limit": 3})).json()
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://testserver") as c:
        r = await c.post("/api/auth/register", json={"username": "nuevo", "password": PW, "invite_token": inv["token"], "accept_privacy": True})
        assert r.status_code == 200
    async with async_session_factory() as s:
        u = (await s.execute(select(AppUser).where(AppUser.username == "nuevo"))).scalar_one()
    assert (u.role, u.vehicle_limit, u.member_limit) == ("manager", 1, 3)
    inv2 = (await admin.post("/api/admin/invites", json={})).json()
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://testserver") as c:
        await c.post("/api/auth/register", json={"username": "otro", "password": PW, "invite_token": inv2["token"], "accept_privacy": True})
    async with async_session_factory() as s:
        u2 = (await s.execute(select(AppUser).where(AppUser.username == "otro"))).scalar_one()
    assert (u2.vehicle_limit, u2.member_limit) == (settings.DEFAULT_VEHICLE_LIMIT, settings.DEFAULT_MEMBER_LIMIT) == (2, 5)
