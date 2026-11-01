"""The car's MQTT channel: connection, messages, poll triggers and the anti-loop guard."""
from __future__ import annotations

import asyncio
import json
import pathlib

import httpx
import pytest

from app.config import settings
from app.core.context import CoreCtx
from app.core.telemetry import parse_telemetry_data
from app.db.models import AppUser, Vehicle
from app.db.session import async_session_factory
from app.main import app
from app.security import auth, passwords
from app.services import car_events as ce
from app.services.poller import VehiclePoller
from app.vehicle.mqtt_client import MqttConnectError

VIN = "LSJA0000000000001"
FIELDS = json.loads((pathlib.Path(__file__).parent / "vehicle" / "fixtures" / "mqtt_5a02.json").read_text())


def payload(data: dict, service_type: str = "5A02") -> bytes:
    return json.dumps({"content": {"serviceType": service_type, "data": data}}).encode()


class FakeClient:
    """Stands in for the paho client: keeps the callbacks so the test can fire them."""
    instances: list["FakeClient"] = []
    connect_error: Exception | None = None

    def __init__(self, config, *, on_message, on_connected, on_disconnected, on_subscribed):
        self.config = config
        self.on_message, self.on_connected = on_message, on_connected
        self.on_disconnected, self.on_subscribed = on_disconnected, on_subscribed
        self.disconnected = False
        FakeClient.instances.append(self)

    def connect(self):
        if FakeClient.connect_error:
            raise FakeClient.connect_error

    def disconnect(self):
        self.disconnected = True


class StubPoller:
    """What the service needs from the poller, recording the calls."""
    def __init__(self):
        self._contexts = {VIN: CoreCtx(vin=VIN)}
        self.applied, self.polls, self.events = [], [], []

    def apply_car_fields(self, vin, fields, geo=None):
        self.applied.append((vin, dict(fields), geo))

    def request_poll(self, vin, force=False):
        self.polls.append((vin, force))

    def broadcast_event(self, payload):
        self.events.append(payload)


@pytest.fixture(autouse=True)
def _env(monkeypatch, data_dir):
    FakeClient.instances, FakeClient.connect_error = [], None
    monkeypatch.setattr(settings, "CAR_MQTT_ENABLED", True)
    monkeypatch.setattr(settings, "EBRO_VIN", None)
    monkeypatch.setattr(ce.certificates, "provision", lambda *a, **k: (True, "ok"))
    monkeypatch.setattr(ce, "_bff_login", lambda ctx: ("usertoken", "T123"))
    monkeypatch.setattr(ce.CarEventService, "_instance", None)
    monkeypatch.setattr(ce.CarEventHub, "_instance", None)


async def flush():
    await asyncio.sleep(0.02)          # lets the pending call_soon_threadsafe run


async def start_service(poller=None):
    poller = poller or StubPoller()
    svc = ce.CarEventService(client_factory=FakeClient)
    await svc.start(poller)
    return svc, poller


async def connected():
    svc, poller = await start_service()
    client = FakeClient.instances[0]
    client.on_connected(True, 0)
    client.on_subscribed(True, "1", svc._topic)
    await flush()
    return svc, poller, client


# ───────────────────────── start-up and states ─────────────────────────
async def test_disabled_by_default(monkeypatch) -> None:
    monkeypatch.setattr(settings, "CAR_MQTT_ENABLED", False)
    svc, _ = await start_service()
    assert svc.state == "disabled" and FakeClient.instances == []
    assert svc.status()["enabled"] is False


async def test_without_vehicles_it_does_not_connect() -> None:
    poller = StubPoller()
    poller._contexts = {}
    svc, _ = await start_service(poller)
    assert svc.state == "no_account" and FakeClient.instances == []


async def test_connects_with_the_account_tuserid() -> None:
    svc, _ = await start_service()
    assert svc.state == "connecting"
    cfg = FakeClient.instances[0].config
    assert cfg.tuserid == "T123" and cfg.topic == "app/4/T123/account/msgCenter/msg"
    assert cfg.certs_dir.endswith("/certs")


async def test_connected_and_subscribed_states() -> None:
    svc, _, client = await connected()
    assert svc.state == "subscribed" and "app/4/T123" in svc.detail
    client.on_subscribed(False, "128", svc._topic)
    await flush()
    assert svc.state == "rejected" and "propietaria" in svc.detail


async def test_without_certificates_it_never_asks_for_the_token(monkeypatch) -> None:
    monkeypatch.setattr(ce.certificates, "provision", lambda *a, **k: (False, "faltan ca.pem"))
    called = []
    monkeypatch.setattr(ce, "_bff_login", lambda ctx: called.append(1) or ("u", "t"))
    svc, _ = await start_service()
    assert svc.state == "no_certs" and "faltan ca.pem" in svc.detail and not called and not FakeClient.instances


