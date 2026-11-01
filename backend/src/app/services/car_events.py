"""The car's MQTT channel: instant events and command confirmations.

**What the car sends here** (documented in the Home Assistant integration):
  * `5A02` — state: doors, lock, boot, roof, charging cable, engine…
  * `110x` — command confirmation: the car acknowledges it has run it.
  * `1301` — position. **This car never sends it**; if it ever arrives, it is merged all the same.
  Battery, range and odometer do NOT come this way: they still come from the poll.

**What it is for in EVBro**
  * Door/lock/cable/engine telemetry updates instantly (WebSocket), without waiting
    for the poll. The push wins over the poll when both carry the same key.
  * Waking up, a driving burst and plugging/unplugging the cable trigger an immediate poll, which
    is what saves the snapshot and feeds the trip and charge detectors.
  * Confirmations reach the web as `command_result`.

**Limitations that matter**
  * It only exists for the OWNER account: with a delegated one the broker accepts the subscription but
    publishes nothing (state `subscribed` with no messages).
  * The topic belongs to the account and messages carry no VIN: they are attributed to ONE vehicle (the
    `EBRO_VIN` one or the first). With several vehicles on the account it is an approximation.
  * **clientId clash.** The clientId (`app_<channel>_<tuserid>`) is the same the Home Assistant integration
    uses, and MQTT kicks out the previous client when another one joins with the same id. That is why the
    service is off by default and, if it sees connections lasting seconds one after another,
    it STOPS (`conflict`) instead of entering a reconnection war against Chery's broker.

paho callbacks arrive on paho's thread: here they ALWAYS hop to the event loop before
touching anything, so only one thread changes this service's state.
"""
from __future__ import annotations

import asyncio
import dataclasses
import datetime
import logging
import time
from typing import Any, Callable, Optional

from app.config import get_data_dir, settings
from app.core.wake import _bff_login
from app.vehicle import certificates
from app.vehicle.constants import DEFAULT_AWAKE_WINDOW
from app.vehicle.mqtt_client import EbroMqttClient, MqttConfig, MqttConnectError
from app.vehicle.poll_policy import BurstTracker
from app.vehicle.state import VehicleState
from app.vehicle.telemetry import format_command_result, parse_car_message

_LOGGER = logging.getLogger("app.services.car_events")

# Anti-loop guard: a connection lasting less than FLAP_MIN_UPTIME_S counts as «ephemeral»; if
# FLAP_MAX_EPHEMERAL happen in a row within FLAP_WINDOW_S, another client is using the same
# account (or the broker rejects us) and we stop trying.
FLAP_MIN_UPTIME_S = 20.0
FLAP_MAX_EPHEMERAL = 3
FLAP_WINDOW_S = 300.0

# States shown on the web (stable key) and their explanation.
STATE_TEXT = {
    "disabled": "Desactivado (CAR_MQTT_ENABLED=false)",
    "no_account": "Sin cuenta Ebro conectada",
    "no_certs": "Faltan los certificados mutual-TLS",
    "connecting": "Conectando con el broker del coche…",
    "connected": "Conectado, suscribiendo…",
    "subscribed": "Escuchando al coche",
    "rejected": "El broker ha rechazado la conexión",
    "error": "Error de conexión",
    "conflict": "Parado: otro cliente usa la misma cuenta",
}


