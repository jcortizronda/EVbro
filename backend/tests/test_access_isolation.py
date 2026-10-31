"""Isolation between accounts: every route is classified, and the access matrix holds for every kind of account.

The matrix calls the central `authorize` rule directly for every route of the API (no side effects), and a few end-to-end tests prove
that the rule is really wired into the router.
"""
from __future__ import annotations

import datetime
import re
from types import SimpleNamespace

import httpx
import pytest
from fastapi import HTTPException
from sqlalchemy import select
from starlette.requests import Request

from app.db.models import (
    AppUser,
    ChargingSession,
    ClimateSchedule,
    DeviceAccess,
    Drive,
    Expense,
    Geofence,
    Journey,
    MaintenanceItem,
    MaintenanceLog,
    PasskeyCredential,
    PushSubscription,
    Refuel,
    Vehicle,
    VehicleDocument,
    VehiclePart,
)
from app.db.session import async_session_factory
from app.main import app
from app.security import access, auth, passwords

VIN_A, VIN_B = "LSJAAAAAAAAAAAAAA", "LSJBBBBBBBBBBBBBB"
PW = "una-contraseña-larga-1"
PUBLIC = {"/api/auth/status", "/api/auth/login", "/api/auth/logout", "/api/auth/register", "/api/auth/password",
          "/api/auth/passkeys/login/options", "/api/auth/passkeys/login/verify",
          "/api/catalog/online/export", "/api/catalog/online/propose"}      # server to server, authenticated by a catalogue key
METHODS = ("get", "post", "put", "patch", "delete")


class _Req:
    client = type("C", (), {"host": "127.0.0.1"})()
    headers = {"user-agent": "t"}


def api_routes():
    paths = app.openapi()["paths"]
    return sorted((m.upper(), p) for p, ops in paths.items() if p.startswith("/api") and p not in PUBLIC for m in ops if m in METHODS)


@pytest.fixture
async def world(db):
    """admin (id 1, owns nothing), manager A (id 2, owns VIN_A, member M linked), manager B (id 3, owns VIN_B), member M (id 4, parent A)."""
    h = passwords.hash_password(PW)
    async with async_session_factory() as s:
        s.add_all([
            AppUser(id=1, username="admin", role="admin", is_admin=True, password_hash=h, vehicle_limit=0, member_limit=0),
            AppUser(id=2, username="ana", role="manager", password_hash=h, vehicle_limit=2, member_limit=2),
            AppUser(id=3, username="beto", role="manager", password_hash=h, vehicle_limit=2, member_limit=2),
            AppUser(id=4, username="miembro", role="member", parent_id=2, password_hash=h),
        ])
        s.add_all([Vehicle(owner_id=2, vin=VIN_A, name="Coche de Ana", odometer=1000.0), Vehicle(owner_id=3, vin=VIN_B, name="Coche de Beto")])
        await s.flush()
        drive = Drive(vin=VIN_A, start_time=datetime.datetime(2026, 1, 1), end_time=datetime.datetime(2026, 1, 1, 1))
        charge = ChargingSession(vin=VIN_A, start_time=datetime.datetime(2026, 1, 2), end_time=datetime.datetime(2026, 1, 2, 3))
        refuel = Refuel(vin=VIN_A, timestamp=datetime.datetime(2026, 1, 3), liters=10)
        expense = Expense(vin=VIN_A, date=datetime.datetime(2026, 1, 4), category="tax", title="IVTM", amount=50)
        part = VehiclePart(vin=VIN_A, category="engine_oil", name="Aceite")
        item = MaintenanceItem(vin=VIN_A, name="Tarea", category="General", origin="custom")
        gf = Geofence(owner_id=2, name="Casa de Ana", latitude=1.0, longitude=1.0)
        sub = PushSubscription(endpoint_hash="h" * 64, user_id=2, subscription={"endpoint": "https://x", "keys": {}})
        doc = VehicleDocument(vin=VIN_A, kind="purchase_invoice", filename="a" * 32 + ".pdf", original_name="factura.pdf",
                              content_type="application/pdf", size=10)
        passkey = PasskeyCredential(user_id=2, credential_id="cred-ana", public_key=b"k", sign_count=0)
        sched = ClimateSchedule(vin=VIN_A, time_local="07:30", days="0,1,2,3,4", temperature=22.0)
        journey = Journey(vin=VIN_A, name="Viaje", start_time=datetime.datetime(2026, 1, 1), end_time=datetime.datetime(2026, 1, 6))
        s.add_all([drive, charge, refuel, expense, part, item, gf, sub, doc, passkey, sched, journey])
        await s.flush()
        log = MaintenanceLog(item_id=item.id, service_date=datetime.datetime(2026, 1, 5), odometer=900.0)
        s.add(log)
        await s.commit()
        ids = {"vin": VIN_A, "drive_id": drive.id, "charge_id": charge.id, "refuel_id": refuel.id, "expense_id": expense.id, "part_id": part.id,
               "item_id": item.id, "log_id": log.id, "document_id": doc.id, "geofence_id": gf.id, "device_id": sub.id, "passkey_id": passkey.id,
               "schedule_id": sched.id, "journey_id": journey.id, "member_id": 4, "user_id": 4, "invite_id": 1}
    return ids


