"""Statistics of a car's history, ready to draw (Más → Estadísticas).

Everything comes from what EVbro already stores: drives, charging sessions, refuels, expenses and workshop logs. Nothing is read
from the car here. Months follow the server's time zone. Only drives of at least `MIN_DRIVE_KM` enter the consumption figures, and the
battery capacity is MEASURED from each charge's curve (services/battery_capacity.py): the energy the pack took ÷ the points gained,
only for charges of at least 20 points with a complete curve.
"""
from __future__ import annotations

import datetime
from collections import defaultdict
from typing import Any, Dict, List, Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_timezone
from app.db.models import ChargePoint, ChargingSession, Drive, Expense, MaintenanceItem, MaintenanceLog, Refuel, Vehicle
from app.services import battery_capacity, periods
from app.services.manual_charges import energy_kwh

MIN_DRIVE_KM = 2.0     # below this, one battery point and the odometer's whole-km rounding give meaningless figures (measured 2026-11-01: 1–2 km drives averaged 52 kWh/100 km, 2–3 km ones 17.5)
IDLE_GAP_H = 6.0
PERIODS = {"12m": 365, "all": None}


def ev_km(d: Drive) -> Optional[float]:
    """Difference of the car's electric-only odometer («Cuentakilómetros EV») between the trip's ends. ONLY for sums over many
    trips (months, periods): the EV counter and the total one both count whole km and tick about half a km apart (readings of
    11/12-11-2026), so one trip can come out a km over or under even when all of it was electric. In a sum those errors cancel
    out; per trip they are noise, so this is never shown nor used to judge a single trip. None when a reading is missing or
    the pair does not fit the trip."""
    start, end = d.start_ev_odometer, d.end_ev_odometer
    if not start or not end or end < start:
        return None
    km = end - start
    if km > (d.distance_km or 0.0) + 1:   # more than the phase between the counters: not a pair of the same trip
        return None
    return km


def electric_only(d: Drive) -> Optional[bool]:
    """Whether the trip was driven without petrol, from the tank: a trip that spent none is electric. The EV odometer is too
    coarse to judge one trip (`ev_km`). None when the tank was not read."""
    if d.fuel_used_l is not None:
        return d.fuel_used_l <= 0
    return None


def counts_for_ev_consumption(d: Drive) -> bool:
    """Drives that enter the electric consumption (kWh/100 km): long enough, with measured energy and fully electric. When the
    petrol engine runs it also charges the battery, so the battery points spent understate what driving cost and the
    consumption would come out lower than real (owner's observation, 10-11-2026)."""
    return (d.distance_km or 0) >= MIN_DRIVE_KM and bool(d.energy_consumed_kwh) and electric_only(d) is not False


def _month(dt: datetime.datetime, tz) -> str:
    return dt.replace(tzinfo=datetime.timezone.utc).astimezone(tz).strftime("%Y-%m")


def _r(v: Optional[float], d: int = 1) -> Optional[float]:
    return None if v is None else round(v, d)


