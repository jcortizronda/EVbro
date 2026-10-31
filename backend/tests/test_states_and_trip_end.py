"""Estados del coche según la alta tensión, hora de fin del viaje y regla de la distancia (solo el odómetro)."""
from __future__ import annotations

import pytest

from app.core.telemetry import parse_telemetry_data
from app.services.trip_detector import resolve_distance


def estado(**raw):
    return parse_telemetry_data({"onlineStatus": "1", **raw})["state"]


# ───────────────────────── Estados ─────────────────────────
@pytest.mark.parametrize("raw,esperado", [
    ({"hVoltageState": "1", "vehicleSpeed": "40"}, "driving"),                       # encendido y rodando
    ({"hVoltageState": "1", "vehicleSpeed": "0"}, "ready"),                          # encendido y parado (semáforo, listo)
    ({"hVoltageState": "1", "vehicleSpeed": "0", "engineState": "1"}, "ready"),
    ({"hVoltageState": "0", "vehicleSpeed": "0"}, "parked"),                         # apagado
    ({"hVoltageState": "0", "vehicleSpeed": "30", "engineState": "1"}, "parked"),     # lectura vieja: con HV apagada no cuenta
    ({"hVoltageState": "0", "onlineStatus": "0"}, "asleep"),
    ({"hVoltageState": "1", "chargeGunState": "1"}, "plugged"),                      # el cable manda sobre el encendido
    ({"hVoltageState": "0", "chargeGunState": "1", "chargeState": "1"}, "charging"),
    ({"vehicleSpeed": "40"}, "driving"),                                             # sin dato de alta tensión: regla antigua
    ({"engineState": "1"}, "driving"),
    ({}, "parked"),
])
def test_estado_segun_la_alta_tension(raw, esperado) -> None:
    assert estado(**raw) == esperado


def test_sin_conexion_ni_dato_el_coche_esta_dormido() -> None:
    assert parse_telemetry_data({"onlineStatus": "0"})["state"] == "asleep"


# ───────────────────────── Distancia ─────────────────────────
@pytest.mark.parametrize("start,end,expected", [
    (1000.0, 1012.0, 12.0),   # the odometer alone, whole kilometres
    (1000.0, 1000.0, 0.0),    # too short to move the odometer
    (1000.0, 999.0, 0.0),     # a reading going backwards never gives a negative distance
    (1000.4, 1012.6, 12.0),   # a fractional reading is truncated, never rounded up (the next drive gets that kilometre)
    (None, 1012.0, None),     # no odometer: not decided here
    (1000.0, None, None),
])
def test_distance_is_the_odometer_alone(start, end, expected) -> None:
    assert resolve_distance(start, end) == expected
