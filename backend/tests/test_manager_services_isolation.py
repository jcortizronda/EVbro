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

    def list_cars(token_data, skip_vins=frozenset(), session_path=None):
        state["skipped"] = set(skip_vins)
        state["session_path"] = session_path
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
        assert (await ana.post("/api/account/pin", json={"pin": "555555"})).status_code == 200
        async with async_session_factory() as s:
            pins = {u: p for u, p in (await s.execute(select(EbroAccount.user_id, EbroAccount.pin))).all()}
        assert pins == {2: "555555", 3: "2222"}                               # Ana's PIN change did not touch Beto's account
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


# ───────────────────────── a VIN that only a file claims gives way to the Ebro account that proves it ─────────────────────────
async def test_a_car_without_ebro_account_gives_its_vin_to_the_account_that_proves_it(world, ebro) -> None:
    """An imported account brings its cars with the VIN written in the file. Typed there, somebody else's VIN used to stay taken and
    the real owner's Ebro account skipped the car. The imported car now moves to a generated VIN with everything it had."""
    from app.db.models import AdminAudit, Expense, MaintenanceItem

    async with async_session_factory() as s:
        s.add(Vehicle(owner_id=3, vin=NEW1, name="Importado por Beto", is_manual=True, license_plate="0000XXX"))
        s.add(Vehicle(owner_id=3, vin=NEW2, name="Otro importado", is_manual=True))
        await s.flush()
        s.add(MaintenanceItem(vin=NEW1, name="Aceite"))
        s.add(Expense(vin=NEW1, category="other", title="Lavado", amount=12.0, date=__import__("datetime").date(2026, 1, 2)))
        await s.commit()
    ebro["cars"] = [VIN_A, NEW1, VIN_B]            # Ana's Ebro account proves NEW1; VIN_B is Beto's *connected* car
    ana = await client_for(2)
    try:
        r = await ana.post("/api/vehicles/discover")
        assert r.status_code == 200 and NEW1 in r.json()["vehicles"]
    finally:
        await ana.aclose()
    assert NEW1 not in ebro["skipped"] and VIN_B in ebro["skipped"]          # only connected cars of others are protected
    async with async_session_factory() as s:
        proved = (await s.execute(select(Vehicle).where(Vehicle.vin == NEW1))).scalar_one()
        assert proved.owner_id == 2 and proved.is_manual is False and proved.name == "Nuevo 01"
        moved = (await s.execute(select(Vehicle).where(Vehicle.owner_id == 3, Vehicle.name == "Importado por Beto"))).scalar_one()
        assert moved.vin.startswith("MAN") and len(moved.vin) == 17 and moved.is_manual and moved.chassis_number == NEW1
        assert moved.license_plate == "0000XXX"
        assert (await s.execute(select(MaintenanceItem.vin).where(MaintenanceItem.name == "Aceite"))).scalar_one() == moved.vin
        assert (await s.execute(select(Expense.vin).where(Expense.title == "Lavado"))).scalar_one() == moved.vin
        beto_connected = (await s.execute(select(Vehicle).where(Vehicle.vin == VIN_B))).scalar_one()
        assert beto_connected.owner_id == 3                                   # a car connected by its own Ebro account never moves
        untouched = (await s.execute(select(Vehicle).where(Vehicle.vin == NEW2))).scalar_one()
        assert untouched.owner_id == 3                                        # nobody proved this one
        audit = (await s.execute(select(AdminAudit).where(AdminAudit.action == "car.vin_released"))).scalar_one()
        assert audit.target == NEW1 and moved.vin in audit.detail



async def test_the_managers_own_car_without_account_becomes_connected_when_its_ebro_account_lists_it(world, ebro) -> None:
    """An account imported from a file brings its own car as «sin cuenta de Ebro». Connecting the Ebro account that has it used
    to refresh its name and leave it unconnected for good (never read). Now it becomes a connected car, keeping what it had."""
    from app.db.models import Expense

    async with async_session_factory() as s:
        s.add(Vehicle(owner_id=2, vin=NEW1, name="Mi coche importado", is_manual=True, license_plate="0000YYY"))
        await s.flush()
        s.add(Expense(vin=NEW1, category="other", title="Lavado", amount=9.0, date=__import__("datetime").date(2026, 1, 3)))
        await s.commit()
        acc = (await s.execute(select(EbroAccount).where(EbroAccount.user_id == 2))).scalar_one()
    ebro["cars"] = [VIN_A, NEW1]
    await VehiclePoller.get_instance().discover_vehicles(account=acc)
    async with async_session_factory() as s:
        car = (await s.execute(select(Vehicle).where(Vehicle.vin == NEW1))).scalar_one()
        assert (car.owner_id, car.is_manual, car.name, car.license_plate) == (2, False, "Nuevo 01", "0000YYY")
        assert (await s.execute(select(Expense.vin).where(Expense.title == "Lavado"))).scalar_one() == NEW1   # its history stays with it
        assert (await s.execute(select(Vehicle).where(Vehicle.owner_id == 2))).scalars().all().__len__() == 2  # no second copy