async def load(user_id):
    async with async_session_factory() as s:
        return await s.get(AppUser, user_id)


async def link(level, read_only=False):
    async with async_session_factory() as s:
        for row in (await s.execute(select(DeviceAccess))).scalars():
            await s.delete(row)
        await s.flush()                                  # the delete must reach the database before the insert (unique user+car)
        member = await s.get(AppUser, 4)
        member.read_only = read_only
        if level:
            s.add(DeviceAccess(user_id=4, vin=VIN_A, level=level, granted_by=2))
        await s.commit()


async def verdict(method, template, params, user_id):
    """'ok' if the central rule lets the account through, else the HTTP status it answers."""
    path = re.sub(r"\{([^}]+)\}", lambda m: str(params[m.group(1)]), template)
    scope = {"type": "http", "method": method, "path": path, "headers": [], "query_string": b"", "path_params": {k: str(params[k]) for k in re.findall(r"\{([^}]+)\}", template)},
             "route": SimpleNamespace(path=template)}
    user = await load(user_id)
    async with async_session_factory() as s:
        try:
            await access.authorize(Request(scope), user, s)
            return "ok"
        except HTTPException as exc:
            return exc.status_code


# ───────────────────────── classification ─────────────────────────
def test_every_route_is_classified_or_about_a_known_resource() -> None:
    """A new route must be added to ACCOUNT_RULES, or take a vehicle resource parameter, or this test fails (and at runtime it is denied)."""
    unclassified = []
    for method, path in api_routes():
        params = set(re.findall(r"\{([^}]+)\}", path))
        if (method, path) in access.ACCOUNT_RULES:
            continue
        if params & set(access.VEHICLE_RESOURCE_PARAMS):
            continue
        unclassified.append(f"{method} {path}")
    assert not unclassified, f"Rutas sin clasificar en security/access.py: {unclassified}"


def test_the_rule_tables_do_not_mention_routes_that_do_not_exist() -> None:
    existing = set(api_routes())
    stale = [k for k in list(access.ACCOUNT_RULES) + list(access.VEHICLE_LEVELS) if k not in existing]
    assert not stale, f"Reglas de rutas que ya no existen: {stale}"


def test_every_path_parameter_has_a_known_meaning() -> None:
    known = set(access.VEHICLE_RESOURCE_PARAMS) | set(access.OTHER_RESOURCE_PARAMS)
    unknown = sorted({p for _, path in api_routes() for p in re.findall(r"\{([^}]+)\}", path)} - known)
    assert not unknown, f"Parámetros de ruta sin significado de seguridad: {unknown}"


def test_commands_and_the_car_sheet_need_the_right_level() -> None:
    need = access.required_level
    assert need("GET", "/api/vehicles/{vin}/latest") == "view"
    assert need("POST", "/api/vehicles/{vin}/expenses") == "edit"
    assert need("POST", "/api/vehicles/{vin}/commands") == "control"
    assert need("PUT", "/api/vehicles/{vin}/charge/schedule") == "control"
    assert need("PUT", "/api/vehicles/{vin}") == "owner"
    assert need("PUT", "/api/vehicles/{vin}/access/{user_id}") == "owner"


