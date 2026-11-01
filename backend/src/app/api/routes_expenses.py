"""Car expenses that are not maintenance, refuels or charges: insurance, taxes, garage, tolls, fines…

Workshop interventions go in Mantenimiento, refuels in `routes_refuels` and charges in Energía; the balance of everything, in Costes.
"""
from __future__ import annotations

import datetime
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import desc, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Expense, Vehicle
from app.db.session import get_db
from app.i18n import _
from app.services import periods

router = APIRouter(tags=["Expenses"])

CATEGORIES = {
    "insurance": "Seguro", "tax": "Impuestos", "parking": "Garaje y aparcamiento", "tolls": "Peajes",
    "fines": "Multas", "wash": "Lavado", "accessories": "Accesorios", "other": "Otros",
}


class ExpenseCreate(BaseModel):
    date: Optional[datetime.datetime] = None
    category: str
    title: str = Field(min_length=1, max_length=150)
    amount: float = Field(ge=0, le=1_000_000)
    notes: Optional[str] = Field(default=None, max_length=2000)
    next_due_date: Optional[datetime.datetime] = None


class ExpenseUpdate(BaseModel):
    """Fields not sent are left alone; `next_due_date: null` removes the reminder."""
    date: Optional[datetime.datetime] = None
    category: Optional[str] = None
    title: Optional[str] = Field(default=None, min_length=1, max_length=150)
    amount: Optional[float] = Field(default=None, ge=0, le=1_000_000)
    notes: Optional[str] = Field(default=None, max_length=2000)
    next_due_date: Optional[datetime.datetime] = None


def _check_category(category: str) -> None:
    if category not in CATEGORIES:
        raise HTTPException(status_code=422, detail=_("Categoría no válida. Usa una de: {p0}.", p0=', '.join(CATEGORIES)))


def _naive(d: Optional[datetime.datetime]) -> Optional[datetime.datetime]:
    return d.replace(tzinfo=None) if d else None


def serialize(e: Expense) -> dict:
    return {"id": e.id, "date": e.date.isoformat(), "category": e.category, "category_label": CATEGORIES.get(e.category, e.category),
            "title": e.title, "amount": e.amount, "notes": e.notes, "next_due_date": e.next_due_date.isoformat() if e.next_due_date else None}


@router.get("/expense-categories")
async def expense_categories():
    return [{"key": k, "label": v} for k, v in CATEGORIES.items()]


@router.get("/vehicles/{vin}/expenses")
async def list_expenses(vin: str, year: Optional[int] = Query(None, ge=1990, le=2100), period: Optional[str] = Query(None, max_length=12),
                        frm: Optional[str] = Query(None, alias="from", max_length=10), to: Optional[str] = Query(None, max_length=10),
                        session: AsyncSession = Depends(get_db)):
    """Car expenses, most recent first; `year` or else `period`/`from`/`to` (services/periods.py) filter by date."""
    stmt = select(Expense).where(Expense.vin == vin, periods.condition(Expense.date, periods.resolve(period, frm, to))).order_by(desc(Expense.date), desc(Expense.id))
    rows = (await session.execute(stmt)).scalars().all()
    if year:
        rows = [e for e in rows if e.date.year == year]
    return [serialize(e) for e in rows]


@router.post("/vehicles/{vin}/expenses")
async def create_expense(vin: str, req: ExpenseCreate, session: AsyncSession = Depends(get_db)):
    _check_category(req.category)
    if (await session.execute(select(Vehicle.id).where(Vehicle.vin == vin))).first() is None:
        raise HTTPException(status_code=404, detail="Vehículo no encontrado.")
    date = _naive(req.date) or datetime.datetime.utcnow()
    due = _naive(req.next_due_date)
    if due and due <= date:
        raise HTTPException(status_code=422, detail="La próxima fecha debe ser posterior a la del gasto.")
    e = Expense(vin=vin, date=date, category=req.category, title=req.title.strip(), amount=round(req.amount, 2), notes=req.notes, next_due_date=due)
    session.add(e)
    await session.commit()
    return serialize(e)


@router.put("/expenses/{expense_id}")
async def update_expense(expense_id: int, req: ExpenseUpdate, session: AsyncSession = Depends(get_db)):
    e = await session.get(Expense, expense_id)
    if e is None:
        raise HTTPException(status_code=404, detail="Gasto no encontrado.")
    sent = req.model_fields_set
    if req.category is not None:
        _check_category(req.category)
        e.category = req.category
    if req.title is not None:
        e.title = req.title.strip()
    if req.amount is not None:
        e.amount = round(req.amount, 2)
    if req.date is not None:
        e.date = _naive(req.date)
    if "notes" in sent:
        e.notes = req.notes
    if "next_due_date" in sent:
        e.next_due_date = _naive(req.next_due_date)
    if e.next_due_date and e.next_due_date <= e.date:
        raise HTTPException(status_code=422, detail="La próxima fecha debe ser posterior a la del gasto.")
    await session.commit()
    return serialize(e)


@router.delete("/expenses/{expense_id}")
async def delete_expense(expense_id: int, session: AsyncSession = Depends(get_db)):
    e = await session.get(Expense, expense_id)
    if e is None:
        raise HTTPException(status_code=404, detail="Gasto no encontrado.")
    await session.delete(e)
    await session.commit()
    return {"ok": True}
