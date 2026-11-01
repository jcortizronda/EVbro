"""Maintenance alerts by plan mode (official or custom). Only reads the DB; sends nothing."""
from __future__ import annotations

import datetime
from typing import Any, Dict, List, Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.routes_maintenance import calculate_item_health
from app.db.models import MaintenanceItem, MaintenancePlan, Vehicle
from app.services import maintenance_official as official


async def collect_alerts(session: AsyncSession, vin: str, odometer: float, now: Optional[datetime.datetime] = None) -> List[Dict[str, Any]]:
    """What is upcoming or overdue right now. Each alert carries a stable `key` so it is not repeated every day."""
    now = now or datetime.datetime.utcnow()
    vehicle = (await session.execute(select(Vehicle).where(Vehicle.vin == vin))).scalar_one_or_none()
    plan = await session.get(MaintenancePlan, vin)
    mode = plan.mode if plan else "official"
    if vehicle is None or not official.is_ebro(vehicle.brand):
        mode = "custom"                                    # the official plan is EBRO's: other brands use their own tasks
    alerts: List[Dict[str, Any]] = []
    from app.api.routes_maintenance_plan import _itv, _official_logs  # (avoids a circular import at startup)

    # The ITV applies to both modes: it counts from the registration date
    if vehicle is not None:
        itv = await _itv(session, vehicle, now.date())
        if itv["status"] in ("warning", "due"):
            alerts.append({
                "key": f"itv:{itv['status']}", "label": "la ITV", "remaining_km": None, "remaining_days": itv["remaining_days"],
                "is_overdue": itv["status"] == "due", "items": [], "labor_hours": None,
            })

    if mode == "official":
        logs = await _official_logs(session, vin)
        last_done = max((log.revision_km for log in logs), default=None)
        nxt = official.official_next(
            odometer, now.date(), last_done,
            (vehicle.delivery_date if vehicle else None) or (plan.baseline_date if plan else None),
            (plan.baseline_odometer if plan else 0.0) or 0.0,
            vehicle.model if vehicle else None,
        )
        if nxt and nxt["status"] != "ok":
            alerts.append({
                "key": f"official:{nxt['km']}:{nxt['status']}", "label": f"revisión oficial de {nxt['label']}",
                "remaining_km": nxt["remaining_km"], "remaining_days": nxt["remaining_days"],
                "is_overdue": nxt["status"] == "due",
                "items": [i["name"] for i in nxt["replace_items"]], "labor_hours": nxt["labor_hours"],
            })
        return alerts

    items = (await session.execute(
        select(MaintenanceItem).where(MaintenanceItem.vin == vin, MaintenanceItem.origin == "custom")
    )).scalars().all()
    for item in items:
        h = calculate_item_health(item, odometer, now)
        if h["status"] != "ok":
            alerts.append({
                "key": f"item:{item.id}:{h['status']}", "label": item.name,
                "remaining_km": h["remaining_km"], "remaining_days": h["remaining_days"],
                "is_overdue": h["status"] == "due", "items": [], "labor_hours": None,
            })
    return alerts