class CarEventService:
    _instance: Optional["CarEventService"] = None

    @classmethod
    def get_instance(cls) -> "CarEventService":
        if cls._instance is None:
            cls._instance = CarEventService()
        return cls._instance

    def __init__(self, client_factory: Callable[..., Any] = EbroMqttClient, owner_id: int | None = None) -> None:
        self._client_factory = client_factory
        self.owner_id = owner_id                     # the manager whose Ebro account this service listens for (None = single-account use)
        self._vins: list[str] | None = None          # cars of that manager: the messages are attributed to one of them
        self._poller = None
        self._loop: asyncio.AbstractEventLoop | None = None
        self._client = None
        self.vin: str | None = None
        self.state = "disabled"
        self.detail = ""
        self.vehicle_state = VehicleState(awake_window=DEFAULT_AWAKE_WINDOW)
        self._burst = BurstTracker()
        self._plugged: bool | None = None
        self.messages = 0
        self.last_message_at: float | None = None
        self.connected_since: float | None = None
        self.last_command_result: str | None = None
        self._ephemeral: list[float] = []
        self._topic: str | None = None

    # ───────────────────────── life cycle ─────────────────────────
    async def start(self, poller, vins: list[str] | None = None) -> None:
        """Connects if enabled and there is an account. Never raises: the outcome is left in `self.state`."""
        self._poller = poller
        if vins is not None:
            self._vins = list(vins)
        self._loop = asyncio.get_running_loop()
        if not settings.CAR_MQTT_ENABLED:
            self._set("disabled")
            return
        target = self._pick_vehicle(poller, self._vins)
        if target is None:
            self._set("no_account", "Aún no hay ningún vehículo vinculado.")
            return
        self.vin, ctx = target
        self._set("connecting")
        try:
            await self._loop.run_in_executor(None, self._connect_blocking, ctx)
        except _ServiceError as err:
            self._set(err.state, str(err))
            _LOGGER.warning("Car MQTT channel: %s", err)
        except Exception as err:                     # noqa: BLE001 — the service must not bring down start-up
            self._set("error", f"{type(err).__name__}: {err}")
            _LOGGER.exception("Car MQTT channel: unexpected failure")

    async def stop(self) -> None:
        client, self._client = self._client, None
        if client is not None and self._loop is not None:
            await self._loop.run_in_executor(None, client.disconnect)
        self.connected_since = None
        if self.state not in ("disabled", "conflict"):
            self._set("disabled" if not settings.CAR_MQTT_ENABLED else "no_account",
                      "" if not settings.CAR_MQTT_ENABLED else "Cuenta desconectada.")

    async def restart(self, poller, vins: list[str] | None = None) -> None:
        await self.stop()
        self._ephemeral.clear()
        await self.start(poller, vins)

    @staticmethod
    def _pick_vehicle(poller, vins: list[str] | None = None):
        contexts = poller._contexts
        if vins is not None:                          # only the cars of this service's manager
            contexts = {v: c for v, c in contexts.items() if v in vins}
        if not contexts:
            return None
        vin = settings.EBRO_VIN if settings.EBRO_VIN in contexts else sorted(contexts)[0]
        if len(contexts) > 1:
            _LOGGER.warning(
                "There are %d vehicles on the account and MQTT messages carry no VIN: they are attributed to %s.",
                len(contexts), vin)
        return vin, contexts[vin]

    def _connect_blocking(self, ctx) -> None:
        """Certificates + tUserId + connection. Blocking: runs in the executor."""
        certs_dir = f"{get_data_dir()}/certs"
        ok, detail = certificates.provision(certs_dir, settings.CAR_MQTT_HOST, settings.CAR_MQTT_CERTS_SRC)
        if not ok:
            raise _ServiceError("no_certs", detail)

        _user_token, tuserid = _bff_login(ctx)
        if not tuserid:
            raise _ServiceError("no_account", "La sesión de la cuenta Ebro ha caducado: vuelve a conectarla.")
        ctx.tuserid = str(tuserid)

        config = MqttConfig(host=settings.CAR_MQTT_HOST, port=settings.CAR_MQTT_PORT, tuserid=str(tuserid),
                            channel_id=ctx.channel_id, certs_dir=certs_dir)
        self._topic = config.topic
        client = self._client_factory(
            config,
            on_message=self._from_thread(self._handle_message),
            on_connected=self._from_thread(self._on_connected),
            on_disconnected=self._from_thread(self._on_disconnected),
            on_subscribed=self._from_thread(self._on_subscribed),
        )
        try:
            client.connect()
        except MqttConnectError as err:
            raise _ServiceError("error", str(err)) from err
        self._client = client

    def _from_thread(self, fn):
        """Wraps a paho callback so it runs on the event loop."""
        def _call(*args):
            loop = self._loop
            if loop is not None and not loop.is_closed():
                loop.call_soon_threadsafe(fn, *args)
        return _call

    # ───────────────────────── connection events (on the loop) ─────────────────────────
    def _set(self, state: str, detail: str = "") -> None:
        self.state = state
        self.detail = detail or STATE_TEXT.get(state, "")

    def _on_connected(self, ok: bool, rc) -> None:
        if ok:
            self.connected_since = time.monotonic()
            self._set("connected")
        else:
            self._set("rejected", f"Código de rechazo: {rc}")
            self._note_ephemeral()

    def _on_subscribed(self, ok: bool, detail: str, topic: str = "") -> None:
        if ok:
            self._set("subscribed", f"Suscrito a {topic or self._topic} (QoS {detail})")
        else:
            self._set("rejected", f"Suscripción rechazada ({detail}): ¿es la cuenta propietaria?")

    def _on_disconnected(self, rc) -> None:
        uptime = time.monotonic() - self.connected_since if self.connected_since else None
        self.connected_since = None
        if self.state == "conflict":
            return
        self._set("connecting", f"Desconectado (rc={rc}); reintentando…")
        if uptime is not None and uptime < FLAP_MIN_UPTIME_S:
            self._note_ephemeral()

    def _note_ephemeral(self) -> None:
        now = time.monotonic()
        self._ephemeral = [t for t in self._ephemeral if now - t < FLAP_WINDOW_S] + [now]
        if len(self._ephemeral) >= FLAP_MAX_EPHEMERAL:
            client, self._client = self._client, None
            self._set("conflict", (
                "La conexión se corta a los pocos segundos una y otra vez: otro cliente usa la misma "
                "cuenta (¿la integración de Home Assistant?) o el broker nos rechaza. Se ha parado para "
                "no expulsarse en bucle; desactiva el otro cliente y pulsa «Reconectar»."))
            _LOGGER.error("Car MQTT channel: %s", self.detail)
            if client is not None and self._loop is not None:
                self._loop.run_in_executor(None, client.disconnect)

    # ───────────────────────── messages from the car (on the loop) ─────────────────────────
    def _handle_message(self, payload: bytes, topic: str | None = None) -> None:
        if topic is not None and self._topic is not None and topic != self._topic:
            return                                    # a topic we do not know how to read
        message = parse_car_message(payload)
        if message is None or self._poller is None or self.vin is None:
            return
        now = time.time()
        self.messages += 1
        self.last_message_at = now

        fields, was_awake = self.vehicle_state.record_message(message.state_fields, now)
        if message.geo:
            self.vehicle_state.set_position(message.geo)

        if message.is_confirmation:
            self.last_command_result = format_command_result(message.data)
            self._poller.broadcast_event({
                "type": "command_result", "vin": self.vin, "message": self.last_command_result,
                "applied": "✅" in self.last_command_result, "failed": "❌" in self.last_command_result,
            })
            return                                    # a confirmation is not telemetry

        if message.meaningful:
            self._poller.apply_car_fields(self.vin, fields, message.geo or None)

        # Poll triggers (free: they come from the push). The wake-up edge respects the probe's
        # cooldown; driving and the cable force it.
        was_bursting = self._burst.is_active(now)
        bursting = self._burst.record(now)
        trigger_force = bursting and not was_bursting
        if "chargeGunState" in message.data:
            plugged = str(message.data["chargeGunState"]).strip() in ("1", "1.0", "true")
            if plugged != self._plugged:
                trigger_force = trigger_force or self._plugged is not None
            self._plugged = plugged
        if trigger_force:
            self._poller.request_poll(self.vin, force=True)
        elif not was_awake:
            self._poller.request_poll(self.vin, force=False)

    # ───────────────────────── query ─────────────────────────
    def fields_for(self, vin: str) -> dict[str, str]:
        """Latest known 5A02 telemetry of that vehicle (empty if it is not the one MQTT listens for)."""
        return self.vehicle_state.fields() if vin == self.vin else {}

    def status(self) -> dict:
        now = time.time()
        return {
            "enabled": settings.CAR_MQTT_ENABLED,
            "state": self.state,
            "detail": self.detail or STATE_TEXT.get(self.state, ""),
            "vin": self.vin,
            "messages": self.messages,
            "last_message_at": (datetime.datetime.fromtimestamp(self.last_message_at, datetime.timezone.utc).isoformat()
                                if self.last_message_at else None),
            "awake": self.vehicle_state.is_awake(now),
            "moving": self._burst.is_active(now),
            "last_command_result": self.last_command_result,
        }