async def test_expired_ebro_session(monkeypatch) -> None:
    monkeypatch.setattr(ce, "_bff_login", lambda ctx: (None, None))
    svc, _ = await start_service()
    assert svc.state == "no_account" and "caducado" in svc.detail


async def test_initial_connection_failure(monkeypatch) -> None:
    FakeClient.connect_error = MqttConnectError("DNS")
    svc, _ = await start_service()
    assert svc.state == "error" and "DNS" in svc.detail


async def test_an_unexpected_error_does_not_bring_down_the_start_up(monkeypatch) -> None:
    monkeypatch.setattr(ce, "_bff_login", lambda ctx: (_ for _ in ()).throw(RuntimeError("boom")))
    svc, _ = await start_service()
    assert svc.state == "error" and "boom" in svc.detail


# ───────────────────────── messages ─────────────────────────
async def test_a_5a02_updates_the_telemetry() -> None:
    svc, poller, client = await connected()
    client.on_message(payload({"trunkDoor": "1", "doorLock": "0"}), svc._topic)
    await flush()
    assert poller.applied == [(VIN, {"trunkDoor": "1", "doorLock": "0"}, None)]
    assert svc.messages == 1 and svc.status()["awake"] is True
    assert svc.fields_for(VIN)["trunkDoor"] == "1" and svc.fields_for("OTRO") == {}


async def test_the_driving_heartbeat_is_not_telemetry() -> None:
    svc, poller, client = await connected()
    client.on_message(payload({"time": "1730000000"}), svc._topic)
    await flush()
    assert poller.applied == [] and svc.messages == 1


async def test_an_unknown_topic_is_ignored() -> None:
    svc, poller, client = await connected()
    client.on_message(payload({"trunkDoor": "1"}), "app/4/OTRO/loquesea")
    await flush()
    assert poller.applied == [] and svc.messages == 0


async def test_garbage_in_the_payload_breaks_nothing() -> None:
    svc, poller, client = await connected()
    for bad in (b"", b"no es json", b"[]", b'{"content": 5}'):
        client.on_message(bad, svc._topic)
    await flush()
    assert poller.applied == []


async def test_waking_up_asks_for_one_unforced_probe_only_once() -> None:
    svc, poller, client = await connected()
    client.on_message(payload({"doorLock": "0"}), svc._topic)
    client.on_message(payload({"trunkDoor": "0"}), svc._topic)
    await flush()
    assert poller.polls == [(VIN, False)]


async def test_the_driving_burst_forces_a_probe_once() -> None:
    svc, poller, client = await connected()
    for _ in range(8):
        client.on_message(payload({"engineState": "1"}), svc._topic)
    await flush()
    assert poller.polls.count((VIN, True)) == 1
    assert svc.status()["moving"] is True


async def test_plugging_or_unplugging_the_cable_forces_a_probe() -> None:
    svc, poller, client = await connected()
    client.on_message(payload({"chargeGunState": "0"}), svc._topic)       # first reading: not a change
    await flush()
    assert (VIN, True) not in poller.polls
    client.on_message(payload({"chargeGunState": "1"}), svc._topic)
    await flush()
    assert poller.polls.count((VIN, True)) == 1
    client.on_message(payload({"chargeGunState": "1"}), svc._topic)       # same as before: nothing
    await flush()
    assert poller.polls.count((VIN, True)) == 1


async def test_a_command_confirmation_reaches_the_web_and_is_not_telemetry() -> None:
    svc, poller, client = await connected()
    client.on_message(payload({"result": "1", "seq": f"{VIN}-1", "resultTime": "x"}, "110D"), svc._topic)
    await flush()
    assert poller.applied == []
    (event,) = poller.events
    assert event["type"] == "command_result" and event["applied"] is True and "confirmado" in event["message"]
    client.on_message(payload({"result": "9", "seq": "x", "reason": ["door open"]}, "110D"), svc._topic)
    await flush()
    assert poller.events[-1]["failed"] is True


async def test_if_a_position_ever_arrives_it_is_passed_to_the_poller() -> None:
    svc, poller, client = await connected()
    client.on_message(payload({"lat": "41.38", "lon": "2.17", "speed": "0", "extra": "x"}, "1301"), svc._topic)
    await flush()
    assert poller.applied[-1][2] == {"lat": "41.38", "lon": "2.17", "speed": "0"}


