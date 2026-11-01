"""Vehicle cost balance: purchase, charges, refuels, maintenance and expenses (insurance, taxes…). Read only:
entries are added in Energía (charges), Gastos (refuels and expenses) and Mantenimiento (interventions)."""
from __future__ import annotations

import datetime
from collections import defaultdict
from typing import Any, Dict, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.routes_expenses import CATEGORIES as EXPENSE_CATEGORIES
from app.db.models import ChargingSession, Drive, Expense, MaintenanceItem, MaintenanceLog, Refuel, Vehicle
from app.db.session import get_db
from app.services import periods

router = APIRouter(tags=["Costs"])

MAINT_ORIGIN_LABEL = {"official": "Revisión oficial", "itv": "ITV", "oneoff": "Intervención puntual", "custom": "Tarea propia"}


def _month(d: datetime.datetime) -> str:
    return f"{d.year}-{d.month:02d}"


@router.get("/vehicles/{vin}/costs")
async def get_costs(vin: str, period: str = Query("all"), frm: Optional[str] = Query(None, alias="from", max_length=10),
                    to: Optional[str] = Query(None, max_length=10), session: AsyncSession = Depends(get_db)) -> Dict[str, Any]:
    """Cost summary: totals per type, per month, per 100 km and the latest entries (`period` or `from`/`to`, services/periods.py)."""
    vehicle = (await session.execute(select(Vehicle).where(Vehicle.vin == vin))).scalar_one_or_none()
    if vehicle is None:
        raise HTTPException(status_code=404, detail="Vehículo no encontrado.")
    bounds = periods.resolve(period, frm, to)
    start = bounds[0]
    everything = periods.is_everything(period, frm, to)
    def inside(d: Optional[datetime.datetime]) -> bool:
        return periods.inside(d, bounds)

    charges = [c for c in (await session.execute(select(ChargingSession).where(ChargingSession.vin == vin))).scalars() if inside(c.start_time)]
    refuels = [r for r in (await session.execute(select(Refuel).where(Refuel.vin == vin))).scalars() if inside(r.timestamp)]
    maint = [(m, i) for m, i in (await session.execute(
        select(MaintenanceLog, MaintenanceItem).join(MaintenanceItem, MaintenanceLog.item_id == MaintenanceItem.id).where(MaintenanceItem.vin == vin)
    )).all() if inside(m.service_date)]

    expenses = [e for e in (await session.execute(select(Expense).where(Expense.vin == vin))).scalars() if inside(e.date)]
    sum_charges = round(sum(c.charge_cost or 0.0 for c in charges), 2)
    sum_refuels = round(sum(r.total_cost or 0.0 for r in refuels), 2)
    sum_maint = round(sum(m.cost or 0.0 for m, _ in maint), 2)
    sum_expenses = round(sum(e.amount or 0.0 for e in expenses), 2)
    purchase_date = vehicle.delivery_date or vehicle.registration_date          # the purchase counts from the delivery
    purchase = vehicle.purchase_price if (vehicle.purchase_price and (start is None or (purchase_date and purchase_date >= start))) else 0.0
    running = round(sum_charges + sum_refuels + sum_maint + sum_expenses, 2)

    # cost per 100 km: over the km driven since the purchase (period "all") or those of the drives recorded in the period
    km = None
    if everything and vehicle.odometer is not None:
        km = max(0.0, vehicle.odometer - (vehicle.purchase_odometer or 0.0))
    else:
        km = sum(d.distance_km or 0.0 for d in (await session.execute(select(Drive).where(Drive.vin == vin))).scalars() if inside(d.start_time)) or None

    monthly: Dict[str, Dict[str, float]] = defaultdict(lambda: {"charges": 0.0, "refuels": 0.0, "maintenance": 0.0, "expenses": 0.0})
    for c in charges:
        monthly[_month(c.start_time)]["charges"] += c.charge_cost or 0.0
    for r in refuels:
        monthly[_month(r.timestamp)]["refuels"] += r.total_cost or 0.0
    for m, _ in maint:
        monthly[_month(m.service_date)]["maintenance"] += m.cost or 0.0
    for e in expenses:
        monthly[_month(e.date)]["expenses"] += e.amount or 0.0
    by_category: Dict[str, float] = defaultdict(float)
    for e in expenses:
        by_category[e.category] += e.amount or 0.0

    recent = (
        [{"type": "charge", "date": c.start_time.isoformat(), "title": c.address or "Carga", "amount": round(c.charge_cost or 0.0, 2),
          "detail": f"{c.kwh_added or 0:.1f} kWh", "id": c.id, "pending": not c.cost_mode} for c in charges]
        + [{"type": "refuel", "date": r.timestamp.isoformat(), "title": r.station or r.address or "Repostaje", "amount": round(r.total_cost or 0.0, 2),
            "detail": f"{r.liters or 0:.1f} L", "id": r.id, "pending": r.total_cost is None} for r in refuels]
        + [{"type": "maintenance", "date": m.service_date.isoformat(), "title": m.title or i.name, "amount": round(m.cost or 0.0, 2),
            "detail": MAINT_ORIGIN_LABEL.get(i.origin, "Mantenimiento"), "id": m.id, "pending": False} for m, i in maint]
        + [{"type": "expense", "date": e.date.isoformat(), "title": e.title, "amount": round(e.amount or 0.0, 2),
            "detail": EXPENSE_CATEGORIES.get(e.category, e.category), "id": e.id, "pending": False} for e in expenses]
    )
    recent.sort(key=lambda x: x["date"], reverse=True)

    return {
        "period": period, "custom_from": frm, "custom_to": to,
        "from": start.isoformat() if start else None,
        "vehicle": {"purchase_price": vehicle.purchase_price, "purchase_date": purchase_date.isoformat() if purchase_date else None,
                    "purchase_odometer": vehicle.purchase_odometer, "odometer": vehicle.odometer},
        "totals": {"purchase": round(purchase, 2), "charges": sum_charges, "refuels": sum_refuels, "maintenance": sum_maint, "expenses": sum_expenses,
                   "running": running, "all": round(running + purchase, 2)},
        "energy": {"kwh": round(sum(c.kwh_added or 0.0 for c in charges), 1), "liters": round(sum(r.liters or 0.0 for r in refuels), 1)},
        "pending": {"charges": sum(1 for c in charges if not c.cost_mode), "refuels": sum(1 for r in refuels if r.total_cost is None)},
        "km": round(km, 0) if km else None,
        "per_100km": round(running / km * 100.0, 2) if km and km >= 50 else None,        # under 50 km the figure is meaningless
        "by_category": [{"key": k, "label": EXPENSE_CATEGORIES.get(k, k), "amount": round(v, 2)} for k, v in sorted(by_category.items(), key=lambda kv: -kv[1])],
        "monthly": [{"month": k, **{a: round(b, 2) for a, b in v.items()}} for k, v in sorted(monthly.items())][-12:],
        "recent": recent[:25],
    }
