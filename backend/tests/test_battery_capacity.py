"""Usable capacity measured from a charge's curve: what it accepts and what it refuses."""
from __future__ import annotations

import datetime
from types import SimpleNamespace as P

from app.services.battery_capacity import measured_capacity_kwh

T0 = datetime.datetime(2026, 11, 1, 18)


def curve(n, step_min=2, start=45, gained=22, kw=2.9, **over):
    return [P(timestamp=T0 + datetime.timedelta(minutes=step_min * i), battery_pct=start + gained * i / (n - 1), power_kw=kw,
              voltage=over.get("v"), current=over.get("a")) for i in range(n)]


def test_a_steady_charge_gives_energy_over_points() -> None:
    # 82 points × 2 min = 162 min at 2.9 kW = 7.83 kWh for 22 points → 35.6 kWh (the real S900 charge of 01-11-2026)
    assert round(measured_capacity_kwh(curve(82)), 1) == 35.6


def test_voltage_times_current_when_there_is_no_power() -> None:
    assert round(measured_capacity_kwh(curve(82, kw=None, v=340.0, a=-8.53)), 1) == 35.6


def test_small_charges_holes_and_missing_power_are_refused() -> None:
    assert measured_capacity_kwh(curve(30, gained=10)) is None                # 10 points: the rounding weighs too much
    holes = curve(82)
    for p in holes[40:]:
        p.timestamp += datetime.timedelta(minutes=30)                           # a 32-minute hole in the middle
    assert measured_capacity_kwh(holes) is None
    gap = curve(82)
    gap[10].power_kw = None
    assert measured_capacity_kwh(gap) is None
    assert measured_capacity_kwh([]) is None