# ───────────────────────── the matrix ─────────────────────────
def vehicle_routes():
    return [(m, p) for m, p in api_routes() if (m, p) not in access.ACCOUNT_RULES and set(re.findall(r"\{([^}]+)\}", p)) & set(access.VEHICLE_RESOURCE_PARAMS)]


async def test_the_owner_can_do_everything_on_their_car(world) -> None:
    bad = [(m, p) for m, p in vehicle_routes() if await verdict(m, p, world, 2) != "ok"]
    assert not bad, bad


@pytest.mark.parametrize("actor", [3, 1])          # another manager / the server admin: neither has any link to car A
async def test_nobody_else_sees_another_accounts_car_not_even_the_admin(world, actor) -> None:
    leaks = [(m, p, v) for m, p in vehicle_routes() if (v := await verdict(m, p, world, actor)) != 404]
    assert not leaks, leaks


def test_levels_are_sets_of_permissions_not_a_ladder() -> None:
    assert access.allows("view_control", "control") and access.allows("view_control", "view")
    assert not access.allows("view_control", "edit") and not access.allows("edit", "control")
    assert access.allows("control", "edit") and not access.allows("control", "owner") and not access.allows(None, "view")


@pytest.mark.parametrize("level", ["view", "edit", "view_control", "control"])
async def test_a_member_gets_exactly_what_the_level_allows(world, level) -> None:
    await link(level)
    wrong = []
    for method, path in vehicle_routes():
        needed = access.required_level(method, path)
        v = await verdict(method, path, world, 4)
        expected = "ok" if access.allows(level, needed) else 403
        if v != expected:
            wrong.append((method, path, needed, v))
    assert not wrong, wrong


async def test_a_member_without_a_link_sees_nothing(world) -> None:
    await link(None)
    assert [(m, p) for m, p in vehicle_routes() if await verdict(m, p, world, 4) != 404] == []


async def test_a_read_only_account_never_writes_even_with_control(world) -> None:
    await link("control", read_only=True)
    for method, path in vehicle_routes():
        v = await verdict(method, path, world, 4)
        assert v == ("ok" if access.required_level(method, path) == "view" else 403), (method, path, v)


async def test_a_link_to_one_car_does_not_open_another(world) -> None:
    await link("control")
    other = {**world, "vin": VIN_B}
    assert {await verdict(m, p, other, 4) for m, p in vehicle_routes() if "{vin}" in p and not re.search(r"_id\}", p)} == {404}


async def test_ids_of_somebody_elses_things_are_not_found(world) -> None:
    """Resources reached by their own id (a trip, an expense...) resolve to their car: another manager gets 404, not the data."""
    for param in ("drive_id", "charge_id", "refuel_id", "expense_id", "part_id", "item_id", "log_id", "document_id", "schedule_id", "journey_id"):
        for method, path in vehicle_routes():
            if f"{{{param}}}" in path and "{vin}" not in path:
                assert await verdict(method, path, world, 3) == 404, (method, path)


@pytest.mark.parametrize("role_id,expected", [(1, {"any": "ok", "root": 403}), (2, {"any": "ok", "root": "ok"}), (4, {"any": "ok", "root": 403})])
async def test_account_rules_by_role(world, role_id, expected) -> None:
    for (method, path), rule in access.ACCOUNT_RULES.items():
        if re.search(r"\{(geofence_id|member_id|device_id|passkey_id)\}", path):
            continue                                       # these also check that the thing is in the caller's space (next test)
        v = await verdict(method, path, world, role_id)
        assert v == expected[rule], (role_id, method, path, rule, v)


async def test_space_resources_belong_to_one_manager(world) -> None:
    """Geofences, member accounts and push devices of manager A do not exist for manager B or for the admin."""
    for method, path in (("PUT", "/api/geofences/{geofence_id}"), ("DELETE", "/api/geofences/{geofence_id}"), ("PATCH", "/api/members/{member_id}"),
                         ("DELETE", "/api/members/{member_id}"), ("DELETE", "/api/notifications/push/devices/{device_id}"),
                         ("DELETE", "/api/passkeys/{passkey_id}")):
        assert await verdict(method, path, world, 2) == "ok", (method, path)
        assert await verdict(method, path, world, 3) == 404, (method, path)               # another manager: it does not exist
        assert await verdict(method, path, world, 1) == (404 if ("push/devices" in path or "passkeys" in path) else 403), (method, path)    # the administrator is not a manager at all
        # a member never manages geofences or other members; push devices are per account, so a device of someone else is just not found
        assert await verdict(method, path, world, 4) == (404 if ("push/devices" in path or "passkeys" in path) else 403), (method, path)


