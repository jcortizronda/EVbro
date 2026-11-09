"""MQTT clients: one per manager account (their own broker, usually the Mosquitto of their Home Assistant) plus the server's `.env` one.

`MqttLinkClient` does the work for one broker: it publishes the telemetry of the cars it is told about (retained JSON, plain topics,
location), announces them to Home Assistant through MQTT discovery (services/ha_discovery.py) and turns the command topics Home
Assistant writes into catalogue commands (stored PIN, origin `mqtt`). `MqttHub` keeps one client per manager (`mqtt_links`) and routes
each car to its owner's client; the server-wide client from the environment (`MqttPublisher`, services/mqtt_publisher.py) is the same
class with the `.env` settings, kept for the person who runs the server.

A client only accepts commands for the cars it publishes, so a manager's broker can never move another manager's car.
"""
from __future__ import annotations

import asyncio
import dataclasses
import datetime
import json
import logging
import ssl
from typing import Any, Awaitable, Callable, Dict, List, Optional, Set, Tuple

from sqlalchemy import select

from app.config import settings
from app.db.models import MqttLink
from app.db.session import async_session_factory
from app.services import ha_discovery

_LOGGER = logging.getLogger(__name__)

try:
    import paho.mqtt.client as mqtt
except ImportError:                                            # pragma: no cover
    mqtt = None

CommandRunner = Callable[[str, str, Optional[Dict[str, Any]], str], Awaitable[Dict[str, Any]]]
Poll = Callable[[str], Awaitable[Any]]
TEST_TIMEOUT_S = 6.0


@dataclasses.dataclass
class MqttLinkConfig:
    host: str
    port: int = 1883
    username: Optional[str] = None
    password: Optional[str] = None
    use_tls: bool = False
    topic_prefix: str = "evbro"
    ha_discovery: bool = True
    ha_prefix: str = "homeassistant"
    client_id: str = "evbro_server"

    @classmethod
    def from_env(cls) -> "MqttLinkConfig":
        return cls(host=settings.MQTT_HOST, port=settings.MQTT_PORT, username=settings.MQTT_USER, password=settings.MQTT_PASSWORD,
                   topic_prefix=settings.MQTT_TOPIC_PREFIX, ha_discovery=settings.MQTT_HA_DISCOVERY, ha_prefix=settings.MQTT_HA_PREFIX)

    @classmethod
    def from_link(cls, link: MqttLink) -> "MqttLinkConfig":
        return cls(host=link.host, port=link.port or (8883 if link.use_tls else 1883), username=link.username or None, password=link.password or None,
                   use_tls=bool(link.use_tls), topic_prefix=link.topic_prefix or "evbro", ha_discovery=bool(link.ha_discovery),
                   ha_prefix=link.ha_prefix or "homeassistant", client_id=f"evbro_{link.user_id}")


def _new_client(client_id: str):
    if hasattr(mqtt, "CallbackAPIVersion"):
        return mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id=client_id)
    return mqtt.Client(client_id=client_id)                     # pragma: no cover — paho 1.x


