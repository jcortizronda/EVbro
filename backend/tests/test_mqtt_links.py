"""A manager's own MQTT broker: saved encrypted, never returned, one client per manager, each car published only on its owner's broker."""
from __future__ import annotations

import json

import httpx
import pytest

from app.db.models import AppUser, MqttLink, Vehicle
from app.db.session import async_session_factory
from app.main import app
from app.security import auth, passwords
from app.services import mqtt_hub
from app.services.mqtt_hub import MqttHub, MqttLinkClient, MqttLinkConfig

VIN_A, VIN_B = "LSJA0000000000061", "LSJA0000000000062"
ORIGIN = {"Origin": "http://testserver"}


class _Req:
    client = type("C", (), {"host": "127.0.0.1"})()
    headers = {"user-agent": "t"}


class FakePaho:
    """Stands in for the paho client: connects at once, records publishes."""

    def __init__(self, *a, **k):
        self.published: list[tuple[str, str]] = []
        self.on_connect = None

    def username_pw_set(self, *a): pass
    def tls_set(self, **k): pass
    def will_set(self, *a, **k): pass
    def reconnect_delay_set(self, **k): pass
    def subscribe(self, *a, **k): pass
    def loop_start(self): pass
    def loop_stop(self): pass
    def disconnect(self): pass

    def connect_async(self, host, port, keepalive):
        if self.on_connect:
            self.on_connect(self, None, None, 0 if host != "bad.example" else 5)

    def publish(self, topic, payload, qos=0, retain=False):
        self.published.append((topic, payload))


@pytest.fixture(autouse=True)
def _fake_paho(monkeypatch):
    monkeypatch.setattr(mqtt_hub, "_new_client", lambda client_id: FakePaho())
    MqttHub._instance = None
    yield
    MqttHub._instance = None


async def _client(user_id: int):
    async with async_session_factory() as s:
        user = await s.get(AppUser, user_id)
        token = await auth.create_session(s, user, _Req())
    c = httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://testserver", headers=ORIGIN)
    c.cookies.set(auth.COOKIE_NAME, token)
    return c


@pytest.fixture
async def world(db):
    h = passwords.hash_password("una-contraseña-larga-1")
    async with async_session_factory() as s:
        s.add_all([AppUser(id=2, role="manager", username="ana", vehicle_limit=-1, password_hash=h),
                   AppUser(id=3, role="manager", username="bea", vehicle_limit=-1, password_hash=h),
                   AppUser(id=4, role="member", username="mia", parent_id=2, password_hash=h),
                   Vehicle(owner_id=2, vin=VIN_A, name="A", brand="Ebro", model="S900 PHEV"), Vehicle(owner_id=3, vin=VIN_B, name="B")])
        await s.commit()
    ana, bea, mia = await _client(2), await _client(3), await _client(4)
    yield {"ana": ana, "bea": bea, "mia": mia}
    for c in (ana, bea, mia):
        await c.aclose()


async def test_the_link_is_saved_encrypted_connects_and_never_returns_the_password(world) -> None:
    ana, mia = world["ana"], world["mia"]
    assert (await ana.get("/api/account/mqtt")).json() == {"link": None}
    assert (await mia.get("/api/account/mqtt")).status_code == 403                                         # members have no broker
    r = await ana.put("/api/account/mqtt", json={"host": "ha.local", "username": "evbro", "password": "secreta-123"})
    assert r.status_code == 200, r.text
    link = r.json()["link"]
    assert link["host"] == "ha.local" and link["has_password"] is True and "password" not in link and link["connected"] is True
    assert "secreta-123" not in r.text
    async with async_session_factory() as s:
        row = (await s.execute(__import__("sqlalchemy").select(MqttLink))).scalar_one()
        assert row.password == "secreta-123" and row.user_id == 2
    # saving again without a password keeps the stored one; "" too
    r = await ana.put("/api/account/mqtt", json={"host": "ha.local", "username": "evbro", "password": "", "port": 1884})
    assert r.json()["link"]["has_password"] is True and r.json()["link"]["port"] == 1884
    assert (await ana.put("/api/account/mqtt", json={"host": "ha.local", "topic_prefix": "bad prefix"})).status_code == 422
    # the hub has one client for Ana, none for Bea
    hub = MqttHub.get_instance()
    assert set(hub._clients) == {2}
    # a wrong broker reports its error
    r = await ana.put("/api/account/mqtt", json={"host": "bad.example", "username": "x", "password": "y"})
    assert r.json()["link"]["connected"] is False and "No autorizado" in r.json()["link"]["last_error"]
    assert (await ana.post("/api/account/mqtt/test", json={"host": "bad.example"})).json() == {"ok": False, "error": "No autorizado."}
    assert (await ana.post("/api/account/mqtt/test", json={"host": "ha.local"})).json() == {"ok": True}
    # remove: no client left
    assert (await ana.delete("/api/account/mqtt")).json() == {"link": None}
    assert hub._clients == {}


