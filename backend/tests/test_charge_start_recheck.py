"""A charge that is starting is read again shortly: the cloud lags a few seconds behind the car, and a plugged car is otherwise
read every 30 minutes (26-11-2026: charge at 02:00, notice at 02:30)."""
from __future__ import annotations

import pytest

from app.services import poller as poller_module
from app.services.poller import VehiclePoller

VIN = "LSJA0000000000081"
STARTING = {"cable_connected": True, "is_charging": False, "charge_power_kw": 0.67, "remaining_charge_time_min": 378}


@pytest.fixture
def asked(monkeypatch):
    calls = []
    monkeypatch.setattr(VehiclePoller, "_poll_later", lambda self, vin, seconds: calls.append((vin, seconds)))
    VehiclePoller.get_instance()._charge_start_rechecks.pop(VIN, None)
    yield calls
    VehiclePoller.get_instance()._charge_start_rechecks.pop(VIN, None)


def test_a_plugged_car_that_announces_a_time_left_but_is_not_charging_yet_is_read_in_two_minutes(asked) -> None:
    assert VehiclePoller.get_instance()._watch_charge_start(VIN, dict(STARTING)) is True
    assert asked == [(VIN, poller_module.CHARGE_START_RECHECK_SECONDS)] and poller_module.CHARGE_START_RECHECK_SECONDS == 120


@pytest.mark.parametrize("telemetry", [
    {"cable_connected": True, "is_charging": True, "charge_power_kw": 2.64, "remaining_charge_time_min": 150},    # already charging
    {"cable_connected": True, "is_charging": False, "charge_power_kw": 0.0, "remaining_charge_time_min": None},   # plugged and idle
    {"cable_connected": True, "is_charging": False, "charge_power_kw": 0.0, "remaining_charge_time_min": 0},
    # plugged with the climate on: 3.6 kW leave the battery and no charge is coming (26-11-2026)
    {"cable_connected": True, "is_charging": False, "charge_power_kw": 3.6, "remaining_charge_time_min": None},
    {"cable_connected": False, "is_charging": False, "charge_power_kw": 18.0, "remaining_charge_time_min": None},  # on the road
    {"cable_connected": False, "is_charging": True, "charge_power_kw": 22.0, "remaining_charge_time_min": 40},    # no cable
])
def test_nothing_is_asked_when_no_charge_is_starting(asked, telemetry) -> None:
    assert VehiclePoller.get_instance()._watch_charge_start(VIN, telemetry) is False
    assert asked == []


def test_a_car_that_announces_a_charge_that_never_comes_is_not_read_every_two_minutes_for_ever(asked) -> None:
    p = VehiclePoller.get_instance()
    results = [p._watch_charge_start(VIN, dict(STARTING)) for _ in range(6)]
    assert results == [True] * poller_module.CHARGE_START_RECHECKS + [False] * (6 - poller_module.CHARGE_START_RECHECKS)
    # the episode ends (it charges, or stops announcing it) and the next one is watched again
    p._watch_charge_start(VIN, {"cable_connected": True, "is_charging": True, "charge_power_kw": 2.6})
    assert p._watch_charge_start(VIN, dict(STARTING)) is True


def test_forgetting_a_car_drops_its_count(asked) -> None:
    p = VehiclePoller.get_instance()
    p._watch_charge_start(VIN, dict(STARTING))
    p.forget_vehicle(VIN)
    assert VIN not in p._charge_start_rechecks
