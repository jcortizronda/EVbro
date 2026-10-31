"""Vencimientos con fecha: renovación del seguro (ficha del coche) y gastos con «próxima fecha» (impuesto de circulación, etc.).

Solo lee la BD. Cada aviso lleva una `key` estable y `repeat_hours`: cuánto esperar antes de repetirlo (el poller lo aplica).
"""
from __future__ import annotations

import datetime
from typing import Any, Dict, List, Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Expense, Vehicle

WARN_DAYS = 30           # desde cuándo se avisa
URGENT_DAYS = 7          # a partir de aquí se avisa a diario
CATEGORY_LABEL = {"insurance": "el seguro", "tax": "el impuesto", "parking": "el garaje", "tolls": "los peajes", "fines": "la multa",
                  "wash": "el lavado", "accessories": "los accesorios", "other": "el gasto"}


def _alert(key: str, label: str, due: datetime.date, today: datetime.date) -> Optional[Dict[str, Any]]:
    days = (due - today).days
    if days > WARN_DAYS:
        return None
    if days < 0:
        band, repeat = "overdue", 72
    elif days <= URGENT_DAYS:
        band, repeat = "urgent", 24
    else:
        band, repeat = "warning", 7 * 24
    return {"key": f"{key}:{band}", "label": label, "remaining_days": days, "is_overdue": days < 0, "repeat_hours": repeat}


async def collect_renewals(session: AsyncSession, vin: str, now: Optional[datetime.datetime] = None) -> List[Dict[str, Any]]:
    now = now or datetime.datetime.utcnow()
    today = now.date()
    out: List[Dict[str, Any]] = []
    vehicle = (await session.execute(select(Vehicle).where(Vehicle.vin == vin))).scalar_one_or_none()
    if vehicle is not None and vehicle.insurance_renewal_date:
        a = _alert("insurance", "la renovación del seguro", vehicle.insurance_renewal_date.date(), today)
        if a:
            out.append(a)

    # Un gasto con «próxima fecha» sigue vigente mientras no haya otro posterior de su misma categoría y título (el del año siguiente)
    rows = (await session.execute(select(Expense).where(Expense.vin == vin, Expense.next_due_date.is_not(None)))).scalars().all()
    latest: Dict[tuple, Expense] = {}
    for e in rows:
        k = (e.category, e.title.strip().lower())
        if k not in latest or e.date > latest[k].date:
            latest[k] = e
    for e in latest.values():
        # el seguro tiene su fecha en la ficha: no se avisa dos veces
        if e.category == "insurance" and vehicle is not None and vehicle.insurance_renewal_date:
            continue
        a = _alert(f"expense:{e.id}", f"{CATEGORY_LABEL.get(e.category, 'el gasto')} «{e.title}»" if e.category != "tax" else f"el impuesto «{e.title}»", e.next_due_date.date(), today)
        if a:
            out.append(a)
    return out
