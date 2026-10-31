"""Background services per manager: discovery, PIN, tokens and the MQTT channel never mix the Ebro accounts of different managers."""
from __future__ import annotations

import tomllib
from pathlib import Path

import httpx
import pytest
from sqlalchemy import select

from app import __version__
from app.api import routes_account
from app.config import settings
from app.db.models import AppUser, EbroAccount, Vehicle
from app.db.session import async_session_factory
from app.main import app
from app.security import auth, passwords
from app.services import car_events as ce
from app.services import poller as poller_mod
from app.services.poller import VehiclePoller

PW = "una-contraseña-larga-1"
VIN_A, VIN_B, NEW1, NEW2 = "LSJAAAAAAAAAAAAAA", "LSJBBBBBBBBBBBBBB", "LSJNEW0000000001", "LSJNEW0000000002"


class _Req:
    client = type("C", (), {"host": "127.0.0.1"})()
    headers = {"user-agent": "t"}


@pytest.fixture(autouse=True)
def _fresh_hub(monkeypatch):
    monkeypatch.setattr(ce.CarEventHub, "_instance", None)


@pytest.fixture
async def world(db, data_dir):
    h = passwords.hash_password(PW)
    async with async_session_factory() as s:
        s.add_all([
            AppUser(id=1, username="admin", role="admin", is_admin=True, password_hash=h, vehicle_limit=0, member_limit=0),
            AppUser(id=4, username="carla", role="manager", password_hash=h, vehicle_limit=2, member_limit=5),
            AppUser(id=2, username="ana", role="manager", password_hash=h, vehicle_limit=2, member_limit=5),
            AppUser(id=3, username="beto", role="manager", password_hash=h, vehicle_limit=2, member_limit=5),
        ])
        s.add_all([Vehicle(owner_id=2, vin=VIN_A, name="Coche de Ana"), Vehicle(owner_id=3, vin=VIN_B, name="Coche de Beto")])
        s.add_all([EbroAccount(user_id=2, phone="600000002", password="x", pin="1111", is_active=True),
                   EbroAccount(user_id=3, phone="600000003", password="x", pin="2222", is_active=True)])
        await s.commit()


async def client_for(user_id):
    async with async_session_factory() as s:
        user = await s.get(AppUser, user_id)
        token = await auth.create_session(s, user, _Req())
    c = httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://testserver")
    c.cookies.set(auth.COOKIE_NAME, token)
    return c


@pytest.fixture
def ebro(monkeypatch):
    """Fake Ebro cloud: login works, and listing the account returns `cars` (recording which VINs were protected from token writes)."""
    state = {"cars": [], "skipped": None}

    def login(phone, password, area_code="34", **kw):
        return {"access_token": "t"}

    def list_cars(token_data, skip_vins=frozenset()):
        state["skipped"] = set(skip_vins)
        return [{"vin": v, "nickname": f"Nuevo {v[-2:]}", "fullName": "S900", "brandName": "Ebro"} for v in state["cars"]]

    for mod in (poller_mod, routes_account):
        monkeypatch.setattr(mod.ebro_login, "login_with_password", login)
        monkeypatch.setattr(mod, "fetch_account_vehicles", list_cars)
    monkeypatch.setattr(VehiclePoller, "request_poll", lambda self, vin, force=False: None)
    return state


# ───────────────────────── discovery ─────────────────────────
async def test_discovery_never_touches_a_car_of_another_manager_and_protects_its_token(world, ebro) -> None:
    ebro["cars"] = [VIN_A, VIN_B]                                   # Ana's Ebro account also lists Beto's car (shared in the Ebro app)
    async with async_session_factory() as s:
        acc = (await s.execute(select(EbroAccount).where(EbroAccount.user_id == 2))).scalar_one()
    await VehiclePoller.get_instance().discover_vehicles(account=acc)
    assert VIN_B in ebro["skipped"] and VIN_A not in ebro["skipped"]      # tokens of others are not overwritten
    async with async_session_factory() as s:
        b = (await s.execute(select(Vehicle).where(Vehicle.vin == VIN_B))).scalar_one()
        a = (await s.execute(select(Vehicle).where(Vehicle.vin == VIN_A))).scalar_one()
    assert (b.owner_id, b.name) == (3, "Coche de Beto")             # not renamed, not taken
    assert a.name == "Nuevo AA" and a.owner_id == 2                 # its own car is refreshed


