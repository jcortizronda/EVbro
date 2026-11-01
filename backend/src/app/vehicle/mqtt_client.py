"""The car's mutual-TLS MQTT client (paho, on its own thread).

The coordinator has no business knowing that paho is underneath, how the broker password is
derived, or which topic must be subscribed. All of that, and only that, lives here: connect,
subscribe, deliver raw messages and disconnect cleanly.

**The thread matters.** paho runs its own loop on a separate thread, so the callbacks are NOT
on the asyncio event loop: whoever receives them must hop onto the loop before touching
anything in the server. This module does not do it on its own — it only delivers, and its user
(`services/car_events.py`) decides.
"""
from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
import hashlib
import logging
import os
import ssl

from .constants import (
    CAR_SEED,
    MQTT_KEEPALIVE_S,
    MQTT_RECONNECT_MAX_S,
    MQTT_RECONNECT_MIN_S,
)
from . import certificates

_LOGGER = logging.getLogger(__name__)


class MqttConnectError(Exception):
    """The initial connect to the broker failed (DNS, certificate, network)."""


@dataclass(frozen=True)
class MqttConfig:
    """What is needed to talk to THIS vehicle's broker."""

    host: str
    port: int
    tuserid: str
    channel_id: str
    certs_dir: str
    #: Subscribe to the WHOLE topic space of this account instead of only the known one.
    #: Used to discover where whatever does not arrive on `topic` comes from. See `discovery_topic`.
    discovery: bool = False

    @property
    def client_id(self) -> str:
        """`app_<channel>_<tuserid>`. **It is NOT the same one the official app uses.**

        This used to say it was "the EXACT format the broker ACL expects". That is false, and it
        is worth writing down: the app's memory dump (Chery Vehicle SDK on top of paho) shows
        that its own adds a suffix derived from the broker URL —

            app_4_<tuserid>-ssltspemqx-app-euebroautocom8083

        i.e. `ssl://<host>:<port>` without the non-alphanumeric characters. The broker accepts
        ours all the same, so no ACL requires the suffix.

        And the difference is DESIRABLE: MQTT requires the clientId to be unique, and two clients
        with the same one kick each other out.

        **TRIED AND DISCARDED (2026-10-01).** The app's exact format was used —
        `app_<channel>_<tuserid>-ssl<host and port without symbols>` — as the last known
        difference between its client and ours, with a delegated account that subscribes fine
        and receives nothing. Result measured live: **9 connections and 9 disconnections with 0
        seconds of life**, without even getting to subscribe. The broker DOES look at the
        clientId, and the app's format does not work for us — with ours the connection is stable.

        Written down so nobody tries it again thinking it is harmless."""
        return f"app_{self.channel_id}_{self.tuserid}"

    @property
    def topic(self) -> str:
        return f"app/{self.channel_id}/{self.tuserid}/account/msgCenter/msg"

    @property
    def discovery_topic(self) -> str:
        """Wildcard over THIS account's topic space.

        The known topic was deduced from the APK and is the only one the integration listens
        to. With a secondary account nothing arrives there, yet the official app —with that same
        account— does reflect unlocks instantly: something is being published elsewhere.

        `#` hangs off the account's own prefix, not the broker root: nothing foreign is asked
        for, only what already belongs to this user. If the ACL still denies it, the SUBACK
        will say so and we fall back to the exact topic."""
        return f"app/{self.channel_id}/{self.tuserid}/#"

    @property
    def password(self) -> str:
        """Broker password: md5(tuserid + app seed).

        The seed is a shared app constant (not a user secret); it can be overridden through
        the environment for diagnostics only."""
        seed = os.environ.get("CAR_SEED", CAR_SEED)
        return hashlib.md5((self.tuserid + seed).encode()).hexdigest()


