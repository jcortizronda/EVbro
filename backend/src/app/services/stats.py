"""Statistics of a car's history, ready to draw (Más → Estadísticas).

Everything comes from what EVBro already stores: drives, charging sessions, refuels, expenses and workshop logs. Nothing is read
from the car here. Months follow the server's time zone. Only drives of at least `MIN_DRIVE_KM` enter the consumption figures, and the
battery capacity is estimated only from charges that added at least `MIN_SOC_DELTA` points (short top-ups give wild numbers).
"""
from __future__ import annotations

import datetime
from collections import defaultdict
from typing import Any, Dict, List, Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_timezone
from app.db.models import ChargingSession, Drive, Expense, MaintenanceItem, MaintenanceLog, Refuel
from app.services import periods

MIN_DRIVE_KM = 2.0     # below this, one battery point and the odometer's whole-km rounding give meaningless figures (measured 2026-11-01: 1–2 km drives averaged 52 kWh/100 km, 2–3 km ones 17.5)
MIN_SOC_DELTA = 30.0
IDLE_GAP_H = 6.0
PERIODS = {"12m": 365, "all": None}


def _month(dt: datetime.datetime, tz) -> str:
    return dt.replace(tzinfo=datetime.timezone.utc).astimezone(tz).strftime("%Y-%m")


def _r(v: Optional[float], d: int = 1) -> Optional[float]:
    return None if v is None else round(v, d)


