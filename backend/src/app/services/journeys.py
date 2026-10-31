"""What a journey cost and consumed: everything the car recorded between two instants, added up on demand.

A `Journey` row only keeps the name and the two dates. The figures come from the tables EVBro already fills: drives (km, hours,
kWh and litres), charging sessions (kWh, euros, AC/DC), refuels (litres, euros), expenses (tolls, parking, fines… the person's own
notes) and workshop visits inside the span. Nothing is copied, so editing a charge or adding a toll later changes the journey too.
Per-day km and the first and last addresses give the shape of the trip. The route itself is the drives' tracks (GET …/drives/{id}).
"""
from __future__ import annotations

import datetime
from collections import defaultdict
from typing import Any, Dict, List, Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_timezone
from app.db.models import ChargingSession, Drive, Expense, Journey, MaintenanceItem, MaintenanceLog, Refuel

MAX_DAYS = 400


def _r(v: Optional[float], d: int = 1) -> Optional[float]:
    return None if v is None else round(v, d)


def _day(dt: datetime.datetime, tz) -> str:
    return dt.replace(tzinfo=datetime.timezone.utc).astimezone(tz).strftime("%Y-%m-%d")


def serialize(j: Journey, summary: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    out = {"id": j.id, "vin": j.vin, "name": j.name, "start_time": j.start_time.isoformat(), "end_time": j.end_time.isoformat(),
           "notes": j.notes, "days": max(1, (j.end_time.date() - j.start_time.date()).days + 1)}
    if summary is not None:
        out["summary"] = summary
    return out


async def summary(session: AsyncSession, vin: str, start: datetime.datetime, end: datetime.datetime, *, with_items: bool = False) -> Dict[str, Any]:
    """Totals of the span [start, end]. `with_items` adds the lists behind the numbers (drives, charges, refuels, expenses)."""
    tz = get_timezone()
    drives = list((await session.execute(select(Drive).where(Drive.vin == vin, Drive.start_time >= start, Drive.start_time <= end, Drive.end_time.isnot(None))
                                         .order_by(Drive.start_time))).scalars())
    charges = list((await session.execute(select(ChargingSession).where(ChargingSession.vin == vin, ChargingSession.start_time >= start,
                                                                        ChargingSession.start_time <= end).order_by(ChargingSession.start_time))).scalars())
    refuels = list((await session.execute(select(Refuel).where(Refuel.vin == vin, Refuel.timestamp >= start, Refuel.timestamp <= end)
                                          .order_by(Refuel.timestamp))).scalars())
    expenses = list((await session.execute(select(Expense).where(Expense.vin == vin, Expense.date >= start, Expense.date <= end).order_by(Expense.date))).scalars())
    maint = list((await session.execute(select(MaintenanceLog).join(MaintenanceItem, MaintenanceLog.item_id == MaintenanceItem.id)
                                        .where(MaintenanceItem.vin == vin, MaintenanceLog.service_date >= start, MaintenanceLog.service_date <= end))).scalars())

    km = sum(d.distance_km or 0.0 for d in drives)
    seconds = sum(d.duration_seconds or 0 for d in drives)
    kwh_used = sum(d.energy_consumed_kwh or 0.0 for d in drives)
    fuel_used = sum(d.fuel_used_l or 0.0 for d in drives)
    ev_km = sum(d.distance_km or 0.0 for d in drives if (d.energy_consumed_kwh or 0.0) > 0 and (d.distance_km or 0.0) >= 1.0)
    fuel_km = sum(d.distance_km or 0.0 for d in drives if (d.fuel_used_l or 0.0) > 0 and (d.distance_km or 0.0) >= 1.0)
    charge_cost = sum(c.charge_cost or 0.0 for c in charges)
    charge_kwh = sum(c.kwh_added or 0.0 for c in charges)
    refuel_cost = sum(r.total_cost or 0.0 for r in refuels)
    refuel_l = sum(r.liters or 0.0 for r in refuels)
    expense_total = sum(e.amount or 0.0 for e in expenses)
    maint_cost = sum(m.cost or 0.0 for m in maint)
    total = charge_cost + refuel_cost + expense_total + maint_cost

    per_day: Dict[str, Dict[str, float]] = defaultdict(lambda: {"km": 0.0, "drives": 0})
    for d in drives:
        day = per_day[_day(d.start_time, tz)]
        day["km"] += d.distance_km or 0.0
        day["drives"] += 1
    by_category: Dict[str, float] = defaultdict(float)
    for e in expenses:
        by_category[e.category] += e.amount or 0.0

    out: Dict[str, Any] = {
        "drives": {"count": len(drives), "km": _r(km), "hours": _r(seconds / 3600.0), "kwh": _r(kwh_used), "fuel_l": _r(fuel_used),
                   "kwh_per_100km": _r(kwh_used / ev_km * 100.0) if ev_km >= 5 else None,
                   "l_per_100km": _r(fuel_used / fuel_km * 100.0) if fuel_km >= 5 else None,
                   "first_address": drives[0].start_address if drives else None, "last_address": drives[-1].end_address if drives else None,
                   "max_speed_kmh": _r(max((d.speed_max_kmh or 0.0) for d in drives)) if drives else None},
        "charges": {"count": len(charges), "kwh": _r(charge_kwh), "cost": _r(charge_cost, 2), "dc_count": sum(1 for c in charges if c.charge_type == "dc"),
                    "unpriced": sum(1 for c in charges if not c.cost_mode)},
        "refuels": {"count": len(refuels), "liters": _r(refuel_l), "cost": _r(refuel_cost, 2), "unpriced": sum(1 for r in refuels if r.total_cost is None)},
        "expenses": {"count": len(expenses), "total": _r(expense_total, 2), "by_category": {k: _r(v, 2) for k, v in sorted(by_category.items())}},
        "maintenance": {"count": len(maint), "cost": _r(maint_cost, 2)},
        "totals": {"cost": _r(total, 2), "cost_per_km": _r(total / km, 3) if km >= 1 else None, "cost_per_day": None},
        "per_day": [{"day": k, "km": _r(v["km"]), "drives": v["drives"]} for k, v in sorted(per_day.items())],
    }
    days = max(1, (end.date() - start.date()).days + 1)
    out["totals"]["cost_per_day"] = _r(total / days, 2)
    if with_items:
        out["items"] = {
            "drives": [{"id": d.id, "start_time": d.start_time.isoformat(), "from": d.start_address, "to": d.end_address, "km": _r(d.distance_km),
                        "kwh": _r(d.energy_consumed_kwh), "fuel_l": _r(d.fuel_used_l)} for d in drives],
            "charges": [{"id": c.id, "start_time": c.start_time.isoformat(), "address": c.address, "kwh": _r(c.kwh_added), "cost": _r(c.charge_cost, 2),
                         "type": c.charge_type or "ac"} for c in charges],
            "refuels": [{"id": r.id, "timestamp": r.timestamp.isoformat(), "station": r.station, "liters": _r(r.liters), "cost": _r(r.total_cost, 2)} for r in refuels],
            "expenses": [{"id": e.id, "date": e.date.isoformat(), "title": e.title, "category": e.category, "amount": _r(e.amount, 2)} for e in expenses],
        }
    return out


async def list_for(session: AsyncSession, vin: str) -> List[Dict[str, Any]]:
    rows = list((await session.execute(select(Journey).where(Journey.vin == vin).order_by(Journey.start_time.desc()))).scalars())
    out = []
    for j in rows:
        s = await summary(session, vin, j.start_time, j.end_time)
        out.append(serialize(j, {"drives": s["drives"], "totals": s["totals"], "charges": {"count": s["charges"]["count"]},
                                 "refuels": {"count": s["refuels"]["count"]}, "expenses": {"count": s["expenses"]["count"]}}))
    return out
