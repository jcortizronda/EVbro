"""Tests for `telemetry.py` — the telemetry map and the parsing of MQTT messages.

These tests used to live in `test_coordinator.py` and needed a coordinator (and therefore a
`hass`) to exercise functions that depend on neither. Now that payload interpretation is
pure, they are tested directly: what a confirmation is, what a driving heartbeat is and which
fields are vehicle state is decided here, without starting HA.
"""

from __future__ import annotations

import json

import pytest

from app.vehicle.constants import MAX_STATUS_LEN
from app.vehicle.telemetry import (
    CMD_CONFIRM_META,
    content_fingerprint,
    format_command_result,
    geo_only,
    is_unit_flag,
    parse_car_message,
    unknown_fields,
)


def _payload(service_type: str, data: dict) -> bytes:
    """An MQTT message from the car, with the real envelope."""
    return json.dumps({"content": {"serviceType": service_type, "data": data}}).encode()


# ───────────────────────── content fingerprint ─────────────────────────


def test_content_fingerprint_ignores_clock_fields_at_any_depth() -> None:
    """Without this, two responses with identical data would always look "changed" and the
    freshness sensor would move on every reading."""
    con_relojes = {
        "dumpEnergy": "80",
        "resultTime": "2026-01-15T12:00:00",
        "anidado": {"collectTime": 1, "odometer": "1"},
        "lista": [{"time": 5, "v": 1}],
    }
    otros_relojes = {
        "dumpEnergy": "80",
        "resultTime": "2026-01-15T18:30:00",
        "anidado": {"collectTime": 99, "odometer": "1"},
        "lista": [{"time": 7, "v": 1}],
    }
    dato_distinto = {**con_relojes, "dumpEnergy": "79"}

    assert content_fingerprint(con_relojes) == content_fingerprint(otros_relojes)
    assert content_fingerprint(con_relojes) != content_fingerprint(dato_distinto)


def test_content_fingerprint_ignores_key_order() -> None:
    assert content_fingerprint({"a": 1, "b": 2}) == content_fingerprint({"b": 2, "a": 1})


# ───────────────────────── unit flags ─────────────────────────


@pytest.mark.parametrize(
    ("key", "es_unidad"),
    [
        ("rangeUnit", True),
        ("averageFuelUnit", True),
        ("tirePressureUnit", True),
        ("dumpEnergy", False),
        ("unitario", False),
    ],
)
def test_is_unit_flag(key: str, es_unidad: bool) -> None:
    """The `*Unit` fields are always 1 or 2 and only say WHICH unit the homonymous field is in.

    Filtering them stops field auto-discovery from flagging them as "to be mapped": pure
    noise that hid the truly new fields.
    """
    assert is_unit_flag(key) is es_unidad


# ───────────────────────── command result ─────────────────────────


@pytest.mark.parametrize(
    ("data", "esperado"),
    [
        ({"reason": ["puerta abierta"]}, "El coche ha señalado un problema ❌"),
        ({"result": "5"}, "Comando en ejecución en el coche… ⏳"),
        ({"result": "1"}, "Comando ejecutado y confirmado por el coche ✅"),
        ({"result": "2"}, "Comando ejecutado y confirmado por el coche ✅"),
        ({"result": "99"}, "Confirmación recibida del coche (código 99)"),
        ({}, "Confirmación recibida del coche (código ?)"),
        # `reason` wins over a successful `result`: if the car reports a reason, it failed
        ({"result": "1", "reason": ["fallo"]}, "El coche ha señalado un problema ❌"),
    ],
)
def test_format_command_result(data: dict, esperado: str) -> None:
    assert format_command_result(data).startswith(esperado)


def test_format_command_result_is_truncated() -> None:
    """The value ends up in a sensor state, whose length HA limits."""
    assert len(format_command_result({"reason": ["x" * 500]})) <= MAX_STATUS_LEN


# ───────────────────────── geolocation ─────────────────────────


def test_geo_only_leaves_out_what_is_not_position() -> None:
    """Battery and cable state live in `realtime`, not in the position: storing them here
    (with `**data`) made them travel to the device_tracker."""
    assert geo_only({"lat": "40.4", "lon": "-3.7", "dumpEnergy": "80", "chargeGunState": "1"}) == {
        "lat": "40.4",
        "lon": "-3.7",
    }


# ───────────────────────── message parsing ─────────────────────────


def test_parse_car_message_unreadable_payload_does_not_raise() -> None:
    """Parsing runs on the paho thread: an exception there takes the thread down with it."""
    assert parse_car_message(b"esto no es json") is None
    assert parse_car_message(b"") is None


def test_parse_car_message_separates_state_from_confirmation_metadata() -> None:
    """A confirmation push carries the REAL state fields besides the command metadata.
    The former go to `fields`; the latter are not vehicle state."""
    message = parse_car_message(
        _payload("1105", {"result": "1", "seq": "VIN-123", "hasAsy": "0", "doorLock": "1"})
    )

    assert message.is_confirmation is True
    assert message.state_fields == {"doorLock": "1"}
    for meta in CMD_CONFIRM_META:
        assert meta not in message.state_fields


def test_parse_car_message_pure_telemetry_is_not_a_confirmation() -> None:
    message = parse_car_message(_payload("5A02", {"doorLock": "0", "hood": "0"}))

    assert message.is_confirmation is False
    assert message.meaningful is True
    assert message.state_fields == {"doorLock": "0", "hood": "0"}


def test_parse_car_message_driving_heartbeat_is_not_meaningful() -> None:
    """Only `time` = heartbeat the car emits every few seconds while driving. It must not
    move «Último contacto» nor write to the recorder during the whole trip."""
    message = parse_car_message(_payload("5A02", {"time": "1737000000000"}))

    assert message.meaningful is False
    assert message.state_fields == {}


def test_parse_car_message_position_is_told_apart_by_message_type() -> None:
    """A 5A02 with lat/lon is NOT a position report: the type is what counts."""
    posicion = parse_car_message(_payload("1301", {"lat": "40.4", "lon": "-3.7"}))
    telemetria = parse_car_message(_payload("5A02", {"lat": "40.4", "lon": "-3.7"}))

    assert posicion.geo == {"lat": "40.4", "lon": "-3.7"}
    assert telemetria.geo == {}


def test_parse_car_message_accepts_the_payload_without_envelope() -> None:
    """Some messages already come as content, without the `content` envelope."""
    crudo = json.dumps({"serviceType": "5A02", "data": {"hood": "1"}}).encode()

    assert parse_car_message(crudo).state_fields == {"hood": "1"}


# ───────────────────────── auto-discovery ─────────────────────────


def test_unknown_fields_flags_only_what_is_not_mapped() -> None:
    message = parse_car_message(
        _payload("5A02", {"doorLock": "0", "campoNuevo": "7", "rangeUnit": "1"})
    )

    assert unknown_fields(message) == [("campoNuevo", "7")]


def test_unknown_fields_never_records_a_coordinate() -> None:
    """Real leak seen in the field on 2026-07-20: comparing a POSITION push against `META` made
    lat/lon get recorded as "fields to be mapped" in the diagnostics file."""
    posicion = parse_car_message(_payload("1301", {"lat": "40.4", "lon": "-3.7"}))
    telemetria_con_geo = parse_car_message(_payload("5A02", {"lat": "40.4", "lon": "-3.7"}))

    assert unknown_fields(posicion) == []
    assert unknown_fields(telemetria_con_geo) == []
