"""Dated deadlines: the insurance renewal (car sheet) and expenses with a «próxima fecha» (road tax, etc.).

Only reads the DB. Each alert carries a stable `key` with its band (warning, urgent, overdue): the poller sends each key once.
"""
from __future__ import annotations

import datetime
from typing import Any, Dict, List, Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Expense, Vehicle
from app.timeutil import utcnow

WARN_DAYS = 30           # how early the alert starts
URGENT_DAYS = 7          # a second alert in the last week
CATEGORY_LABEL = {"insurance": "el seguro", "tax": "el impuesto", "parking": "el garaje", "tolls": "los peajes", "fines": "la multa",
                  "wash": "el lavado", "accessories": "los accesorios", "other": "el gasto"}


def _alert(key: str, label: str, due: datetime.date, today: datetime.date) -> Optional[Dict[str, Any]]:
    days = (due - today).days
    if days > WARN_DAYS:
        return None
    band = "overdue" if days < 0 else ("urgent" if days <= URGENT_DAYS else "warning")
    return {"key": f"{key}:{band}", "label": label, "remaining_days": days, "is_overdue": days < 0}


async def collect_renewals(session: AsyncSession, vin: str, now: Optional[datetime.datetime] = None) -> List[Dict[str, Any]]:
    now = now or utcnow()
    today = now.date()
    out: List[Dict[str, Any]] = []
    vehicle = (await session.execute(select(Vehicle).where(Vehicle.vin == vin))).scalar_one_or_none()
    if vehicle is not None and vehicle.insurance_renewal_date:
        a = _alert("insurance", "la renovación del seguro", vehicle.insurance_renewal_date.date(), today)
        if a:
            out.append(a)

    # an expense with a «próxima fecha» stays current until a later one with the same category and title appears (next year's)
    rows = (await session.execute(select(Expense).where(Expense.vin == vin, Expense.next_due_date.is_not(None)))).scalars().all()
    latest: Dict[tuple, Expense] = {}
    for e in rows:
        k = (e.category, e.title.strip().lower())
        if k not in latest or e.date > latest[k].date:
            latest[k] = e
    for e in latest.values():
        # the insurance has its date on the car sheet: do not alert twice
        if e.category == "insurance" and vehicle is not None and vehicle.insurance_renewal_date:
            continue
        a = _alert(f"expense:{e.id}", f"{CATEGORY_LABEL.get(e.category, 'el gasto')} «{e.title}»" if e.category != "tax" else f"el impuesto «{e.title}»", e.next_due_date.date(), today)
        if a:
            out.append(a)
    return out
