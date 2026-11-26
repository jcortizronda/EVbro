"""EVbro's maintenance recommendations for a car (services/maintenance_advice.py): the list and the owner's three answers
(«Revisado, está bien», «Recuérdamelo más adelante», on/off). «Registrar intervención» is the usual one-off intervention
(`POST …/maintenance/events` with `advice_key`). Every route hangs from the car, so `security/access.py` asks for «view» to read
and «edit» to answer.
"""
from __future__ import annotations

import datetime
from typing import Any, Dict, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Vehicle
from app.db.session import get_db
from app.i18n import _, render
from app.services import maintenance_advice as advice
from app.timeutil import utcnow

router = APIRouter(tags=["Maintenance"])


class CheckRequest(BaseModel):
    """When it was looked at; empty = now, with the car's present odometer."""
    date: Optional[datetime.datetime] = None
    odometer: Optional[float] = Field(default=None, ge=0, le=2_000_000)


class EnabledRequest(BaseModel):
    enabled: bool


async def _vehicle(session: AsyncSession, vin: str, key: Optional[str] = None) -> Vehicle:
    vehicle = (await session.execute(select(Vehicle).where(Vehicle.vin == vin))).scalar_one_or_none()
    if vehicle is None:
        raise HTTPException(status_code=404, detail="Vehículo no encontrado.")
    if key is not None and key not in advice.ADVICE:
        raise HTTPException(status_code=404, detail=_("Esa recomendación no existe."))
    return vehicle


def _iso(value: Optional[datetime.datetime]) -> Optional[str]:
    return value.isoformat() if value else None


def _shown(a: Dict[str, Any]) -> Dict[str, Any]:
    """A recommendation as the page gets it: texts in the language of the request, dates as ISO text."""
    out = {k: a[k] for k in ("key", "status", "enabled", "category", "part", "can_log", "interval_km", "interval_months", "base_odometer",
                             "base_kind", "due_odometer", "remaining_km", "remaining_days", "snooze_odometer")}
    out.update(name=render(a["name"]), why=render(a["why"]), note=render(a["note"]) if a["note"] else None,
               base_date=_iso(a["base_date"]), due_date=_iso(a["due_date"]), snooze_until=_iso(a["snooze_until"]))
    return out


async def _listing(session: AsyncSession, vehicle: Vehicle) -> Dict[str, Any]:
    items = [_shown(a) for a in await advice.collect(session, vehicle)]
    return {"odometer": vehicle.odometer or 0.0, "advice": items, "due": sum(1 for a in items if a["status"] == "due")}


@router.get("/vehicles/{vin}/maintenance/advice")
async def list_advice(vin: str, session: AsyncSession = Depends(get_db)):
    """The recommendations that apply to this car: due ones first, then the postponed, the ones in time and the switched-off."""
    return await _listing(session, await _vehicle(session, vin))


@router.post("/vehicles/{vin}/maintenance/advice/{advice_key}/check")
async def check_advice(vin: str, advice_key: str, req: Optional[CheckRequest] = None, session: AsyncSession = Depends(get_db)):
    """«Revisado, está bien»: it counts again from now (or from the day and km given)."""
    vehicle = await _vehicle(session, vin, advice_key)
    req = req or CheckRequest()
    if req.date is not None and req.date.replace(tzinfo=None) > utcnow() + datetime.timedelta(days=1):
        raise HTTPException(status_code=422, detail=_("La fecha no puede ser futura."))
    await advice.mark_checked(session, vehicle, advice_key, req.date, req.odometer)
    await session.commit()
    return await _listing(session, vehicle)


@router.post("/vehicles/{vin}/maintenance/advice/{advice_key}/snooze")
async def snooze_advice(vin: str, advice_key: str, session: AsyncSession = Depends(get_db)):
    """«Recuérdamelo más adelante»: it comes back in 30 days or 1,000 km, whichever comes first."""
    vehicle = await _vehicle(session, vin, advice_key)
    await advice.snooze(session, vehicle, advice_key)
    await session.commit()
    return await _listing(session, vehicle)


@router.put("/vehicles/{vin}/maintenance/advice/{advice_key}")
async def enable_advice(vin: str, advice_key: str, req: EnabledRequest, session: AsyncSession = Depends(get_db)):
    """Switches one recommendation on or off for this car."""
    vehicle = await _vehicle(session, vin, advice_key)
    await advice.set_enabled(session, vehicle, advice_key, req.enabled)
    await session.commit()
    return await _listing(session, vehicle)
