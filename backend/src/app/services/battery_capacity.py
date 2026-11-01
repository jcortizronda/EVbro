"""Usable battery capacity measured from the charging curve, not from the sheet.

The kWh stored on a charge are an estimate (points gained × the sheet's capacity), so dividing them by the points gained gives the
sheet's capacity back: useless to follow the battery's health. The curve is a measurement: every point of a charge (every ~2 min)
carries the pack's power (`power_kw`, or voltage × current). Integrating it gives the energy that went into the battery, and
dividing by the points gained gives the usable capacity the car really showed (checked 2026-11-01 on real charges: 36.3 and 35.6 kWh
on an S900 whose sheet says 34.5; 19.5 kWh on a car with 19.27).

Limits, said plainly: the state of charge comes in whole points, so a charge of 20 points has a ±5 % error; the energy is measured at
the pack, so it includes a little loss inside the battery (a few percent above the sheet is normal). It follows a trend over months;
it is not a laboratory test.
"""
from __future__ import annotations

from typing import Iterable, Optional

MIN_POINTS_GAINED = 20.0          # fewer points: the whole-point rounding of the state of charge weighs too much
MAX_GAP_SECONDS = 10 * 60         # a hole in the curve longer than this and the energy can no longer be trusted


def _power_kw(p) -> Optional[float]:
    if p.power_kw is not None and p.power_kw > 0:
        return p.power_kw
    if p.voltage is not None and p.current is not None:
        return abs(p.voltage * p.current) / 1000.0
    return None


def measured_capacity_kwh(points: Iterable) -> Optional[float]:
    """Usable capacity shown by one charge's curve (`ChargePoint`s in any order), or None when the curve is not good enough:
    too few points gained, a hole longer than 10 min or a point without power."""
    pts = sorted((p for p in points if p.battery_pct is not None), key=lambda p: p.timestamp)
    if len(pts) < 3:
        return None
    gained = pts[-1].battery_pct - pts[0].battery_pct
    if gained < MIN_POINTS_GAINED:
        return None
    energy = 0.0
    for a, b in zip(pts, pts[1:], strict=False):         # consecutive pairs: the second list is one shorter on purpose
        dt = (b.timestamp - a.timestamp).total_seconds()
        pa, pb = _power_kw(a), _power_kw(b)
        if dt > MAX_GAP_SECONDS or pa is None or pb is None:
            return None
        energy += (pa + pb) / 2 * dt / 3600.0
    return energy / gained * 100.0 if energy > 0 else None