class CarEventHub:
    """One MQTT channel per manager: every Ebro account has its own broker session (and, on Chery's side, only one client per account).

    The hub only routes: it keeps a `CarEventService` per manager, starts them for the managers that have connected cars, and answers
    for a given car or manager. A service never sees the cars of another manager."""

    _instance: Optional["CarEventHub"] = None

    @classmethod
    def get_instance(cls) -> "CarEventHub":
        if cls._instance is None:
            cls._instance = CarEventHub()
        return cls._instance

    def __init__(self, service_factory: Callable[..., "CarEventService"] = None) -> None:
        self._factory = service_factory or CarEventService
        self._services: dict[int, CarEventService] = {}

    def service_for(self, owner_id: int) -> "CarEventService":
        if owner_id not in self._services:
            self._services[owner_id] = self._factory(owner_id=owner_id)
        return self._services[owner_id]

    @staticmethod
    async def _vins_of(owner_id: int) -> list[str]:
        from sqlalchemy import select

        from app.db.models import Vehicle
        from app.db.session import async_session_factory
        async with async_session_factory() as session:
            return [v for (v,) in (await session.execute(
                select(Vehicle.vin).where(Vehicle.owner_id == owner_id, Vehicle.is_manual.is_(False)))).all()]

    @staticmethod
    async def _owners_with_cars() -> list[int]:
        from sqlalchemy import select

        from app.db.models import EbroAccount, Vehicle
        from app.db.session import async_session_factory
        async with async_session_factory() as session:
            owners = {o for (o,) in (await session.execute(select(Vehicle.owner_id).where(Vehicle.is_manual.is_(False)))).all() if o is not None}
            with_account = {u for (u,) in (await session.execute(select(EbroAccount.user_id).where(EbroAccount.is_active.is_(True)))).all()}
        return sorted(owners & with_account)

    async def start(self, poller) -> None:
        """Starts the channel of every manager that has an Ebro account with cars. One at a time: connecting is slow and talks to Chery."""
        for owner_id in await self._owners_with_cars():
            await self.service_for(owner_id).start(poller, await self._vins_of(owner_id))

    async def stop(self) -> None:
        for service in list(self._services.values()):
            await service.stop()

    async def restart_owner(self, owner_id: int, poller) -> None:
        await self.service_for(owner_id).restart(poller, await self._vins_of(owner_id))

    async def stop_owner(self, owner_id: int) -> None:
        if owner_id in self._services:
            await self._services[owner_id].stop()

    def fields_for(self, vin: str) -> dict[str, str]:
        """Latest 5A02 telemetry of that car, from the service that listens for it (empty if none does)."""
        for service in self._services.values():
            if service.vin == vin:
                return service.fields_for(vin)
        return {}

    def status_for(self, owner_id: int) -> dict:
        return self.service_for(owner_id).status()


@dataclasses.dataclass
class _ServiceError(Exception):
    state: str
    message: str

    def __str__(self) -> str:
        return self.message
