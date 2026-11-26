"""Energy of a charge measured on its curve.

The stored figure of a charge (`kwh_added`) is an estimate: % gained × the capacity on the car sheet, which takes every 1 % to be
worth the same. The curve offers a second, independent figure: the power the car reports at the battery (voltage × current) in
each reading, added up over time. It needs no capacity, so it also tells how much one 1 % was really worth in that charge.

The two figures are not the same thing and need not agree: the curve measures what enters the battery, and part of that is lost
as heat inside it, so it usually comes out a few per cent above what the % says (median +7 % over 66 real charges of several cars
on 26-11-2026, between −6 % and +16 %). That is why it is shown beside the stored figure and does not replace it.

It is only as good as the readings: the cloud is read every couple of minutes while charging, and a long hole between two readings
cannot be filled in. Holes are left out of the sum and counted, and the result says whether enough of the charge was seen to trust it.
"""
from __future__ import annotations

import datetime
from typing import Any, Dict, Optional, Sequence

MAX_GAP_S = 600           # two readings further apart than this are a hole: nothing is assumed about what happened in between
MIN_COVERAGE = 0.9        # share of the charge's duration that must lie between close readings for the sum to be trusted
MIN_POINTS = 4
MIN_GAIN_FOR_CAPACITY = 20.0      # % gained below which «what the whole battery would hold» is too noisy to say


def energy(points: Sequence[Any], start: Optional[datetime.datetime], end: Optional[datetime.datetime],
           gain_pct: Optional[float] = None) -> Optional[Dict[str, Any]]:
    """`{"kwh", "coverage", "reliable", "capacity_kwh"}` for the readings of one finished charge (objects with `timestamp` and
    `power_kw`), or `None` when there is nothing to add up. `capacity_kwh` is what 100 % would hold at this charge's rate
    (only with a reliable sum and a gain of `MIN_GAIN_FOR_CAPACITY` or more)."""
    rows = sorted(((p.timestamp, max(0.0, p.power_kw)) for p in points if p.power_kw is not None and p.timestamp is not None), key=lambda r: r[0])
    if len(rows) < 2 or start is None or end is None or end <= start:
        return None
    kwh = seen_s = 0.0
    for (t0, p0), (t1, p1) in zip(rows, rows[1:], strict=False):
        gap = (t1 - t0).total_seconds()
        if 0 < gap <= MAX_GAP_S:
            kwh += (p0 + p1) / 2.0 * gap / 3600.0             # trapezoid between two close readings
            seen_s += gap
    if kwh <= 0:
        return None
    coverage = min(1.0, seen_s / (end - start).total_seconds())
    reliable = len(rows) >= MIN_POINTS and coverage >= MIN_COVERAGE
    capacity = round(kwh / gain_pct * 100.0, 1) if reliable and gain_pct and gain_pct >= MIN_GAIN_FOR_CAPACITY else None
    return {"kwh": round(kwh, 2), "coverage": round(coverage, 2), "reliable": reliable, "capacity_kwh": capacity}
