"""Journeys: a named span of days whose drives, charges, refuels and expenses are added up (services/journeys.py)."""
from __future__ import annotations

import datetime
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Journey, Vehicle
from app.db.session import get_db
from app.i18n import _
from app.services import journeys

router = APIRouter(tags=["Journeys"])


class JourneyIn(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    start_time: datetime.datetime
    end_time: datetime.datetime
    notes: Optional[str] = Field(default=None, max_length=2000)


class JourneyUpdate(BaseModel):
    name: Optional[str] = Field(default=None, min_length=1, max_length=120)
    start_time: Optional[datetime.datetime] = None
    end_time: Optional[datetime.datetime] = None
    notes: Optional[str] = Field(default=None, max_length=2000)


def _naive(d: datetime.datetime) -> datetime.datetime:
    return d.astimezone(datetime.timezone.utc).replace(tzinfo=None) if d.tzinfo else d


def _check_span(start: datetime.datetime, end: datetime.datetime) -> None:
    if end <= start:
        raise HTTPException(status_code=422, detail="El final del viaje tiene que ser posterior al inicio.")
    if (end - start).days > journeys.MAX_DAYS:
        raise HTTPException(status_code=422, detail=_("Un viaje no puede durar más de {p0} días.", p0=journeys.MAX_DAYS))


async def _vehicle(session: AsyncSession, vin: str) -> None:
    if (await session.execute(select(Vehicle.id).where(Vehicle.vin == vin))).scalar_one_or_none() is None:
        raise HTTPException(status_code=404, detail="Vehículo no encontrado.")


async def _journey(session: AsyncSession, vin: str, journey_id: int) -> Journey:
    j = (await session.execute(select(Journey).where(Journey.id == journey_id, Journey.vin == vin))).scalar_one_or_none()
    if j is None:
        raise HTTPException(status_code=404, detail="Viaje no encontrado.")
    return j


@router.get("/vehicles/{vin}/journeys")
async def list_journeys(vin: str, session: AsyncSession = Depends(get_db)):
    """Every journey of the car, newest first, each with its km, cost and counts."""
    await _vehicle(session, vin)
    return await journeys.list_for(session, vin)


@router.post("/vehicles/{vin}/journeys/preview")
async def preview_journey(vin: str, req: JourneyIn, session: AsyncSession = Depends(get_db)):
    """The figures a span would give, without saving: the form shows them while the dates are being chosen."""
    await _vehicle(session, vin)
    start, end = _naive(req.start_time), _naive(req.end_time)
    _check_span(start, end)
    return {"summary": await journeys.summary(session, vin, start, end)}


@router.post("/vehicles/{vin}/journeys", status_code=201)
async def create_journey(vin: str, req: JourneyIn, session: AsyncSession = Depends(get_db)):
    await _vehicle(session, vin)
    start, end = _naive(req.start_time), _naive(req.end_time)
    _check_span(start, end)
    j = Journey(vin=vin, name=req.name.strip(), start_time=start, end_time=end, notes=(req.notes or "").strip() or None)
    session.add(j)
    await session.commit()
    await session.refresh(j)
    return journeys.serialize(j, await journeys.summary(session, vin, start, end, with_items=True))


@router.get("/vehicles/{vin}/journeys/{journey_id}")
async def get_journey(vin: str, journey_id: int, session: AsyncSession = Depends(get_db)):
    """One journey with every figure and the lists behind them."""
    j = await _journey(session, vin, journey_id)
    return journeys.serialize(j, await journeys.summary(session, vin, j.start_time, j.end_time, with_items=True))


@router.put("/vehicles/{vin}/journeys/{journey_id}")
async def update_journey(vin: str, journey_id: int, req: JourneyUpdate, session: AsyncSession = Depends(get_db)):
    j = await _journey(session, vin, journey_id)
    if req.name is not None:
        j.name = req.name.strip()
    if req.start_time is not None:
        j.start_time = _naive(req.start_time)
    if req.end_time is not None:
        j.end_time = _naive(req.end_time)
    if req.notes is not None:
        j.notes = req.notes.strip() or None
    _check_span(j.start_time, j.end_time)
    await session.commit()
    await session.refresh(j)
    return journeys.serialize(j, await journeys.summary(session, vin, j.start_time, j.end_time, with_items=True))


@router.delete("/vehicles/{vin}/journeys/{journey_id}")
async def delete_journey(vin: str, journey_id: int, session: AsyncSession = Depends(get_db)):
    """Removes the journey only: the drives, charges and expenses it added up stay where they are."""
    j = await _journey(session, vin, journey_id)
    await session.delete(j)
    await session.commit()
    return {"ok": True}
