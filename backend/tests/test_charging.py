"""Charge scheduling: local hours↔UTC, plans and the decision of when to hold or release."""
from __future__ import annotations

import datetime
from zoneinfo import ZoneInfo

import pytest

from app.services.charge_manager import HYSTERESIS, MODE_FREE, MODE_HOLD, MODE_SCHEDULED, PlanState, decide
from app.vehicle import charging as c

MADRID = ZoneInfo("Europe/Madrid")
UTC = datetime.timezone.utc
WINTER = datetime.datetime(2026, 1, 15, 12, 0, tzinfo=UTC)     # Spain = UTC+1
SUMMER = datetime.datetime(2026, 7, 15, 12, 0, tzinfo=UTC)       # Spain = UTC+2


# ───────────────────────── local time ↔ UTC ─────────────────────────
@pytest.mark.parametrize("now,local_h,utc_h", [(WINTER, 3, 2), (SUMMER, 3, 1), (WINTER, 0, 23), (SUMMER, 1, 23)])
def test_local_to_utc_follows_the_current_offset(now, local_h, utc_h) -> None:
    """Verified in the field by the HA integration: the backend reads startTime as UTC."""
    assert c.local_minutes_to_utc(local_h * 60, MADRID, now) == utc_h * 60


@pytest.mark.parametrize("now", [WINTER, SUMMER])
def test_round_trip(now) -> None:
    for minutes in (0, 59, 180, 1439):
        assert c.utc_minutes_to_local(c.local_minutes_to_utc(minutes, MADRID, now), MADRID, now) == minutes


# ───────────────────────── plans ─────────────────────────
def test_normal_plan_converts_the_start_but_not_the_duration() -> None:
    plan = c.build_plan(22 * 60, 8 * 60, 1, MADRID, WINTER)
    assert plan == {"cycleData": [1, 2, 3, 4, 5, 6, 7], "startTime": 21 * 60, "switchStatus": 1, "timeConsuming": 480}


def test_the_car_minimum_duration_is_enforced() -> None:
    assert c.build_plan(0, 10, 1, MADRID, WINTER)["timeConsuming"] == 60      # under 1 h gives code 89


def test_the_hold_plan_has_an_already_finished_window() -> None:
    plan = c.build_stop_plan(WINTER)                                          # 12:00 UTC
    start, dur = plan["startTime"], plan["timeConsuming"]
    assert (start, dur) == (10 * 60 + 30, 60)                                  # [10:30, 11:30] UTC: ended 30 min ago
    now = 12 * 60
    assert not (start <= now < start + dur), "«now» must not fall inside the window"


def test_the_hold_plan_crosses_midnight_without_leaving_the_day() -> None:
    plan = c.build_stop_plan(datetime.datetime(2026, 1, 15, 0, 30, tzinfo=UTC))
    assert plan["startTime"] == (30 - 90) % 1440 == 1380


# ───────────────────────── renewing the hold window ─────────────────────────
def test_minutes_until_the_next_opening() -> None:
    assert c.minutes_until_start(10 * 60 + 30, WINTER) == 22 * 60 + 30         # hold window just set
    assert c.minutes_until_start(13 * 60, WINTER) == 60
    assert c.minutes_until_start(11 * 60, WINTER) == 23 * 60                   # already past today → tomorrow


def test_the_hold_is_renewed_before_the_window_opens_again() -> None:
    recent = c.build_stop_plan(WINTER)["startTime"]
    assert not c.hold_needs_refresh(recent, WINTER)
    assert not c.hold_needs_refresh(recent, WINTER + datetime.timedelta(hours=18))     # 4 h 30 before opening
    assert c.hold_needs_refresh(recent, WINTER + datetime.timedelta(hours=19))         # 3 h 30: renewed
    assert c.hold_needs_refresh(None, WINTER)                                            # no data: to be safe


def test_renewing_puts_the_window_far_away_again() -> None:
    before = c.build_stop_plan(WINTER)["startTime"]
    now = WINTER + datetime.timedelta(hours=19)
    after = c.build_stop_plan(now)["startTime"]
    assert c.hold_needs_refresh(before, now) and not c.hold_needs_refresh(after, now)