async def test_new_cars_get_the_account_owner_and_stop_at_the_vehicle_limit(world, ebro) -> None:
    ebro["cars"] = [VIN_A, NEW1, NEW2]                              # Ana: limit 2, already has 1 -> only one slot
    async with async_session_factory() as s:
        acc = (await s.execute(select(EbroAccount).where(EbroAccount.user_id == 2))).scalar_one()
    found = await VehiclePoller.get_instance().discover_vehicles(account=acc)
    async with async_session_factory() as s:
        owned = {v for (v,) in (await s.execute(select(Vehicle.vin).where(Vehicle.owner_id == 2))).all()}
    assert owned == {VIN_A, NEW1} and NEW2 not in owned
    assert {v.vin for v in found} == {VIN_A, NEW1}


async def test_the_discover_route_uses_the_callers_own_account(world, ebro) -> None:
    ebro["cars"] = [NEW1]
    ana, admin = await client_for(2), await client_for(4)
    try:
        r = await admin.post("/api/vehicles/discover")                      # Carla has no Ebro account
        assert r.status_code == 409 and "cuenta" in r.json()["detail"]
        r = await ana.post("/api/vehicles/discover")
        assert r.status_code == 200 and NEW1 in r.json()["vehicles"]
        async with async_session_factory() as s:
            assert (await s.execute(select(Vehicle.owner_id).where(Vehicle.vin == NEW1))).scalar_one() == 2
    finally:
        await ana.aclose()
        await admin.aclose()


async def test_an_ebro_account_cannot_be_claimed_by_two_users(world, ebro) -> None:
    beto = await client_for(3)
    try:
        r = await beto.post("/api/account/login", json={"phone": "600000002", "password": "x", "area_code": "34"})
        assert r.status_code == 409 and "otro usuario" in r.json()["detail"]
    finally:
        await beto.aclose()


async def test_the_account_routes_only_show_the_callers_own_account_and_cars(world) -> None:
    ana, beto = await client_for(2), await client_for(3)
    try:
        a = (await ana.get("/api/account/status")).json()
        assert a["connected"] and [v["vin"] for v in a["vehicles"]] == [VIN_A] and "600000003" not in str(a)
        b = (await beto.get("/api/account/status")).json()
        assert [v["vin"] for v in b["vehicles"]] == [VIN_B]
        assert (await ana.post("/api/account/pin", json={"pin": "5555"})).status_code == 200
        async with async_session_factory() as s:
            pins = {u: p for u, p in (await s.execute(select(EbroAccount.user_id, EbroAccount.pin))).all()}
        assert pins == {2: "5555", 3: "2222"}                               # Ana's PIN change did not touch Beto's account
    finally:
        await ana.aclose()
        await beto.aclose()


async def test_the_ebro_account_status_counts_only_the_cars_of_the_ebro_account(world) -> None:
    """A car added by hand (no Ebro account) belongs to the manager but not to its Ebro account."""
    async with async_session_factory() as s:
        s.add(Vehicle(owner_id=2, vin="MAN0000000000001", name="Coche a mano", is_manual=True))
        await s.commit()
    ana = await client_for(2)
    try:
        a = (await ana.get("/api/account/status")).json()
        assert a["vehicles_count"] == 1 and [v["vin"] for v in a["vehicles"]] == [VIN_A]
        assert len((await ana.get("/api/vehicles")).json()) == 2                 # it is still one of Ana's cars
    finally:
        await ana.aclose()


async def test_version_endpoint_needs_a_session_and_reports_the_build(world, monkeypatch) -> None:
    pyproject = tomllib.loads((Path(__file__).resolve().parents[1] / "pyproject.toml").read_text())
    assert __version__ == pyproject["project"]["version"]
    anon = httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://testserver")
    try:
        assert (await anon.get("/api/version")).status_code == 401
    finally:
        await anon.aclose()
    ana = await client_for(2)
    try:
        monkeypatch.delenv("EVBRO_COMMIT", raising=False)
        assert (await ana.get("/api/version")).json() == {"version": __version__, "commit": None}
        monkeypatch.setenv("EVBRO_COMMIT", " abc1234 ")
        assert (await ana.get("/api/version")).json()["commit"] == "abc1234"
    finally:
        await ana.aclose()