async def test_the_login_route_also_frees_a_claimed_vin_but_not_beyond_the_vehicle_limit(world, ebro) -> None:
    async with async_session_factory() as s:
        s.add(Vehicle(owner_id=3, vin=NEW1, name="Importado por Beto", is_manual=True))
        s.add(Vehicle(owner_id=3, vin=NEW2, name="Otro importado", is_manual=True))
        await s.commit()
    ebro["cars"] = [VIN_A, NEW1, NEW2]             # Ana: limit 2, already has 1 -> room for one only
    ana = await client_for(2)
    try:
        r = await ana.post("/api/account/login", json={"phone": "600000002", "password": "x", "area_code": "34"})
        assert r.status_code == 200, r.text
    finally:
        await ana.aclose()
    async with async_session_factory() as s:
        owners = {v.vin: (v.owner_id, v.is_manual) for v in (await s.execute(select(Vehicle))).scalars()}
    assert owners[NEW1] == (2, False)                                         # proved and taken
    assert owners[NEW2] == (3, True)                                          # no room in Ana's account: Beto's car stays as it was
    assert sum(1 for vin, (owner, manual) in owners.items() if owner == 3 and manual and vin.startswith("MAN")) == 1


# ───────────────────────── a shared server does not connect into its own network for a manager ─────────────────────────
async def test_private_links_are_allowed_by_default_and_refused_on_a_shared_server(world, monkeypatch) -> None:
    import socket

    from app.security import netguard
    from app.services import mqtt_hub
    from app.services.traccar import TraccarClient, TraccarError

    def resolve(ip):
        return lambda *a, **k: [(socket.AF_INET, socket.SOCK_STREAM, 6, "", (ip, 0))]

    monkeypatch.setattr(socket, "getaddrinfo", resolve("192.168.1.10"))
    assert settings.ALLOW_PRIVATE_LINKS is True                              # a self-hosted EVbro reaches its LAN, as always
    assert netguard.private_link_problem("traccar.lan", 8082) is None
    monkeypatch.setattr(settings, "ALLOW_PRIVATE_LINKS", False)
    assert "públicas" in netguard.private_link_problem("traccar.lan", 8082)
    assert netguard.url_host_port("http://10.0.0.5:8123/any/path?x=1#:8082/api") == ("10.0.0.5", 8123)     # what the URL really reaches
    ana = await client_for(2)
    try:
        body = {"host": "10.0.0.5:8123/any/path?x=1#", "api_token": "t", "port": 8082}
        r = await ana.post(f"/api/vehicles/{VIN_A}/traccar/test", json=body)
        assert r.status_code == 502 and "públicas" in r.json()["detail"]
        r = await ana.put(f"/api/vehicles/{VIN_A}/traccar", json={**body, "device_id": 1})
        assert r.status_code == 422 and "públicas" in r.json()["detail"]
        mqtt = {"host": "192.168.1.10", "port": 1883, "username": "u", "password": "p"}
        r = await ana.post("/api/account/mqtt/test", json=mqtt)
        assert r.status_code == 200 and r.json()["ok"] is False and "públicas" in r.json()["error"]
        r = await ana.put("/api/account/mqtt", json=mqtt)
        assert r.status_code == 422 and "públicas" in r.json()["detail"]
    finally:
        await ana.aclose()
    # a link saved before the switch (or a name that now points inside) is not connected either
    with pytest.raises(TraccarError):
        await TraccarClient("192.168.1.10", "t").get_server()
    started = []
    monkeypatch.setattr(mqtt_hub, "_new_client", lambda *a, **k: started.append(a) or (_ for _ in ()).throw(AssertionError("must not connect")))
    client = mqtt_hub.MqttLinkClient(mqtt_hub.MqttLinkConfig(host="192.168.1.10", guarded=True, client_id="evbro_2"))
    await client.start()
    assert started == [] and "públicas" in client.last_error
    # a public address passes, and so does the operator's own broker from the environment (not guarded)
    monkeypatch.setattr(socket, "getaddrinfo", resolve("93.184.216.34"))
    assert netguard.private_link_problem("traccar.example", 443) is None
    assert mqtt_hub.MqttLinkConfig.from_env().guarded is False
    monkeypatch.setattr(socket, "getaddrinfo", lambda *a, **k: (_ for _ in ()).throw(OSError("no such host")))
    assert "resolver" in netguard.private_link_problem("nope.example", 443)