# ───────────────────────── reading what the car has ─────────────────────────
def test_parse_schedule_goes_back_to_local_time() -> None:
    payload = {"mainSwitch": 1, "chargeAppointPlans": [{"cycleData": [1, 2], "startTime": 21 * 60, "switchStatus": 1, "timeConsuming": 480}]}
    s = c.parse_schedule(payload, MADRID, WINTER)
    assert s.enabled and s.start_minutes == 22 * 60 and s.end_minutes == 6 * 60 and s.days == (1, 2)


def test_parse_schedule_requires_both_switches() -> None:
    plan = {"startTime": 0, "switchStatus": 0, "timeConsuming": 60}
    assert not c.parse_schedule({"mainSwitch": 1, "chargeAppointPlans": [plan]}, MADRID, WINTER).enabled
    assert not c.parse_schedule({"mainSwitch": 0, "chargeAppointPlans": [{**plan, "switchStatus": 1}]}, MADRID, WINTER).enabled


@pytest.mark.parametrize("garbage", [None, "x", [], {}, {"chargeAppointPlans": "mal"}])
def test_parse_schedule_tolerates_garbage(garbage) -> None:
    s = c.parse_schedule(garbage, MADRID, WINTER)
    assert s is None or (not s.enabled and s.start_minutes is None)


def test_duration_between_two_times() -> None:
    assert c.duration_between(22 * 60, 6 * 60) == 8 * 60          # crosses midnight
    assert c.duration_between(1 * 60, 5 * 60) == 4 * 60
    assert c.duration_between(60, 60) == 0                        # start = end: invalid


# ───────────────────────── manager decision ─────────────────────────
def free(target=80, **kw): return PlanState(target_pct=target, mode=MODE_FREE, **kw)
def on_hold(reason, target=80, start=None):
    return PlanState(target_pct=target, mode=MODE_HOLD, hold_reason=reason,
                     hold_start_utc=c.build_stop_plan(WINTER)["startTime"] if start is None else start)


def d(state, soc=None, plugged=True, charging=False, now=WINTER):
    return decide(state, soc=soc, plugged=plugged, charging_now=charging, now=now)


def test_with_a_100_target_the_manager_does_nothing() -> None:
    assert d(free(100), soc=100, charging=True) is None
    assert d(PlanState(), soc=99, charging=True) is None                     # default state: inert


def test_reaching_the_target_while_charging_holds() -> None:
    assert d(free(80), soc=80, charging=True) == "hold_target"
    assert d(free(80), soc=83, charging=True) == "hold_target"
    assert d(PlanState(target_pct=80, mode=MODE_SCHEDULED), soc=80, charging=True) == "hold_target"


def test_below_the_target_or_not_charging_does_not_hold() -> None:
    assert d(free(80), soc=79, charging=True) is None
    assert d(free(80), soc=95, charging=False) is None                       # not charging: nothing to stop


@pytest.mark.parametrize("soc", [None, 0, -1])
def test_an_unknown_soc_never_counts(soc) -> None:
    """With high voltage off the car sends 0: that is neither «target reached» nor «low battery»."""
    assert d(free(80), soc=soc, charging=True) is None
    assert d(on_hold("target"), soc=soc) is None


def test_the_target_hold_is_released_when_there_is_room_and_it_is_plugged() -> None:
    assert d(on_hold("target", 80), soc=75) == "release"                      # 5 points below the target
    assert d(on_hold("target", 80), soc=40) == "release"
    assert d(on_hold("target", 80), soc=76) is None                          # 5-point hysteresis: no flapping
    assert HYSTERESIS == 5
    assert d(on_hold("target", 80), soc=40, plugged=False) is None           # no cable, nothing to release


def test_raising_the_target_to_100_releases_the_hold() -> None:
    assert d(on_hold("target", 100), soc=95) == "release"
    assert d(on_hold("target", 100), soc=None, plugged=False) == "release"


def test_the_user_hold_is_not_released_by_itself() -> None:
    assert d(on_hold("user", 80), soc=10) is None
    assert d(on_hold("user", 100), soc=10) is None


def test_the_hold_is_renewed_when_the_window_is_near() -> None:
    near = on_hold("user", start=(12 * 60 + 120) % 1440)                                           # opens in 2 h
    assert d(near) == "refresh_hold"
    assert d(on_hold("user")) is None                                                              # just set


def test_release_takes_priority_over_renew() -> None:
    near = on_hold("target", 80, start=(12 * 60 + 120) % 1440)
    assert d(near, soc=50) == "release"
