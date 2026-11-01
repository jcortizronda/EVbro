"""How often to poll the realtime channel, depending on the car's STATE.

The MQTT channel (doors, lock, cable, engine…) arrives on its own and for free. The realtime
channel (battery, range, high voltage, charge progress…) has to be requested.

**And requesting it does NOT wake the car.** The probe queries the cloud, not the vehicle: with
the car asleep it answers all the same, with `onlineStatus=0` and a snapshot from minutes or hours
ago. For a long time this said that polling "drains the car's 12 V battery", and that is false
—commands do that, since they do wake it—. What polling does cost is traffic against the cloud
and the session, which Chery grants **one per account**: if the component talks a lot, it
competes with the user's official app.

Even so the pace still depends on the state, for two reasons other than the battery: with the
car asleep the cloud returns the same snapshot over and over, and with the car driving the data
changes fast and it is worth asking more often.

Hence the pace is not fixed but a function of the state. This decision used to be spread across
the coordinator's `_poll_state`, `_active_interval_s`, `_burst_active` and `_note_mqtt_burst`,
reading `self` at every step: to test "is plugged + high voltage classified as charging and
not as driving?" a whole coordinator had to be built. Here it is a function of its
arguments.
"""
from __future__ import annotations

from collections import deque
from dataclasses import dataclass
from enum import StrEnum
import time

from .constants import MOVE_BURST_COUNT, MOVE_BURST_WINDOW_S


class PollState(StrEnum):
    """Car state for polling purposes, from highest to lowest priority."""

    CHARGING = "cargando"
    PLUGGED = "enchufado"           # cable connected but not charging yet (HV off)
    MOVING = "en marcha"            # high voltage + MQTT burst = driving
    MOVING_IDLE = "en marcha detenido"  # high voltage without a burst (traffic light, short stop)
    PARKED = "parado"


@dataclass(frozen=True)
class PollIntervals:
    """The five configurable intervals, in MINUTES. 0 = disabled in that state."""

    parked: int
    plugged: int
    charging: int
    moving: int
    moving_idle: int

    def for_state(self, state: PollState) -> int:
        return {
            PollState.CHARGING: self.charging,
            PollState.PLUGGED: self.plugged,
            PollState.MOVING: self.moving,
            PollState.MOVING_IDLE: self.moving_idle,
            PollState.PARKED: self.parked,
        }[state]


@dataclass(frozen=True)
class CarConditions:
    """What is known about the car at the moment of deciding.

    It is passed as data and deliberately not read from `self`: that is what makes the decision
    reproducible in a test without `hass` or MQTT.
    """

    plugged: bool
    charging: bool
    hv_on: bool
    burst_active: bool
    #: the cable has been connected longer than the cap without ever charging
    plugged_timed_out: bool = False


def classify(conditions: CarConditions) -> PollState:
    """Car conditions → polling state. Priority: charging > plugged > moving >
    moving idle > parked.

    Key points: (1) charging also turns on the high voltage → plugged + HV = CHARGING (not
    driving). (2) DRIVING is detected by the MQTT BURST (the car beats "Último contacto"
    while driving) + HV on and cable disconnected. (3) With HV on but NO burst
    (traffic light, or you got out with the car running) → "moving idle", more relaxed.
    (4) HV off → parked.

    **Charging REQUIRES the cable.** `chargeState=1` without a cable connected is not a recharge:
    it is a hybrid recovering energy while driving —regenerative braking or the combustion
    engine— and it happens continuously while driving. Recorded in the field on 2026-08-20/25
    dozens of times, always with `plugged=false` and alternating with "en marcha" every few minutes:

        13:06:19  cargando   cada 900 s     ← driving
        13:09:27  en marcha  cada 180 s
        13:13:12  cargando   cada 900 s     ← driving
        13:14:13  en marcha  cada 180 s

    The effect was that polling relaxed from 3 to 15 minutes RIGHT while driving, which is when
    everything changes fastest. Without a cable, recharging is a drivetrain detail, not a polling
    state."""
    if conditions.plugged and (conditions.charging or conditions.hv_on):
        return PollState.CHARGING
    if conditions.plugged and not conditions.plugged_timed_out:
        return PollState.PLUGGED
    if conditions.hv_on and not conditions.plugged:
        return PollState.MOVING if conditions.burst_active else PollState.MOVING_IDLE
    return PollState.PARKED


def interval_seconds(minutes: int) -> int | None:
    """Configured minutes → seconds, or `None` if polling is disabled in that state.

    `None` is NOT an error: it means "ask nothing until the next free MQTT event", which is the
    default with the car parked."""
    return minutes * 60 if minutes and minutes > 0 else None


class BurstTracker:
    """DRIVING detector based on bursts of MQTT messages.

    While driving, the car emits "Último contacto" continuously; parked it sends nothing. ≥
    `MOVE_BURST_COUNT` messages in the last `MOVE_BURST_WINDOW_S` seconds = moving.

    Thread-safe by usage design: both HA's event loop and the paho thread query it, but its
    owner (the coordinator) protects it with its own lock. Old timestamps are pruned on every
    query, so no timer is needed to clean it up.
    """

    def __init__(self, count: int = MOVE_BURST_COUNT, window_s: int = MOVE_BURST_WINDOW_S) -> None:
        self._count = count
        self._window_s = window_s
        self._timestamps: deque[float] = deque()

    def _prune(self, now: float) -> None:
        cutoff = now - self._window_s
        while self._timestamps and self._timestamps[0] < cutoff:
            self._timestamps.popleft()

    def record(self, now: float | None = None) -> bool:
        """Record a message's timestamp and return whether there is a burst NOW."""
        now = time.time() if now is None else now
        self._timestamps.append(now)
        self._prune(now)
        return len(self._timestamps) >= self._count

    def is_active(self, now: float | None = None) -> bool:
        """Is there a burst right now? Prunes old timestamps."""
        now = time.time() if now is None else now
        self._prune(now)
        return len(self._timestamps) >= self._count