def _bucket(value: float, size: float) -> float:
    return (value // size) * size


async def compute(session: AsyncSession, vin: str, period: str = "12m", frm: Optional[str] = None, to: Optional[str] = None) -> Dict[str, Any]:
    """`period` is a preset of services/periods.py (`12m` by default) or `custom` with `frm`/`to` (`YYYY-MM`)."""
    tz = get_timezone()
    bounds = periods.resolve(period, frm, to)

    def recent(column):
        return periods.condition(column, bounds)

    drives = list((await session.execute(select(Drive).where(Drive.vin == vin, recent(Drive.start_time)).order_by(Drive.start_time))).scalars())
    charges = list((await session.execute(select(ChargingSession).where(ChargingSession.vin == vin, recent(ChargingSession.start_time))
                                          .order_by(ChargingSession.start_time))).scalars())
    refuels = list((await session.execute(select(Refuel).where(Refuel.vin == vin, recent(Refuel.timestamp)))).scalars())
    expenses = list((await session.execute(select(Expense).where(Expense.vin == vin, recent(Expense.date)))).scalars())
    logs = list((await session.execute(select(MaintenanceLog).join(MaintenanceItem, MaintenanceLog.item_id == MaintenanceItem.id)
                                       .where(MaintenanceItem.vin == vin, recent(MaintenanceLog.service_date)))).scalars())

    # ── per month ──
    months: Dict[str, Dict[str, float]] = defaultdict(lambda: defaultdict(float))
    for d in drives:
        m = months[_month(d.start_time, tz)]
        m["km"] += d.distance_km or 0.0
        m["kwh_used"] += d.energy_consumed_kwh or 0.0
        m["liters_used"] += d.fuel_used_l or 0.0
        if (d.distance_km or 0) >= MIN_DRIVE_KM and d.energy_consumed_kwh:
            m["eff_km"] += d.distance_km
            m["eff_kwh"] += d.energy_consumed_kwh
    for c in charges:
        m = months[_month(c.start_time, tz)]
        m["kwh_charged"] += c.kwh_added or 0.0
        m["cost_charges"] += c.charge_cost or 0.0
    for r in refuels:
        m = months[_month(r.timestamp, tz)]
        m["liters"] += r.liters or 0.0
        m["cost_fuel"] += r.total_cost or 0.0
    for e in expenses:
        months[_month(e.date, tz)]["cost_other"] += e.amount or 0.0
    for log in logs:
        months[_month(log.service_date, tz)]["cost_maintenance"] += log.cost or 0.0

    monthly: List[Dict[str, Any]] = []
    for key in sorted(months):
        m = months[key]
        total = m["cost_charges"] + m["cost_fuel"] + m["cost_other"] + m["cost_maintenance"]
        monthly.append({"month": key, "km": _r(m["km"]), "kwh_used": _r(m["kwh_used"]), "liters_used": _r(m["liters_used"]),
                        "kwh_charged": _r(m["kwh_charged"]), "liters": _r(m["liters"]),
                        "cost_charges": _r(m["cost_charges"], 2), "cost_fuel": _r(m["cost_fuel"], 2), "cost_other": _r(m["cost_other"], 2),
                        "cost_maintenance": _r(m["cost_maintenance"], 2), "cost_total": _r(total, 2),
                        "cost_per_km": _r(total / m["km"], 3) if m["km"] >= 20 else None,
                        "kwh_per_100km": _r(m["eff_kwh"] / m["eff_km"] * 100) if m["eff_km"] >= MIN_DRIVE_KM else None})

    # ── consumption by average speed (a by-temperature chart was dropped on 2026-10-31: the car never reports the outside temperature) ──
    def buckets(key_fn, size: int, label_fn) -> List[Dict[str, Any]]:
        acc: Dict[float, Dict[str, float]] = defaultdict(lambda: defaultdict(float))
        for d in drives:
            value = key_fn(d)
            if value is None or (d.distance_km or 0) < MIN_DRIVE_KM or not d.energy_consumed_kwh:
                continue
            b = acc[_bucket(value, size)]
            b["km"] += d.distance_km
            b["kwh"] += d.energy_consumed_kwh
            b["n"] += 1
        return [{"label": label_fn(k, size), "from": k, "kwh_per_100km": _r(v["kwh"] / v["km"] * 100), "drives": int(v["n"])}
                for k, v in sorted(acc.items()) if v["km"] > 0]

    by_speed = buckets(lambda d: d.speed_avg_kmh if (d.speed_avg_kmh or 0) > 0 else None, 10, lambda k, s: f"{int(k)}–{int(k + s)} km/h")

    # ── usable capacity observed in big charges: kWh added / SoC gained, per month ──
    cap_months: Dict[str, List[float]] = defaultdict(list)
    for c in charges:
        if c.start_battery_pct is None or c.end_battery_pct is None or not c.kwh_added:
            continue
        delta = c.end_battery_pct - c.start_battery_pct
        if delta >= MIN_SOC_DELTA:
            cap_months[_month(c.start_time, tz)].append(c.kwh_added / delta * 100.0)
    battery_health = [{"month": k, "capacity_kwh": _r(sum(v) / len(v), 2), "charges": len(v)} for k, v in sorted(cap_months.items())]

    # ── charges by place ──
    places: Dict[str, Dict[str, float]] = defaultdict(lambda: defaultdict(float))
    for c in charges:
        p = places[c.address or "Sin dirección"]
        p["kwh"] += c.kwh_added or 0.0
        p["cost"] += c.charge_cost or 0.0
        p["count"] += 1
    charges_by_place = sorted(({"address": k, "kwh": _r(v["kwh"]), "cost": _r(v["cost"], 2), "count": int(v["count"])} for k, v in places.items()),
                              key=lambda p: -(p["kwh"] or 0))[:8]

    # ── idle drain: battery lost between the end of a drive and the start of the next, with no charge in between ──
    charge_starts = [c.start_time for c in charges]
    drain_months: Dict[str, List[float]] = defaultdict(list)
    for prev, nxt in zip(drives, drives[1:], strict=False):
        if prev.end_time is None or prev.end_battery_pct is None or nxt.start_battery_pct is None:
            continue
        hours = (nxt.start_time - prev.end_time).total_seconds() / 3600.0
        if hours < IDLE_GAP_H or any(prev.end_time <= s <= nxt.start_time for s in charge_starts):
            continue
        drop = prev.end_battery_pct - nxt.start_battery_pct
        if drop < 0 or drop > 40:
            continue
        drain_months[_month(prev.end_time, tz)].append(drop / hours * 24.0)
    idle_drain = [{"month": k, "pct_per_day": _r(sum(v) / len(v), 2), "stops": len(v)} for k, v in sorted(drain_months.items())]

    km = sum(m["km"] or 0 for m in monthly)
    cost_total = sum(m["cost_total"] or 0 for m in monthly)
    eff_km = sum(months[k]["eff_km"] for k in months)
    eff_kwh = sum(months[k]["eff_kwh"] for k in months)
    totals = {"km": _r(km), "kwh_charged": _r(sum(m["kwh_charged"] or 0 for m in monthly)), "liters": _r(sum(m["liters"] or 0 for m in monthly)),
              "cost_total": _r(cost_total, 2), "cost_per_km": _r(cost_total / km, 3) if km >= 20 else None, "drives": len(drives), "charges": len(charges),
              # electric consumption of the whole period: drives of ≥ MIN_DRIVE_KM with measured energy (the Energía tab shows the 30-day value)
              "kwh_per_100km": _r(eff_kwh / eff_km * 100) if eff_km >= MIN_DRIVE_KM else None}
    notes = ("Los kWh de viajes y cargas se estiman a partir del porcentaje de batería y la capacidad de la ficha; la capacidad observada sale de "
             f"cargas de al menos {int(MIN_SOC_DELTA)} puntos y el gasto en reposo de paradas de más de {int(IDLE_GAP_H)} horas sin carga.")
    return {"period": period, "from": frm, "to": to, "monthly": monthly, "consumption_by_speed": by_speed, "battery_health": battery_health,
            "charges_by_place": charges_by_place, "idle_drain": idle_drain, "totals": totals, "notes": notes}