# ───────────────────────── anti-loop guard (clientId clash) ─────────────────────────
async def test_connections_lasting_seconds_stop_the_service() -> None:
    svc, _ = await start_service()
    client = FakeClient.instances[0]
    for _ in range(ce.FLAP_MAX_EPHEMERAL):
        client.on_connected(True, 0)
        client.on_disconnected(7)             # another client kicks us out instantly
        await flush()
    assert svc.state == "conflict" and "Home Assistant" in svc.detail
    await asyncio.sleep(0.05)
    assert client.disconnected is True


async def test_a_disconnection_after_a_long_time_is_not_a_conflict(monkeypatch) -> None:
    svc, _, client = await connected()
    svc.connected_since -= 3600               # it had been connected for an hour
    client.on_disconnected(0)
    await flush()
    assert svc.state == "connecting" and svc._ephemeral == []


async def test_repeated_broker_rejections_also_stop_the_service() -> None:
    svc, _ = await start_service()
    client = FakeClient.instances[0]
    for _ in range(ce.FLAP_MAX_EPHEMERAL):
        client.on_connected(False, 5)
        await flush()
    assert svc.state == "conflict"


async def test_restart_clears_the_conflict() -> None:
    svc, poller = await start_service()
    client = FakeClient.instances[0]
    for _ in range(ce.FLAP_MAX_EPHEMERAL):
        client.on_connected(False, 5)
        await flush()
    assert svc.state == "conflict"
    await svc.restart(poller)
    assert svc.state == "connecting" and len(FakeClient.instances) == 2


# ───────────────────────── integration with the poller ─────────────────────────
async def test_the_push_merges_over_the_last_reading_and_notifies_over_websocket(db) -> None:
    poller = VehiclePoller()
    poller._latest_snapshots = {VIN: parse_telemetry_data({"dumpEnergy": "80", "trunkDoor": "0", "doorLock": "1"})}
    received = []
    poller._ws_subscribers = {received.append}
    merged = poller.apply_car_fields(VIN, {"trunkDoor": "1"})
    assert merged["doors"]["trunk"] and merged["battery_level"] == 80.0        # keeps the battery from the probe
    assert received[0]["type"] == "telemetry" and received[0]["data"]["doors"]["trunk"]


async def test_the_probe_respects_the_last_push(db, monkeypatch) -> None:
    """The probe may bring an old cloud snapshot: the push wins when both carry the key."""
    from app.services import poller as poller_module
    async with async_session_factory() as s:
        s.add(Vehicle(owner_id=1, vin=VIN, name="Ebro"))
        await s.commit()
    service = ce.CarEventService(client_factory=FakeClient)
    service.vin = VIN
    service.vehicle_state.record_message({"trunkDoor": "1"}, 1.0)
    ce.CarEventHub.get_instance()._services[1] = service              # the service of car's owner (manager 1)

    def fake_probe(ctx, publish, force=False, on_data=None):
        on_data({"dumpEnergy": "55", "trunkDoor": "0"})          # the cloud still says «closed»
        return {"ok": True, "online": True, "got_data": True}
    monkeypatch.setattr(poller_module, "probe_once", fake_probe)

    poller = VehiclePoller()
    poller._contexts = {VIN: CoreCtx(vin=VIN)}
    telemetry = await poller.poll_vehicle(VIN, force=True)
    assert telemetry["doors"]["trunk"] == 1 and telemetry["battery_level"] == 55.0


async def test_request_poll_does_not_stack_probes_for_the_same_vehicle(monkeypatch) -> None:
    poller = VehiclePoller()
    poller._poll_requests = {}
    calls = []

    async def slow(vin, force=False):
        calls.append((vin, force))
        await asyncio.sleep(0.05)
    monkeypatch.setattr(poller, "poll_vehicle", slow)
    poller.request_poll(VIN, force=False)
    poller.request_poll(VIN, force=True)           # one is already running
    await asyncio.sleep(0.1)
    assert calls == [(VIN, False)]
    poller.request_poll(VIN, force=True)
    await asyncio.sleep(0.1)
    assert calls == [(VIN, False), (VIN, True)]


# ───────────────────────── API ─────────────────────────
async def test_the_status_api_requires_a_session_and_answers(db) -> None:
    async with async_session_factory() as s:
        user = AppUser(id=1, role="manager", username="juan", password_hash=passwords.hash_password("una-contraseña-larga-1"))
        s.add(user)
        await s.commit()

        class Req:
            client = type("C", (), {"host": "127.0.0.1"})()
            headers = {"user-agent": "t"}
        token = await auth.create_session(s, user, Req())
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://testserver") as c:
        assert (await c.get("/api/car-events/status")).status_code == 401
        c.cookies.set(auth.COOKIE_NAME, token)
        r = await c.get("/api/car-events/status")
        assert r.status_code == 200 and {"enabled", "state", "detail", "messages", "awake"} <= set(r.json())
        assert (await c.post("/api/car-events/reconnect")).status_code == 200
