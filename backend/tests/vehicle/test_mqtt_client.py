"""Tests for `vehicle/mqtt_client.py` — the lifecycle of the paho client.

What is tested here is the STATE the client reports upwards, not paho. It matters
because the case that prompted these tests —connection accepted, subscription denied— is
invisible from outside: the coordinator saw `car_connected: true` and not a single message,
exactly as if the car were asleep.
"""

from __future__ import annotations

import ssl
from unittest.mock import MagicMock, patch

import pytest

from app.vehicle.mqtt_client import EbroMqttClient, MqttConfig

CONFIG = MqttConfig(
    host="broker.example", port=8083, tuserid="U123", channel_id="4", certs_dir="/certs"
)


@pytest.fixture
def cliente_paho():
    """A `paho.mqtt.Client` double that keeps the callbacks assigned to it."""
    fake = MagicMock()
    with (
        patch("paho.mqtt.client.Client", return_value=fake),
        patch(
            "app.vehicle.certificates.tls_paths",
            return_value={"ca_certs": "ca", "certfile": "c", "keyfile": "k"},
        ),
    ):
        yield fake


def _conectar(cliente_paho, **callbacks):
    defaults = {
        "on_message": lambda payload, topic: None,
        "on_connected": lambda ok, rc: None,
        "on_disconnected": lambda rc: None,
    }
    client = EbroMqttClient(CONFIG, **{**defaults, **callbacks})
    client.connect()
    return client


def test_identity_required_by_the_broker_acl() -> None:
    """Client id and topic are in the broker ACL: changing the format means rejection.

    The broker suffix the app uses (`-ssl<host and port>`) was TRIED and discarded: with it
    the broker dropped the connection in a loop (9 connections and 9 disconnections of 0
    seconds, without getting to subscribe). It looks at the clientId, and the app's format
    does not work for us."""
    assert CONFIG.client_id == "app_4_U123"
    assert CONFIG.topic == "app/4/U123/account/msgCenter/msg"


def test_tls_verifies_the_broker_name(cliente_paho) -> None:
    """The connection carries the MQTT login and the telemetry (GPS included): without checking
    the name, any certificate signed by the bundle CA would pass for the broker."""
    _conectar(cliente_paho)

    _, kwargs = cliente_paho.tls_set.call_args
    assert kwargs["cert_reqs"] == ssl.CERT_REQUIRED
    assert kwargs["tls_version"] == ssl.PROTOCOL_TLS_CLIENT
    cliente_paho.tls_insecure_set.assert_not_called()


def test_connecting_subscribes_the_topic(cliente_paho) -> None:
    _conectar(cliente_paho)

    cliente_paho.on_connect(cliente_paho, None, None, 0)

    cliente_paho.subscribe.assert_called_once_with(CONFIG.topic, qos=1)


def test_a_granted_subscription_is_reported_as_ok(cliente_paho) -> None:
    resultados: list[tuple] = []
    _conectar(cliente_paho, on_subscribed=lambda ok, detail, topic: resultados.append((ok, detail, topic)))
    cliente_paho.on_connect(cliente_paho, None, None, 0)

    cliente_paho.on_subscribe(cliente_paho, None, 1, [1])

    assert resultados == [(True, "1", CONFIG.topic)]


def test_a_denied_subscription_is_distinguished_from_being_connected(cliente_paho) -> None:
    """0x80 = the broker accepts the connection and DENIES the topic. Without this report the
    integration stays connected and silent forever, with nothing to give it away."""
    resultados: list[tuple] = []
    _conectar(cliente_paho, on_subscribed=lambda ok, detail, topic: resultados.append((ok, detail, topic)))
    cliente_paho.on_connect(cliente_paho, None, None, 0)

    cliente_paho.on_subscribe(cliente_paho, None, 1, [0x80])

    assert resultados == [(False, "128", CONFIG.topic)]


