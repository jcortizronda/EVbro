"""Statistics of a car's history, ready to draw (Más → Estadísticas)."""
from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Vehicle
from app.db.session import get_db
from app.services import stats

router = APIRouter(tags=["Stats"])


@router.get("/vehicles/{vin}/stats")
async def vehicle_stats(vin: str, period: str = Query("12m", max_length=12), frm: Optional[str] = Query(None, alias="from", max_length=10),
                        to: Optional[str] = Query(None, max_length=10), session: AsyncSession = Depends(get_db)):
    """Per-month km, energy, litres and money; consumption by speed; observed battery capacity; charges by place; idle drain."""
    if (await session.execute(select(Vehicle.id).where(Vehicle.vin == vin))).scalar_one_or_none() is None:
        raise HTTPException(status_code=404, detail="Vehículo no encontrado.")
    return await stats.compute(session, vin, period, frm, to)