def _bucket(value: float, size: float) -> float:
    return (value // size) * size


async def measured_battery_health(session: AsyncSession, vin: str, charges: List[ChargingSession], tz) -> tuple:
    """Usable capacity MEASURED from the curve of each big charge (services/battery_capacity.py): per month, and the latest
    charges against the sheet's capacity. Shared by Estadísticas and the car book (services/car_book.py)."""
    charges = sorted(charges, key=lambda c: c.start_time)
    big = [c for c in charges if c.start_battery_pct is not None and c.end_battery_pct is not None
           and c.end_battery_pct - c.start_battery_pct >= battery_capacity.MIN_POINTS_GAINED]
    points: Dict[int, List[Any]] = defaultdict(list)
    if big:
        for p in (await session.execute(select(ChargePoint).where(ChargePoint.charging_session_id.in_([c.id for c in big])))).scalars():
            points[p.charging_session_id].append(p)
    cap_months: Dict[str, List[float]] = defaultdict(list)
    measured: List[float] = []
    for c in big:
        cap = battery_capacity.measured_capacity_kwh(points[c.id])
        if cap is not None:
            cap_months[_month(c.start_time, tz)].append(cap)
            measured.append(cap)
    health = [{"month": k, "capacity_kwh": _r(sum(v) / len(v), 2), "charges": len(v)} for k, v in sorted(cap_months.items())]
    nominal = (await session.execute(select(Vehicle.battery_capacity_kwh).where(Vehicle.vin == vin))).scalar_one_or_none()
    recent_caps = measured[-5:]                      # the latest charges say how the battery is now
    now_kwh = sum(recent_caps) / len(recent_caps) if recent_caps else None
    summary = {"nominal_kwh": nominal, "measured_kwh": _r(now_kwh, 1), "charges": len(recent_caps),
               "pct_of_nominal": _r(now_kwh / nominal * 100) if now_kwh and nominal else None}
    return health, summary


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
        if counts_for_ev_consumption(d):
            m["eff_km"] += d.distance_km
            m["eff_kwh"] += d.energy_consumed_kwh
        km_ev = ev_km(d)
        if km_ev is not None:
            m["ev_km"] += km_ev
            m["ev_known_km"] += d.distance_km or 0.0
    for c in charges:
        m = months[_month(c.start_time, tz)]
        m["kwh_charged"] += energy_kwh(c)
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
                        "kwh_per_100km": _r(m["eff_kwh"] / m["eff_km"] * 100) if m["eff_km"] >= MIN_DRIVE_KM else None,
                        # electric and petrol km of the trips with the EV odometer at both ends; the rest of `km` is unknown
                        "ev_km": _r(m["ev_km"]), "fuel_km": _r(max(0.0, m["ev_known_km"] - m["ev_km"])),
                        "ev_pct": _r(min(100.0, m["ev_km"] / m["ev_known_km"] * 100), 0) if m["ev_known_km"] > 0 else None})

    # ── consumption by average speed (a by-temperature chart was dropped on 2026-10-31: the car never reports the outside temperature) ──
    def buckets(key_fn, size: int, label_fn) -> List[Dict[str, Any]]:
        acc: Dict[float, Dict[str, float]] = defaultdict(lambda: defaultdict(float))
        for d in drives:
            value = key_fn(d)
            if value is None or not counts_for_ev_consumption(d):
                continue
            b = acc[_bucket(value, size)]
            b["km"] += d.distance_km
            b["kwh"] += d.energy_consumed_kwh
            b["n"] += 1
        return [{"label": label_fn(k, size), "from": k, "kwh_per_100km": _r(v["kwh"] / v["km"] * 100), "drives": int(v["n"])}
                for k, v in sorted(acc.items()) if v["km"] > 0]

    by_speed = buckets(lambda d: d.speed_avg_kmh if (d.speed_avg_kmh or 0) > 0 else None, 10, lambda k, s: f"{int(k)}–{int(k + s)} km/h")

    # ── usable capacity measured in big charges (their curve), per month, and against the sheet's capacity ──
    battery_health, battery_capacity_summary = await measured_battery_health(session, vin, charges, tz)

    # ── charges by place ──
    places: Dict[str, Dict[str, float]] = defaultdict(lambda: defaultdict(float))
    for c in charges:
        p = places[c.address or "Sin dirección"]
        p["kwh"] += energy_kwh(c)
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
    ev_total = sum(months[k]["ev_km"] for k in months)
    ev_known = sum(months[k]["ev_known_km"] for k in months)
    totals = {"km": _r(km), "kwh_charged": _r(sum(m["kwh_charged"] or 0 for m in monthly)), "liters": _r(sum(m["liters"] or 0 for m in monthly)),
              "cost_total": _r(cost_total, 2), "cost_per_km": _r(cost_total / km, 3) if km >= 20 else None, "drives": len(drives), "charges": len(charges),
              # electric consumption of the whole period: fully electric drives of ≥ MIN_DRIVE_KM with measured energy (the Energía tab shows the 30-day value)
              "kwh_per_100km": _r(eff_kwh / eff_km * 100) if eff_km >= MIN_DRIVE_KM else None,
              "ev_km": _r(ev_total), "ev_pct": _r(min(100.0, ev_total / ev_known * 100), 0) if ev_known > 0 else None}
    notes = ("Los kWh de viajes y cargas se estiman a partir del porcentaje de batería y la capacidad de la ficha; la capacidad de la batería se mide con la curva de "
             f"las cargas de al menos {int(battery_capacity.MIN_POINTS_GAINED)} puntos y el gasto en reposo de paradas de más de {int(IDLE_GAP_H)} horas sin carga.")
    return {"period": period, "from": frm, "to": to, "monthly": monthly, "consumption_by_speed": by_speed, "battery_health": battery_health, "battery_capacity": battery_capacity_summary,
            "charges_by_place": charges_by_place, "idle_drain": idle_drain, "totals": totals, "notes": notes}
