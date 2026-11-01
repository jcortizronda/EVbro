"""Tests for `poll_policy.py` — how often the realtime channel is polled.

This decision has a real cost when it is wrong: polling does not wake the car (the probe
queries the cloud), but polling too much competes with the user's official app for the single
session Chery's cloud grants per account. It deserves direct tests, and now that the classification is a function
of its arguments they can be written without `hass`, without MQTT and without building a coordinator.
"""

from __future__ import annotations

import pytest

from app.vehicle.poll_policy import (
    BurstTracker,
    CarConditions,
    PollIntervals,
    PollState,
    classify,
    interval_seconds,
)

INTERVALOS = PollIntervals(parked=0, plugged=30, charging=15, moving=3, moving_idle=5)


@pytest.mark.parametrize(
    ("condiciones", "esperado"),
    [
        # Charging also turns on the high voltage: plugged + HV is CHARGING, never driving.
        # This is the confusion that made it poll every 3 minutes during a whole night of charging.
        (CarConditions(plugged=True, charging=False, hv_on=True, burst_active=False),
         PollState.CHARGING),
        (CarConditions(plugged=True, charging=True, hv_on=False, burst_active=False),
         PollState.CHARGING),
        # Cable connected but not charging yet (scheduled charge waiting for its time).
        (CarConditions(plugged=True, charging=False, hv_on=False, burst_active=False),
         PollState.PLUGGED),
        # High voltage + MQTT burst + no cable = driving.
        (CarConditions(plugged=False, charging=False, hv_on=True, burst_active=True),
         PollState.MOVING),
        # High voltage WITHOUT a burst: traffic light, or you got out with the car running.
        (CarConditions(plugged=False, charging=False, hv_on=True, burst_active=False),
         PollState.MOVING_IDLE),
        # RECOVERY while driving: chargeState=1 WITHOUT a cable. A hybrid recharges the battery
        # with regenerative braking or the combustion engine, and that is not a charge to poll.
        # Recorded in the field dozens of times alternating with "en marcha" every few minutes:
        # polling relaxed from 3 to 15 min right while driving.
        (CarConditions(plugged=False, charging=True, hv_on=True, burst_active=True),
         PollState.MOVING),
        (CarConditions(plugged=False, charging=True, hv_on=True, burst_active=False),
         PollState.MOVING_IDLE),
        # ...and without high voltage neither: without a cable there is nothing to charge.
        (CarConditions(plugged=False, charging=True, hv_on=False, burst_active=False),
         PollState.PARKED),
        # High voltage off = parked, whatever happens with the burst.
        (CarConditions(plugged=False, charging=False, hv_on=False, burst_active=True),
         PollState.PARKED),
        (CarConditions(plugged=False, charging=False, hv_on=False, burst_active=False),
         PollState.PARKED),
    ],
)
def test_classify(condiciones: CarConditions, esperado: PollState) -> None:
    assert classify(condiciones) is esperado


def test_plugged_without_charging_cap_returns_to_parked() -> None:
    """A plugged-in car that never starts charging is not polled indefinitely."""
    esperando = CarConditions(plugged=True, charging=False, hv_on=False, burst_active=False)
    vencido = CarConditions(
        plugged=True, charging=False, hv_on=False, burst_active=False, plugged_timed_out=True
    )

    assert classify(esperando) is PollState.PLUGGED
    assert classify(vencido) is PollState.PARKED


def test_the_cap_does_not_affect_a_real_charge_in_progress() -> None:
    """If the car STARTS charging, the waiting cap is irrelevant: charging takes priority."""
    vencido_pero_cargando = CarConditions(
        plugged=True, charging=True, hv_on=True, burst_active=False, plugged_timed_out=True
    )

    assert classify(vencido_pero_cargando) is PollState.CHARGING


@pytest.mark.parametrize(
    ("state", "minutos"),
    [
        (PollState.CHARGING, 15),
        (PollState.PLUGGED, 30),
        (PollState.MOVING, 3),
        (PollState.MOVING_IDLE, 5),
        (PollState.PARKED, 0),
    ],
)
def test_interval_per_state(state: PollState, minutos: int) -> None:
    assert INTERVALOS.for_state(state) == minutos


@pytest.mark.parametrize(
    ("minutos", "segundos"),
    [(15, 900), (3, 180), (1, 60), (0, None), (-1, None)],
)
def test_interval_seconds(minutos: int, segundos: int | None) -> None:
    """`None` is not an error: it means "do not touch the car until the next free MQTT event",
    which is precisely the default with the car parked."""
    assert interval_seconds(minutos) == segundos


# ───────────────────────── driving detection by burst ─────────────────────────


def test_burst_tracker_needs_the_full_threshold() -> None:
    tracker = BurstTracker(count=5, window_s=30)
    ahora = 1_000.0

    for i in range(4):
        assert tracker.record(ahora + i) is False
    assert tracker.record(ahora + 4) is True


def test_burst_tracker_prunes_timestamps_outside_the_window() -> None:
    """Four old messages plus a new one are NOT a burst: the car is parked."""
    tracker = BurstTracker(count=5, window_s=30)
    for i in range(4):
        tracker.record(1_000.0 + i)

    assert tracker.record(1_100.0) is False
    assert tracker.is_active(1_100.0) is False


def test_burst_tracker_turns_off_by_itself_after_the_window() -> None:
    """Without pruning, a trip left the burst "active" forever and the car kept being
    polled at the driving pace with the engine already off."""
    tracker = BurstTracker(count=3, window_s=30)
    for i in range(3):
        tracker.record(1_000.0 + i)

    assert tracker.is_active(1_010.0) is True
    assert tracker.is_active(1_060.0) is False
