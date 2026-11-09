"""Car states from the high voltage, trip end time and the distance rule (odometer only)."""
from __future__ import annotations

import pytest

from app.core.telemetry import parse_telemetry_data
from app.services.trip_detector import resolve_distance


def state(**raw):
    return parse_telemetry_data({"onlineStatus": "1", **raw})["state"]


# ───────────────────────── States ─────────────────────────
@pytest.mark.parametrize("raw,expected", [
    ({"hVoltageState": "1", "vehicleSpeed": "40"}, "driving"),                       # on and moving
    ({"hVoltageState": "1", "vehicleSpeed": "0"}, "ready"),                          # on and stopped (traffic light, ready)
    ({"hVoltageState": "1", "vehicleSpeed": "0", "engineState": "1"}, "ready"),
    ({"hVoltageState": "0", "vehicleSpeed": "0"}, "parked"),                         # off
    ({"hVoltageState": "0", "vehicleSpeed": "30", "engineState": "1"}, "parked"),     # stale reading: with HV off it does not count
    ({"hVoltageState": "0", "onlineStatus": "0"}, "asleep"),
    ({"hVoltageState": "1", "chargeGunState": "1"}, "plugged"),                      # the cable wins over the ignition
    ({"hVoltageState": "0", "chargeGunState": "1", "chargeState": "1"}, "charging"),
    ({"vehicleSpeed": "40"}, "driving"),                                             # no high-voltage data: old rule
    ({"engineState": "1"}, "driving"),
    ({}, "parked"),
])
def test_state_from_the_high_voltage(raw, expected) -> None:
    assert state(**raw) == expected


def test_without_connection_or_data_the_car_is_asleep() -> None:
    assert parse_telemetry_data({"onlineStatus": "0"})["state"] == "asleep"


# ───────────────────────── Distance ─────────────────────────
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


@pytest.mark.parametrize("raw, expected", [("0", "Desactivada"), ("1", "Activa"), ("2", "En ejecución"), ("4", "Finalizada"), ("7", "Desconocido (7)")])
def test_the_scheduled_charge_state_has_a_name_for_the_end_of_a_schedule(raw, expected) -> None:
    """4 arrives right after a scheduled charge stops (2 → 4 → 0; three cars, 08-11-2026): it used to show as «Desconocido (4)»."""
    from app.core.telemetry import parse_telemetry_data
    assert parse_telemetry_data({"appointmentChargeState": raw})["appointment_charge_desc"] == expected
