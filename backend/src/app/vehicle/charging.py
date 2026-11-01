"""Car charge scheduling: build plans, local↔UTC times and read what the car has.

Comes from `vehicle/charging.py` in the Home Assistant integration, without the `ChargeLimiter` (the decision of when
to stop is taken by `services/charge_manager.py`) and with `zoneinfo` instead of the Home Assistant utilities.
Everything is pure: the time zone and the instant are passed as arguments, so it is tested without a clock or network.

**What you need to know about the car**
  * `chargeStartStopControl` answers `A00084`: there is no direct start/stop. Only the schedule exists.
  * `startTime` is interpreted in **UTC** (verified in the field: at UTC+2 a charge set for 03:00
    started at 05:00). `timeConsuming` is a duration and is not converted.
  * The car requires a minimum duration of 1 h; less gives code 89.
  * Stopping = imposing a schedule whose window is already over. That window "opens" again the next day
    at the same time, hence `hold_needs_refresh`: it must be moved before then.
"""
from __future__ import annotations

import datetime
from dataclasses import dataclass
from zoneinfo import ZoneInfo

from .constants import (
    CHARGE_MIN_DURATION_MIN,
    CHARGE_STOP_DURATION_MIN,
    CHARGE_STOP_START_BACK_MIN,
    MINUTES_PER_DAY,
)

# Every day of the week: the plan always applies and the main switch decides whether it is active.
ALL_WEEKDAYS = [1, 2, 3, 4, 5, 6, 7]

# How many minutes before the pause window reopens it gets moved. With the window at
# [now−90, now−30] the next opening is 22 h 30 away; it is renewed when less than 4 h remain.
HOLD_REFRESH_MARGIN_MIN = 240


def _utcnow() -> datetime.datetime:
    return datetime.datetime.now(datetime.timezone.utc)


def local_minutes_to_utc(local_mins: int, tz: ZoneInfo, now: datetime.datetime | None = None) -> int:
    """Minutes since midnight in LOCAL time → minutes since midnight in UTC.

    TODAY's date is used so the offset follows the current summer/winter time."""
    local_mins = int(local_mins) % MINUTES_PER_DAY
    hh, mm = divmod(local_mins, 60)
    now = (now or _utcnow()).astimezone(tz)
    local_dt = now.replace(hour=hh, minute=mm, second=0, microsecond=0)
    utc_dt = local_dt.astimezone(datetime.timezone.utc)
    return utc_dt.hour * 60 + utc_dt.minute


def utc_minutes_to_local(utc_mins: int, tz: ZoneInfo, now: datetime.datetime | None = None) -> int:
    """The inverse: what the car stores → wall clock (to SHOW its schedule)."""
    utc_mins = int(utc_mins) % MINUTES_PER_DAY
    hh, mm = divmod(utc_mins, 60)
    utc_dt = (now or _utcnow()).astimezone(datetime.timezone.utc).replace(hour=hh, minute=mm, second=0, microsecond=0)
    local_dt = utc_dt.astimezone(tz)
    return local_dt.hour * 60 + local_dt.minute


@dataclass(frozen=True)
class ChargeSchedule:
    """The charge schedule AS THE CAR HAS IT (the official app may have changed it)."""

    enabled: bool
    start_minutes: int | None      # local wall clock
    duration_minutes: int | None
    days: tuple[int, ...] = ()

    @property
    def end_minutes(self) -> int | None:
        if self.start_minutes is None or self.duration_minutes is None:
            return None
        return (self.start_minutes + self.duration_minutes) % MINUTES_PER_DAY


def _as_int(value) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def parse_schedule(payload: dict | None, tz: ZoneInfo, now: datetime.datetime | None = None) -> ChargeSchedule | None:
    """`chargeAppointQuery` response → `ChargeSchedule`, or `None` if it cannot be interpreted.

    `mainSwitch` (main) and `switchStatus` (of the plan) are different things and BOTH must be on for
    the car to charge on schedule. The car returns a list of plans; the first one is taken."""
    if not isinstance(payload, dict):
        return None
    planes = payload.get("chargeAppointPlans")
    plan = planes[0] if isinstance(planes, list) and planes and isinstance(planes[0], dict) else {}
    inicio = _as_int(plan.get("startTime"))
    dias = plan.get("cycleData")
    return ChargeSchedule(
        enabled=_as_int(payload.get("mainSwitch")) == 1 and _as_int(plan.get("switchStatus")) == 1,
        start_minutes=None if inicio is None else utc_minutes_to_local(inicio, tz, now),
        duration_minutes=_as_int(plan.get("timeConsuming")),
        days=tuple(int(d) for d in dias) if isinstance(dias, list) else (),
    )


def build_plan(start_minutes: int, duration_minutes: int, switch_status: int, tz: ZoneInfo,
               now: datetime.datetime | None = None) -> dict:
    """A `chargeAppointPlans` plan. `startTime` in UTC; the duration is clamped to the car's minimum."""
    return {
        "cycleData": ALL_WEEKDAYS,
        "startTime": local_minutes_to_utc(int(start_minutes), tz, now),
        "switchStatus": switch_status,
        "timeConsuming": max(CHARGE_MIN_DURATION_MIN, int(duration_minutes)),
    }


def build_stop_plan(now: datetime.datetime | None = None) -> dict:
    """A plan whose window is ALREADY over: now − 90 min with a 60 min duration → [now−90, now−30].

    "Now" falls outside the window and the car stops charging. The duration is not shortened (the car
    rejects less than 1 h): the START is moved instead. `startTime` is computed directly in UTC."""
    now_utc = (now or _utcnow()).astimezone(datetime.timezone.utc)
    start = (now_utc.hour * 60 + now_utc.minute - CHARGE_STOP_START_BACK_MIN) % MINUTES_PER_DAY
    return {"cycleData": ALL_WEEKDAYS, "startTime": int(start), "switchStatus": 1,
            "timeConsuming": CHARGE_STOP_DURATION_MIN}


def minutes_until_start(start_utc: int, now: datetime.datetime | None = None) -> int:
    """Minutes until the next opening of a window starting at `start_utc` (UTC minutes of the day)."""
    now_utc = (now or _utcnow()).astimezone(datetime.timezone.utc)
    return (int(start_utc) - (now_utc.hour * 60 + now_utc.minute)) % MINUTES_PER_DAY


def hold_needs_refresh(hold_start_utc: int | None, now: datetime.datetime | None = None,
                       margin: int = HOLD_REFRESH_MARGIN_MIN) -> bool:
    """Is the pause window about to reopen (and would the car start charging on its own)?

    Without a start value, it is renewed to be safe."""
    if hold_start_utc is None:
        return True
    return minutes_until_start(hold_start_utc, now) < margin


def duration_between(start_minutes: int, end_minutes: int) -> int:
    """Duration between a start and an end time (local). Wraps past midnight if needed; 0 = invalid."""
    return (int(end_minutes) - int(start_minutes)) % MINUTES_PER_DAY