class EbroMqttClient:
    """Lifecycle of the paho client. All its methods are BLOCKING: run in an executor.

    The four callbacks are invoked **from the paho thread**:

    * `on_message(payload: bytes, topic: str)` — one message, uninterpreted;
    * `on_connected(ok: bool, rc)` — result of a connection attempt;
    * `on_subscribed(ok: bool, detail, topic)` — result of the SUBSCRIBE, with the requested topic;
    * `on_disconnected(rc)` — the session dropped.
    """

    def __init__(
        self,
        config: MqttConfig,
        *,
        on_message: Callable[[bytes, str], None],
        on_connected: Callable[[bool, object], None],
        on_disconnected: Callable[[object], None],
        on_subscribed: Callable[[bool, str, str], None] = lambda ok, detail, topic: None,
    ) -> None:
        self._config = config
        self._on_message = on_message
        self._on_connected = on_connected
        self._on_disconnected = on_disconnected
        self._on_subscribed = on_subscribed
        self._client = None
        #: which topic the last SUBSCRIBE asked for, to know what the SUBACK answers
        self._requested: str | None = None

    @property
    def connected(self) -> bool:
        return self._client is not None

    def connect(self) -> None:
        """Connect and start the paho loop. Blocking: run in an executor.

        A failure of the initial connect (DNS/certificate/network) becomes `MqttConnectError`
        so the caller retries, instead of leaving the client hanging."""
        # import here (executor): at module level it causes a blocking-call warning on the loop.
        import paho.mqtt.client as mqtt

        cfg = self._config
        client = mqtt.Client(
            callback_api_version=mqtt.CallbackAPIVersion.VERSION2,
            client_id=cfg.client_id, protocol=mqtt.MQTTv311, clean_session=False)
        client.username_pw_set(cfg.tuserid, cfg.password)
        # Full verification: bundle CA + broker name. The broker certificate has an odd CN
        # (`CN=CA`), but its SAN is the host, which is what `ssl` checks.
        # Never `tls_insecure_set(True)`: it would accept any certificate signed by that CA.
        client.tls_set(**certificates.tls_paths(cfg.certs_dir),
                       cert_reqs=ssl.CERT_REQUIRED, tls_version=ssl.PROTOCOL_TLS_CLIENT)

        def _on_connect(cl, _userdata, _flags, rc, _props=None):
            ok = (rc == 0) or (getattr(rc, "value", 1) == 0)
            _LOGGER.info("[auto] MQTT on_connect rc=%s → %s (sub %s)",
                         rc, "conectado" if ok else "RECHAZADO", cfg.topic if ok else "-")
            if ok:
                self._requested = cfg.discovery_topic if cfg.discovery else cfg.topic
                cl.subscribe(self._requested, qos=1)
            self._on_connected(ok, rc)

        def _on_disconnect(_cl, _userdata, *args):
            _LOGGER.info("[auto] MQTT desconectado")
            # The signature varies between paho versions: rc may be the first or the second
            # positional. It is normalised here so the caller does not need to know.
            rc = args[1] if len(args) > 1 else (args[0] if args else None)
            self._on_disconnected(rc)

        def _on_subscribe(_cl, _userdata, _mid, reason_codes, _props=None):
            """Result of the SUBSCRIBE. A broker can ACCEPT the connection and DENY the topic.

            Without this, that case is indistinguishable from "the car has said nothing": the
            integration stays connected and silent forever, and the diagnostics report shows a
            reassuring `car_connected: true` that does not mean what it seems."""
            # MQTT 3.1.1: 0/1/2 = granted QoS, 0x80 = rejected. paho VERSION2 wraps them
            # in ReasonCode, which compares and prints as its value.
            granted = [str(rc) for rc in (reason_codes or [])]
            ok = bool(reason_codes) and all(
                getattr(rc, "value", rc) != 0x80 for rc in reason_codes
            )
            detail = ", ".join(granted) or "sin respuesta"
            pedido, self._requested = self._requested, None
            if ok:
                _LOGGER.info("[auto] MQTT suscrito a %s (QoS %s)", pedido, detail)
            elif pedido == cfg.discovery_topic:
                # The ACL does not allow listening to the whole account: not a failure, discovery
                # is just not allowed. Fall back to the known topic so we are not left WITHOUT a
                # subscription for having asked for too much.
                _LOGGER.warning(
                    "[auto] MQTT: descubrimiento denegado en %s (%s) → vuelvo al topic conocido",
                    pedido, detail)
                # REPORT BEFORE retrying. Falling back here silently left the rejection only in
                # the log: the diagnostics file showed a single
                # "Granted QoS 1" and it looked like the wildcard had worked. It cost a wrong
                # reading of a field capture — the conclusion was "I listen to the whole account
                # and nothing arrives" when the truth was "I am not allowed to listen to it".
                self._on_subscribed(False, detail, pedido)
                self._requested = cfg.topic
                _cl.subscribe(cfg.topic, qos=1)
                return
            else:
                _LOGGER.error(
                    "[auto] MQTT SUSCRIPCIÓN RECHAZADA a %s (%s): la conexión está viva pero el "
                    "coche no podrá entregar telemetría", pedido, detail)
            # the topic goes in the report: without it, a "Granted QoS 1" does not say whether the
            # wildcard or the exact fallback topic was granted — and those are opposite conclusions.
            self._on_subscribed(ok, detail, pedido or "")

        def _on_message(_cl, _userdata, msg):
            self._on_message(msg.payload, msg.topic)

        client.on_connect = _on_connect
        client.on_disconnect = _on_disconnect
        client.on_subscribe = _on_subscribe
        client.on_message = _on_message
        # [H4] reconnect backoff: paho's default is a fixed 1 s, i.e. one attempt per second
        # forever while the network is down — exactly the pattern that gateways penalise.
        client.reconnect_delay_set(min_delay=MQTT_RECONNECT_MIN_S, max_delay=MQTT_RECONNECT_MAX_S)
        try:
            client.connect(cfg.host, cfg.port, keepalive=MQTT_KEEPALIVE_S)
        except Exception as err:
            raise MqttConnectError(
                f"conexión MQTT al coche fallida ({cfg.host}:{cfg.port}): {err}"
            ) from err
        client.loop_start()
        self._client = client

    def disconnect(self) -> None:
        """Clean shutdown. Blocking (`loop_stop` joins the thread) → run in an executor.

        `disconnect()` BEFORE `loop_stop()`: that way the loop processes the DISCONNECT and the
        thread exits without waiting for the keepalive."""
        client, self._client = self._client, None
        if client is None:
            return
        try:
            client.disconnect()
            client.loop_stop()
        except Exception as err:
            _LOGGER.debug("[auto] error al desconectar MQTT: %s", err)
