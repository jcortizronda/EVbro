"""Fuel statistics out of the refuel log: real consumption between full tanks, cost per 100 km, price per litre, autonomy.

How consumption is measured (the only honest way with a fuel log): between two **full-tank** refuels, the litres poured in after
the first one, up to and including the second, are exactly what the car burned over the odometer distance between them. Refuels
in between that were not full just add their litres. A refuel is "full" when the user says so (`full_tank`) or, for a detected
one with no answer, when the gauge ended at `FULL_TANK_PCT` or more.

For a plug-in hybrid the figure is litres per 100 km of **all** the km driven (electric ones included): that is the number a
driver pays for. The gauge-based estimate (`gauge_l_per_100km`) uses the percentage the tank dropped between detected refuels
times the tank size; it needs no full tanks but inherits the gauge's coarseness.
"""
from __future__ import annotations

import datetime
from collections import defaultdict
from typing import Any, Dict, Iterable, List, Optional

FULL_TANK_PCT = 95.0
MIN_SEGMENT_KM = 20.0            # shorter stretches between full tanks are noise (odometer rounding, a top-up next door)
PLAUSIBLE_L_PER_100 = (1.0, 30.0)


def is_full(refuel) -> bool:
    if refuel.full_tank is not None:
        return bool(refuel.full_tank)
    return refuel.fuel_after_pct is not None and refuel.fuel_after_pct >= FULL_TANK_PCT


def _month(d: datetime.datetime) -> str:
    return f"{d.year:04d}-{d.month:02d}"


def _mean(values: List[float]) -> Optional[float]:
    return round(sum(values) / len(values), 1) if values else None


def compute(refuels: Iterable, tank_liters: float, since: Optional[datetime.datetime] = None) -> Dict[str, Any]:
    """`refuels` in any order; `since` limits the aggregates (segments still use the previous refuel, even if it is older)."""
    rows = sorted(refuels, key=lambda r: r.timestamp)
    per_refuel: Dict[int, Dict[str, Any]] = {}

    # 1. km since the previous refuel (any kind) and the gauge-based litres burned in between
    prev = None
    for r in rows:
        info: Dict[str, Any] = {"km_since_previous": None, "gauge_liters_burned": None}
        if prev is not None and prev.odometer and r.odometer and r.odometer > prev.odometer:
            info["km_since_previous"] = round(r.odometer - prev.odometer, 1)
        if prev is not None and prev.fuel_after_pct is not None and r.fuel_before_pct is not None and prev.fuel_after_pct > r.fuel_before_pct:
            info["gauge_liters_burned"] = round((prev.fuel_after_pct - r.fuel_before_pct) / 100.0 * tank_liters, 1)
        per_refuel[r.id] = info
        prev = r

    # 2. real consumption between full tanks
    last_full = None
    liters_since_full = 0.0
    for r in rows:
        info = per_refuel[r.id]
        info.update({"l_per_100km": None, "km_since_full": None, "full": is_full(r)})
        if last_full is not None:
            liters_since_full += r.liters or 0.0
            if info["full"] and r.odometer and last_full.odometer and r.odometer > last_full.odometer:
                km = r.odometer - last_full.odometer
                if km >= MIN_SEGMENT_KM and liters_since_full > 0:
                    cons = liters_since_full / km * 100.0
                    if PLAUSIBLE_L_PER_100[0] <= cons <= PLAUSIBLE_L_PER_100[1]:
                        info["l_per_100km"] = round(cons, 1)
                        info["km_since_full"] = round(km, 1)
                        info["liters_since_full"] = round(liters_since_full, 1)
        if info["full"]:
            last_full = r
            liters_since_full = 0.0

    # 3. aggregates over the period
    selected = [r for r in rows if since is None or r.timestamp >= since]
    seg_km = sum(per_refuel[r.id]["km_since_full"] for r in selected if per_refuel[r.id]["l_per_100km"] is not None)
    seg_l = sum(per_refuel[r.id]["liters_since_full"] for r in selected if per_refuel[r.id]["l_per_100km"] is not None)
    avg_cons = round(seg_l / seg_km * 100.0, 1) if seg_km else None

    gauge_km = sum(per_refuel[r.id]["km_since_previous"] for r in selected
                   if per_refuel[r.id]["gauge_liters_burned"] is not None and per_refuel[r.id]["km_since_previous"])
    gauge_l = sum(per_refuel[r.id]["gauge_liters_burned"] for r in selected
                  if per_refuel[r.id]["gauge_liters_burned"] is not None and per_refuel[r.id]["km_since_previous"])
    gauge_cons = round(gauge_l / gauge_km * 100.0, 1) if gauge_km else None

    priced = [r for r in selected if r.total_cost is not None and r.liters]
    total_cost = round(sum(r.total_cost for r in priced), 2)
    priced_liters = sum(r.liters for r in priced)
    avg_price = round(total_cost / priced_liters, 3) if priced_liters else None
    consumption = avg_cons if avg_cons is not None else gauge_cons

    months: Dict[str, Dict[str, Any]] = defaultdict(lambda: {"count": 0, "liters": 0.0, "cost": 0.0, "km": 0.0})
    for r in selected:
        m = months[_month(r.timestamp)]
        m["count"] += 1
        m["liters"] += r.liters or 0.0
        m["cost"] += r.total_cost or 0.0
        m["km"] += per_refuel[r.id]["km_since_previous"] or 0.0

    last = rows[-1] if rows else None
    return {
        "count": len(selected),
        "total_liters": round(sum(r.liters or 0.0 for r in selected), 1),
        "total_cost": total_cost,
        "unpriced": sum(1 for r in selected if r.total_cost is None),
        "avg_price_per_liter": avg_price,
        "l_per_100km": avg_cons,                       # between full tanks (real)
        "l_per_100km_segments": sum(1 for r in selected if per_refuel[r.id]["l_per_100km"] is not None),
        "gauge_l_per_100km": gauge_cons,               # from the gauge drop (estimate)
        "fuel_cost_per_100km": round(avg_price * consumption, 2) if avg_price is not None and consumption is not None else None,
        "avg_km_between_refuels": _mean([per_refuel[r.id]["km_since_previous"] for r in selected if per_refuel[r.id]["km_since_previous"]]),
        "avg_liters_per_refuel": _mean([r.liters for r in selected if r.liters]),
        "full_tank_range_km": round(tank_liters * 100.0 / consumption) if consumption else None,
        "tank_liters": tank_liters,
        "last_refuel_at": last.timestamp.isoformat() if last else None,
        "months": [{"month": k, **{kk: (round(v, 1) if isinstance(v, float) else v) for kk, v in m.items()}}
                   for k, m in sorted(months.items(), reverse=True)],
        "per_refuel": per_refuel,
    }