def test_an_empty_suback_is_not_taken_as_granted(cliente_paho) -> None:
    """Without codes there is no grant to accept: it is an "I don't know", and `all()` over an
    empty list would have said yes."""
    resultados: list[tuple] = []
    _conectar(cliente_paho, on_subscribed=lambda ok, detail, topic: resultados.append((ok, detail, topic)))
    cliente_paho.on_connect(cliente_paho, None, None, 0)

    cliente_paho.on_subscribe(cliente_paho, None, 1, [])

    assert resultados == [(False, "sin respuesta", CONFIG.topic)]


def test_a_rejected_connection_does_not_subscribe(cliente_paho) -> None:
    conexiones: list[tuple] = []
    _conectar(cliente_paho, on_connected=lambda ok, rc: conexiones.append((ok, rc)))

    cliente_paho.on_connect(cliente_paho, None, None, 5)   # 5 = not authorised

    assert conexiones == [(False, 5)]
    cliente_paho.subscribe.assert_not_called()


# ───────────────────── topic discovery ─────────────────────
# The known topic was deduced from the APK. With a secondary account nothing arrives there, but
# the official app —same account— reflects unlocks instantly: something is being published
# elsewhere. The wildcard is for finding out where, and it is only requested with the
# diagnostics monitor on.

DESCUBRIR = MqttConfig(
    host="broker.example", port=8083, tuserid="U123", channel_id="4",
    certs_dir="/certs", discovery=True,
)


def test_the_wildcard_hangs_off_the_account_not_the_root() -> None:
    """`#` over the account's own prefix: nothing foreign is asked for, only what already belongs to the user."""
    assert DESCUBRIR.discovery_topic == "app/4/U123/#"
    assert DESCUBRIR.discovery_topic.startswith("app/4/U123/")


def test_without_discovery_only_the_known_topic_is_subscribed(cliente_paho) -> None:
    _conectar(cliente_paho)

    cliente_paho.on_connect(cliente_paho, None, None, 0)

    cliente_paho.subscribe.assert_called_once_with(CONFIG.topic, qos=1)


def test_with_discovery_the_wildcard_is_subscribed(cliente_paho) -> None:
    client = EbroMqttClient(
        DESCUBRIR, on_message=lambda p, t: None,
        on_connected=lambda ok, rc: None, on_disconnected=lambda rc: None,
    )
    client.connect()

    cliente_paho.on_connect(cliente_paho, None, None, 0)

    cliente_paho.subscribe.assert_called_once_with(DESCUBRIR.discovery_topic, qos=1)


def test_if_the_acl_denies_the_wildcard_it_falls_back_to_the_known_topic(cliente_paho) -> None:
    """Asking for too much cannot leave us WITHOUT a subscription: if the ACL does not allow
    listening to the whole account, discovery is dropped and the integration keeps working as usual."""
    resultados: list[tuple] = []
    client = EbroMqttClient(
        DESCUBRIR, on_message=lambda p, t: None, on_connected=lambda ok, rc: None,
        on_disconnected=lambda rc: None,
        on_subscribed=lambda ok, detail, topic: resultados.append((ok, detail, topic)),
    )
    client.connect()
    cliente_paho.on_connect(cliente_paho, None, None, 0)

    cliente_paho.on_subscribe(cliente_paho, None, 1, [0x80])      # wildcard denied
    cliente_paho.subscribe.assert_called_with(DESCUBRIR.topic, qos=1)
    # the rejection is REPORTED even though a retry follows: otherwise the diagnostics file
    # only kept the fallback's "Granted" and it looked like the wildcard had worked
    assert resultados == [(False, "128", DESCUBRIR.discovery_topic)]

    cliente_paho.on_subscribe(cliente_paho, None, 2, [1])          # the known one is granted
    assert resultados[-1] == (True, "1", DESCUBRIR.topic)


def test_the_message_arrives_with_its_topic(cliente_paho) -> None:
    """Without the topic there is no way to know where it came in: it was the missing piece."""
    recibidos: list[tuple] = []
    _conectar(cliente_paho, on_message=lambda p, t: recibidos.append((p, t)))

    msg = MagicMock()
    msg.payload = b'{"x":1}'
    msg.topic = "app/4/U123/otra/cosa"
    cliente_paho.on_message(cliente_paho, None, msg)

    assert recibidos == [(b'{"x":1}', "app/4/U123/otra/cosa")]