class MqttLinkClient:
    """One broker. Instance attributes are deliberately simple (tests poke them)."""

    def __init__(self, config: MqttLinkConfig) -> None:
        self.config = config
        self._client: Optional[Any] = None
        self._connected: bool = False
        self._loop: Optional[asyncio.AbstractEventLoop] = None
        self._announced: Set[str] = set()
        self._published: Set[str] = set()
        self._vehicles: Dict[str, Tuple] = {}   # vin -> (name, brand, model, reported keys) for re-announcing
        self._last_target_temp: Dict[str, float] = {}
        self._runner: Optional[CommandRunner] = None
        self._poll: Optional[Poll] = None
        self.last_error: Optional[str] = None
        self.connected_at: Optional[datetime.datetime] = None

    # ───────────────────────── lifecycle ─────────────────────────
    def configure(self, runner: CommandRunner, poll: Poll) -> None:
        self._runner, self._poll = runner, poll

    @property
    def availability_topic(self) -> str:
        return ha_discovery.topics(self.config.topic_prefix, "x")["availability"]

    @property
    def birth_topic(self) -> str:
        """Home Assistant publishes `online` here when it (re)starts: the moment to publish the discovery again."""
        return f"{self.config.ha_prefix}/status"

    async def start(self) -> None:
        if mqtt is None or not self.config.host:
            return
        self._loop = asyncio.get_running_loop()
        try:
            self._client = _new_client(self.config.client_id)
            if self.config.username:
                self._client.username_pw_set(self.config.username, self.config.password or None)
            if self.config.use_tls:
                self._client.tls_set(cert_reqs=ssl.CERT_REQUIRED)
            self._client.will_set(self.availability_topic, "offline", qos=1, retain=True)
            self._client.on_connect = self._on_connect
            self._client.on_disconnect = self._on_disconnect
            self._client.on_message = self._on_message
            self._client.reconnect_delay_set(min_delay=5, max_delay=120)
            self._client.connect_async(self.config.host, self.config.port, 60)
            self._client.loop_start()
        except Exception as e:                                 # noqa: BLE001
            self.last_error = str(e)[:200]
            _LOGGER.warning(f"Could not start the MQTT client for {self.config.host}: {e}")

    def _on_connect(self, client, userdata, flags, rc, properties=None):
        code = getattr(rc, "value", rc)
        if code == 0:
            self._connected, self.last_error, self.connected_at = True, None, datetime.datetime.utcnow()
            self._announced = set()
            client.publish(self.availability_topic, "online", qos=1, retain=True)
            if self.config.ha_discovery:
                client.subscribe(f"{self.config.topic_prefix}/cars/+/cmd/#", qos=1)
                client.subscribe(self.birth_topic, qos=1)
            _LOGGER.info(f"Connected to the MQTT broker at {self.config.host}:{self.config.port}")
        else:
            self._connected = False
            self.last_error = {4: "Usuario o contraseña incorrectos", 5: "No autorizado"}.get(int(code) if str(code).isdigit() else -1,
                                                                                                  f"El broker rechazó la conexión ({rc})")
            _LOGGER.warning(f"Error connecting to MQTT {self.config.host}: {self.last_error}")

    def _on_disconnect(self, client, *args, **kwargs):
        self._connected = False

    def _on_message(self, client, userdata, msg):
        try:
            payload = msg.payload.decode("utf-8", "replace")
            if msg.topic == self.birth_topic:
                if payload.strip() == "online":
                    self.reannounce()
                return
            self._on_command(msg.topic, payload)
        except Exception as ex:                                # noqa: BLE001 — the MQTT thread must survive
            _LOGGER.warning(f"MQTT command not processed ({msg.topic}): {ex}")

    async def stop(self) -> None:
        if self._client:
            try:
                if self._connected:
                    self._client.publish(self.availability_topic, "offline", qos=1, retain=True)
            except Exception:                                  # noqa: BLE001
                pass
            self._client.loop_stop()
            try:
                self._client.disconnect()
            except Exception:                                  # noqa: BLE001
                pass
            self._connected = False

    def status(self) -> Dict[str, Any]:
        return {"connected": self._connected, "last_error": self.last_error, "connected_at": self.connected_at.isoformat() if self.connected_at else None,
                "cars": sorted(self._published)}

    # ───────────────────────── publishing ─────────────────────────
    def announce(self, vin: str, name: Optional[str], brand: Optional[str], model: Optional[str], reported_keys: Optional[List[str]] = None,
                 rejected_commands: Optional[List[str]] = None) -> None:
        """Home Assistant discovery for one car (retained, once per connection; again when the car reports new keys or refuses a command)."""
        if not self._connected or not self._client or not self.config.ha_discovery or vin in self._announced:
            return
        for topic, payload in ha_discovery.discovery_messages(self.config.topic_prefix, self.config.ha_prefix, vin, name, brand, model, reported_keys,
                                                              rejected_commands):
            self._client.publish(topic, json.dumps(payload), qos=1, retain=True)
        self._announced.add(vin)
        _LOGGER.info(f"Home Assistant discovery published for {vin[-6:]} on {self.config.host}")

    def reannounce(self) -> None:
        """Publishes the discovery again for every car seen on this connection (Home Assistant restarted, or a device was deleted there)."""
        self._announced = set()
        for vin, facts in list(self._vehicles.items()):
            self.announce(vin, *facts)

    def publish_telemetry(self, vin: str, telemetry: Dict[str, Any], vehicle: Any = None) -> None:
        """Publishes the car's telemetry (JSON + plain topics + location) and, the first time, its discovery."""
        if not self._connected or not self._client:
            return
        self._published.add(vin)
        if vehicle is not None:
            facts = (getattr(vehicle, "name", None), getattr(vehicle, "brand", None), getattr(vehicle, "model", None),
                     list(getattr(vehicle, "reported_keys", None) or []) or None,
                     list(getattr(vehicle, "rejected_commands", None) or []) or None)
            if self._vehicles.get(vin) != facts:
                self._announced.discard(vin)                 # a new key, a refused command or a new name change what the car offers: announce it again
            self._vehicles[vin] = facts
            self.announce(vin, *facts)
        t = ha_discovery.topics(self.config.topic_prefix, vin)
        body = {k: v for k, v in telemetry.items() if k != "raw_data"}
        body["last_contact"] = datetime.datetime.now(datetime.timezone.utc).isoformat()
        if telemetry.get("target_temp") is not None:
            self._last_target_temp[vin] = float(telemetry["target_temp"])
        self._client.publish(t["json"], json.dumps(body, default=str), qos=0, retain=True)
        location = ha_discovery.location_payload(telemetry)
        if location:
            self._client.publish(t["location"], json.dumps(location), qos=0, retain=True)
        prefix = f"{self.config.topic_prefix}/cars/{vin}"
        for key in ("state", "battery_level", "electric_range_km", "total_range_km", "odometer_km", "speed_kmh", "latitude", "longitude",
                    "altitude", "heading", "is_charging", "cable_connected", "is_locked", "climate_on", "inside_temp", "outside_temp"):
            val = telemetry.get(key)
            if val is not None:
                self._client.publish(f"{prefix}/{key}", str(val), qos=0, retain=True)

    # ───────────────────────── commands from Home Assistant ─────────────────────────
    def _on_command(self, topic: str, payload: str) -> None:
        parsed = ha_discovery.command_from(self.config.topic_prefix, topic, payload, None)
        if parsed is None:
            return
        vin, command, params = parsed
        if vin not in self._published:
            _LOGGER.warning(f"MQTT command for a car that is not published ({vin[-6:]}): ignored")
            return
        if command == "clima_on" and params and topic.endswith("climatizacion_modo"):
            params = {"temperature": f"{self._last_target_temp.get(vin, 21.0):.1f}"}
        if self._loop is None:
            return
        if command == "__poll__":
            if self._poll:
                asyncio.run_coroutine_threadsafe(self._poll(vin), self._loop)
            return
        if self._runner:
            asyncio.run_coroutine_threadsafe(self._runner(vin, command, params, "mqtt"), self._loop)