async def test_each_car_is_published_only_on_its_owners_broker_and_commands_stay_there(world) -> None:
    ana, bea = world["ana"], world["bea"]
    await ana.put("/api/account/mqtt", json={"host": "ana.local"})
    await bea.put("/api/account/mqtt", json={"host": "bea.local", "topic_prefix": "casa"})
    hub = MqttHub.get_instance()
    runs: list = []

    async def runner(vin, command, params, origin):
        runs.append((vin, command, origin))
        return {"success": True}

    async def poll(vin):
        return None
    for c in hub._clients.values():
        c.configure(runner, poll)
    car_a = type("V", (), {"name": "A", "brand": "Ebro", "model": "S900 PHEV"})()
    hub.publish(2, VIN_A, {"battery_level": 50, "latitude": 40.0, "longitude": -3.0}, car_a)
    hub.publish(3, VIN_B, {"battery_level": 70}, None)
    hub.publish(None, "MANUAL", {"battery_level": 1}, None)                       # nobody's: dropped
    pa, pb = hub._clients[2]._client.published, hub._clients[3]._client.published
    assert any(t == f"evbro/cars/{VIN_A}/json" for t, _ in pa) and not any(VIN_B in t for t, _ in pa)
    assert any(t == f"casa/cars/{VIN_B}/json" for t, _ in pb) and not any(VIN_A in t for t, _ in pb)
    assert any(t.startswith("homeassistant/sensor/") for t, _ in pa)              # discovery only where a vehicle was given
    bat = next(json.loads(p) for t, p in pa if t == f"homeassistant/sensor/ebro_{VIN_A}/battery/config")
    assert bat["default_entity_id"] == f"sensor.ebro_{VIN_A[-4:].lower()}_bateria"
    # Home Assistant's birth message (it restarted, or the device was deleted there) makes the discovery go out again
    pa.clear()
    hub._clients[2]._on_message(None, None, type("M", (), {"topic": "homeassistant/status", "payload": b"online"})())
    assert any(t == f"homeassistant/sensor/ebro_{VIN_A}/battery/config" for t, _ in pa)
    pa.clear()
    hub._clients[2]._on_message(None, None, type("M", (), {"topic": "homeassistant/status", "payload": b"offline"})())
    assert pa == []
    # Bea's broker cannot move Ana's car, Ana's own can
    import asyncio
    hub._clients[3]._on_command(f"casa/cars/{VIN_A}/cmd/cierre_centralizado", "UNLOCK")
    hub._clients[2]._on_command(f"evbro/cars/{VIN_A}/cmd/cierre_centralizado", "LOCK")
    await asyncio.sleep(0.05)
    assert runs == [(VIN_A, "bloquear", "mqtt")]
    assert hub.status(2)["cars"] == [VIN_A] and hub.status(3)["connected"] is True


def test_config_from_env_and_link_defaults() -> None:
    cfg = MqttLinkConfig(host="h", use_tls=True)
    client = MqttLinkClient(cfg)
    assert client.availability_topic == "evbro/status" and cfg.port == 1883
    link = MqttLink(user_id=7, host="h", use_tls=True, topic_prefix="x", ha_prefix="homeassistant", ha_discovery=True)
    c2 = MqttLinkConfig.from_link(link)
    assert c2.port == 8883 and c2.client_id == "evbro_7" and c2.topic_prefix == "x"