async def test_an_unclassified_route_is_denied(db) -> None:
    scope = {"type": "http", "method": "GET", "path": "/api/nueva/ruta", "headers": [], "query_string": b"", "path_params": {}, "route": SimpleNamespace(path="/api/nueva/ruta")}
    async with async_session_factory() as s:
        admin = AppUser(id=1, role="admin", username="x", password_hash="x")        # even the admin: new routes are denied until classified
        with pytest.raises(HTTPException) as exc:
            await access.authorize(Request(scope), admin, s)
    assert exc.value.status_code == 403


# ───────────────────────── end to end: the rule is wired into the router ─────────────────────────
async def client_for(user_id):
    async with async_session_factory() as s:
        user = await s.get(AppUser, user_id)
        token = await auth.create_session(s, user, _Req())
    c = httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://testserver")
    c.cookies.set(auth.COOKIE_NAME, token)
    return c


async def test_http_isolation_and_levels(world) -> None:
    await link("view")
    anon = httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://testserver")
    beto, member, ana, admin = await client_for(3), await client_for(4), await client_for(2), await client_for(1)
    try:
        url = f"/api/vehicles/{VIN_A}/expenses"
        assert (await anon.get(url)).status_code == 401
        assert (await beto.get(url)).status_code == 404 and (await admin.get(url)).status_code == 401     # an admin session is worthless in the app
        assert (await ana.get(url)).status_code == 200 and (await member.get(url)).status_code == 200
        body = {"category": "tax", "title": "Nuevo", "amount": 1}
        assert (await member.post(url, json=body)).status_code == 403                  # view cannot write
        await link("edit")
        assert (await member.post(url, json=body)).status_code == 200
        assert (await member.post(f"/api/vehicles/{VIN_A}/commands", json={"command": "x"})).status_code == 403   # edit cannot control
        assert (await member.put(f"/api/vehicles/{VIN_A}", json={"name": "Hackeado"})).status_code == 403          # the sheet is the owner's
        assert (await beto.delete(f"/api/expenses/{world['expense_id']}")).status_code == 404
        assert (await beto.get(f"/api/drives/{world['drive_id']}")).status_code == 404
        assert [v["vin"] for v in (await beto.get("/api/vehicles")).json()] == [VIN_B]
        assert (await admin.get("/api/vehicles")).status_code == 401
        listed = (await member.get("/api/vehicles")).json()
        assert [(v["vin"], v["access_level"]) for v in listed] == [(VIN_A, "edit")]
    finally:
        for c in (anon, beto, member, ana, admin):
            await c.aclose()


async def test_a_manual_car_has_an_alphanumeric_vin_and_refuses_what_needs_the_ebro_cloud(world) -> None:
    ana = await client_for(2)
    try:
        r = await ana.post("/api/vehicles", json={"name": "Sin cuenta"})
        assert r.status_code == 200
        vin = r.json()["vin"]
        assert vin.startswith("MAN") and vin.isalnum() and len(vin) == 17                      # same rules as a real VIN (it becomes a folder name)
        for method, url in (("get", f"/api/vehicles/{vin}/alarm"), ("post", f"/api/vehicles/{vin}/poll"), ("get", f"/api/vehicles/{vin}/charge/plan"),
                            ("post", f"/api/vehicles/{vin}/commands")):
            res = await getattr(ana, method)(url, **({"json": {"command": "x"}} if method == "post" else {}))
            assert res.status_code == 409 and "Ebro" in res.json()["detail"], (method, url, res.status_code)
        assert (await ana.get(f"/api/vehicles/{vin}/expenses")).status_code == 200            # everything else works
    finally:
        await ana.aclose()
