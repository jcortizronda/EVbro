"""2.0TD periods on the local clock, with the national holidays of CNMC Circular 3/2020 off-peak all day."""
from __future__ import annotations

import datetime

from app.config import settings
from app.services.electricity_tariffs import get_time_of_use_period


def utc(*args) -> datetime.datetime:
    return datetime.datetime(*args)                  # naive = UTC, as EVbro stores times


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


async def test_a_fixed_tariff_of_zero_is_free_not_the_default_price() -> None:
    """`cost_per_kwh or 0.15` charged a fixed 0 €/kWh at 0.15 (calculations audit, 27-11-2026)."""
    import datetime
    from types import SimpleNamespace

    from app.services.electricity_tariffs import calculate_charging_session_cost

    start = datetime.datetime(2026, 11, 20, 22, 0)
    free = SimpleNamespace(cost_mode="fixed", cost_per_kwh=0.0)
    res = await calculate_charging_session_cost(free, start, start + datetime.timedelta(hours=2), 10.0)
    assert (res["total_cost"], res["effective_kwh_cost"]) == (0.0, 0.0)
    unset = SimpleNamespace(cost_mode="fixed", cost_per_kwh=None)
    assert (await calculate_charging_session_cost(unset, start, start + datetime.timedelta(hours=2), 10.0))["total_cost"] == 1.5