# ───────────────────────── PIN ─────────────────────────
async def test_each_car_uses_the_pin_of_its_owners_account(world, monkeypatch) -> None:
    monkeypatch.setattr(settings, "EBRO_PIN", "9999")
    p = VehiclePoller.get_instance()
    for vin in (VIN_A, VIN_B):
        p._contexts.pop(vin, None)
    assert await p._stored_pin(p._get_or_create_ctx(VIN_A)) == "1111"
    assert await p._stored_pin(p._get_or_create_ctx(VIN_B)) == "2222"
    async with async_session_factory() as s:
        for uid in (2, 3):
            acc = (await s.execute(select(EbroAccount).where(EbroAccount.user_id == uid))).scalar_one()
            acc.pin = None
        await s.commit()
    for vin in (VIN_A, VIN_B):
        p._contexts.pop(vin, None)
    # the environment PIN is the server owner's (the first manager): it never leaks to another manager's car
    assert await p._stored_pin(p._get_or_create_ctx(VIN_A)) == "9999"
    assert await p._stored_pin(p._get_or_create_ctx(VIN_B)) == ""


async def test_a_new_context_starts_without_any_shared_pin(monkeypatch) -> None:
    monkeypatch.setattr(settings, "EBRO_PIN", "9999")
    p = VehiclePoller.get_instance()
    p._contexts.pop("LSJZZZ", None)
    assert p._get_or_create_ctx("LSJZZZ").pin == ""


# ───────────────────────── MQTT hub ─────────────────────────
class FakeService:
    instances = []

    def __init__(self, owner_id=None, **kw):
        self.owner_id, self.vin, self.started_with, self.stopped = owner_id, None, None, False
        FakeService.instances.append(self)

    async def start(self, poller, vins=None):
        self.started_with, self.vin = list(vins or []), (vins or [None])[0]

    async def stop(self):
        self.stopped = True

    async def restart(self, poller, vins=None):
        await self.stop()
        await self.start(poller, vins)

    def fields_for(self, vin):
        return {"owner": str(self.owner_id)} if vin == self.vin else {}

    def status(self):
        return {"owner": self.owner_id}


async def test_the_hub_runs_one_channel_per_manager_with_their_own_cars(world) -> None:
    FakeService.instances = []
    hub = ce.CarEventHub(service_factory=FakeService)
    await hub.start(object())
    assert {(s.owner_id, tuple(s.started_with)) for s in FakeService.instances} == {(2, (VIN_A,)), (3, (VIN_B,))}     # the admin has no Ebro account
    assert hub.fields_for(VIN_A) == {"owner": "2"} and hub.fields_for(VIN_B) == {"owner": "3"} and hub.fields_for("OTRO") == {}
    assert hub.status_for(2) == {"owner": 2}
    await hub.stop_owner(2)
    assert [s.stopped for s in sorted(FakeService.instances, key=lambda s: s.owner_id)] == [True, False]               # only Ana's stopped


async def test_a_service_only_listens_for_its_managers_cars() -> None:
    class P:
        _contexts = {VIN_A: object(), VIN_B: object()}

    assert ce.CarEventService._pick_vehicle(P(), [VIN_B])[0] == VIN_B
    assert ce.CarEventService._pick_vehicle(P(), [VIN_A])[0] == VIN_A
    assert ce.CarEventService._pick_vehicle(P(), ["NINGUNO"]) is None
    assert ce.CarEventService._pick_vehicle(P(), None) is not None            # single-account use is unchanged


async def test_the_mqtt_status_and_reconnect_routes_are_per_user(world) -> None:
    ana, beto = await client_for(2), await client_for(3)
    try:
        assert (await ana.get("/api/car-events/status")).status_code == 200
        hub = ce.CarEventHub.get_instance()
        assert set(hub._services) >= {2} and 3 not in hub._services          # Beto's channel is not created by Ana's request
        assert (await beto.post("/api/car-events/reconnect")).status_code == 200
        assert 3 in hub._services
    finally:
        await ana.aclose()
        await beto.aclose()
