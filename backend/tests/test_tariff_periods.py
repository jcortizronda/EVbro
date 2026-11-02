"""2.0TD periods on the local clock, with the national holidays of CNMC Circular 3/2020 off-peak all day."""
from __future__ import annotations

import datetime

from app.config import settings
from app.services.electricity_tariffs import get_time_of_use_period


def utc(*args) -> datetime.datetime:
    return datetime.datetime(*args)                  # naive = UTC, as EVBro stores times


def test_periods_follow_the_spanish_clock_not_utc(monkeypatch) -> None:
    monkeypatch.setattr(settings, "TIMEZONE", "Europe/Madrid")
    # Tuesday 3 Nov 2026, winter (UTC+1): 09:30 UTC is 10:30 in Madrid -> punta (in UTC it would wrongly be llano)
    assert get_time_of_use_period(utc(2026, 11, 3, 9, 30))[0] == "punta"
    # 06:30 UTC = 07:30 local -> still valle; 07:30 UTC = 08:30 local -> llano
    assert get_time_of_use_period(utc(2026, 11, 3, 6, 30))[0] == "valle"
    assert get_time_of_use_period(utc(2026, 11, 3, 7, 30))[0] == "llano"
    # summer (UTC+2): Wednesday 15 Jul 2026 16:30 UTC = 18:30 local -> punta
    assert get_time_of_use_period(utc(2026, 7, 15, 16, 30))[0] == "punta"
    # Friday 6 Nov 23:30 UTC is already Saturday 00:30 local -> the weekend (in UTC it would wrongly be Friday llano)
    assert get_time_of_use_period(utc(2026, 11, 6, 23, 30))[0] == "valle"
    assert get_time_of_use_period(utc(2026, 11, 6, 22, 30))[0] == "llano"
    # the Canary Islands keep their own clock (UTC+0 in winter): 09:30 UTC is 09:30 there -> llano
    assert get_time_of_use_period(utc(2026, 11, 3, 9, 30), zone="Canarias")[0] == "llano"


def test_fixed_national_holidays_are_off_peak_all_day_but_moved_ones_are_not(monkeypatch) -> None:
    monkeypatch.setattr(settings, "TIMEZONE", "Europe/Madrid")
    # Tuesday 8 Dec 2026 and Monday 12 Oct 2026 at noon local
    assert get_time_of_use_period(utc(2026, 12, 8, 11, 0)) == ("valle", "Valle (Festivo nacional 24h)")
    assert get_time_of_use_period(utc(2026, 10, 12, 10, 0))[0] == "valle"
    # 6 January counts even though it can be moved; 1 May too
    assert get_time_of_use_period(utc(2027, 1, 6, 11, 0))[0] == "valle"
    assert get_time_of_use_period(utc(2026, 5, 1, 10, 0))[0] == "valle"
    # Monday 2 Nov 2026: 1 Nov fell on a Sunday and the regions moved it -> a normal working day
    assert get_time_of_use_period(utc(2026, 11, 2, 11, 0))[0] == "punta"
    # Good Friday (3 Apr 2026) has no fixed date -> a normal working day
    assert get_time_of_use_period(utc(2026, 4, 3, 11, 0))[0] == "punta"