async def test_connection(config: MqttLinkConfig) -> Dict[str, Any]:
    """Tries the broker once, with a timeout, and says what happened. Never keeps the connection."""
    if mqtt is None:
        return {"ok": False, "error": "paho-mqtt no está instalado en el servidor."}
    loop = asyncio.get_running_loop()
    done: asyncio.Future = loop.create_future()
    client = _new_client(f"{config.client_id}_test")
    if config.username:
        client.username_pw_set(config.username, config.password or None)
    if config.use_tls:
        client.tls_set(cert_reqs=ssl.CERT_REQUIRED)

    def on_connect(c, userdata, flags, rc, properties=None):
        code = getattr(rc, "value", rc)
        if not done.done():
            loop.call_soon_threadsafe(done.set_result, code)

    client.on_connect = on_connect
    try:
        client.connect_async(config.host, config.port, 10)
        client.loop_start()
        try:
            code = await asyncio.wait_for(done, TEST_TIMEOUT_S)
        except asyncio.TimeoutError:
            return {"ok": False, "error": "El broker no responde (comprueba host, puerto y cortafuegos)."}
        if code == 0:
            return {"ok": True}
        return {"ok": False, "error": {4: "Usuario o contraseña incorrectos.", 5: "No autorizado."}.get(int(code) if str(code).isdigit() else -1,
                                                                                                          f"El broker rechazó la conexión ({code}).")}
    except (OSError, ValueError) as ex:
        return {"ok": False, "error": f"No se pudo conectar: {str(ex)[:160]}"}
    finally:
        try:
            client.loop_stop()
            client.disconnect()
        except Exception:                                      # noqa: BLE001
            pass


class MqttHub:
    """One `MqttLinkClient` per manager with a saved, enabled link. Cars go to their owner's client."""

    _instance: Optional["MqttHub"] = None

    def __init__(self) -> None:
        self._clients: Dict[int, MqttLinkClient] = {}
        self._runner: Optional[CommandRunner] = None
        self._poll: Optional[Poll] = None

    @classmethod
    def get_instance(cls) -> "MqttHub":
        if cls._instance is None:
            cls._instance = MqttHub()
        return cls._instance

    def configure(self, runner: CommandRunner, poll: Poll) -> None:
        self._runner, self._poll = runner, poll

    async def start(self) -> None:
        async with async_session_factory() as session:
            links = list((await session.execute(select(MqttLink).where(MqttLink.enabled == True))).scalars())  # noqa: E712
        for link in links:
            await self._open(link)

    async def _open(self, link: MqttLink) -> None:
        client = MqttLinkClient(MqttLinkConfig.from_link(link))
        if self._runner and self._poll:
            client.configure(self._runner, self._poll)
        self._clients[link.user_id] = client
        await client.start()

    async def reload_user(self, user_id: int) -> None:
        """The manager saved, enabled, disabled or removed their link."""
        old = self._clients.pop(user_id, None)
        if old:
            await old.stop()
        async with async_session_factory() as session:
            link = (await session.execute(select(MqttLink).where(MqttLink.user_id == user_id, MqttLink.enabled == True))).scalar_one_or_none()  # noqa: E712
        if link:
            await self._open(link)

    async def stop(self) -> None:
        for client in list(self._clients.values()):
            await client.stop()
        self._clients.clear()

    def publish(self, owner_id: Optional[int], vin: str, telemetry: Dict[str, Any], vehicle: Any = None) -> None:
        client = self._clients.get(owner_id) if owner_id is not None else None
        if client:
            client.publish_telemetry(vin, telemetry, vehicle)

    def status(self, user_id: int) -> Optional[Dict[str, Any]]:
        client = self._clients.get(user_id)
        return client.status() if client else None
