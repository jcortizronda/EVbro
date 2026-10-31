"""Timetabled pre-conditioning (Confort → Programar climatización)."""
from __future__ import annotations

import re
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field, field_validator
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import ClimateSchedule, Vehicle
from app.db.session import get_db
from app.i18n import _
from app.services import climate_schedule

router = APIRouter(tags=["Climate schedules"])
MAX_PER_CAR = 10
_TIME = re.compile(r"^([01]\d|2[0-3]):[0-5]\d$")


class ScheduleIn(BaseModel):
    time_local: str = Field(min_length=5, max_length=5)
    days: List[int] = Field(default=[0, 1, 2, 3, 4], max_length=7)
    temperature: float = Field(default=22.0, ge=climate_schedule.TEMPERATURE_RANGE[0], le=climate_schedule.TEMPERATURE_RANGE[1])
    enabled: bool = True
    label: Optional[str] = Field(default=None, max_length=60)

    @field_validator("time_local")
    @classmethod
    def _time(cls, v: str) -> str:
        if not _TIME.match(v):
            raise ValueError("La hora debe ser HH:MM.")
        return v

    @field_validator("days")
    @classmethod
    def _days(cls, v: List[int]) -> List[int]:
        days = sorted({d for d in v if 0 <= d <= 6})
        if not days:
            raise ValueError("Elige al menos un día.")
        return days


class SchedulePatch(BaseModel):
    time_local: Optional[str] = Field(default=None, min_length=5, max_length=5)
    days: Optional[List[int]] = Field(default=None, max_length=7)
    temperature: Optional[float] = Field(default=None, ge=climate_schedule.TEMPERATURE_RANGE[0], le=climate_schedule.TEMPERATURE_RANGE[1])
    enabled: Optional[bool] = None
    label: Optional[str] = Field(default=None, max_length=60)

    _time = field_validator("time_local")(ScheduleIn._time.__func__)
    _days = field_validator("days")(ScheduleIn._days.__func__)


@router.get("/vehicles/{vin}/climate-schedules")
async def list_schedules(vin: str, session: AsyncSession = Depends(get_db)):
    rows = (await session.execute(select(ClimateSchedule).where(ClimateSchedule.vin == vin).order_by(ClimateSchedule.time_local))).scalars().all()
    return [climate_schedule.serialize(s) for s in rows]


@router.post("/vehicles/{vin}/climate-schedules")
async def create_schedule(vin: str, req: ScheduleIn, session: AsyncSession = Depends(get_db)):
    if (await session.execute(select(Vehicle.vin).where(Vehicle.vin == vin))).scalar_one_or_none() is None:
        raise HTTPException(status_code=404, detail="Vehículo no encontrado.")
    count = len((await session.execute(select(ClimateSchedule.id).where(ClimateSchedule.vin == vin))).all())
    if count >= MAX_PER_CAR:
        raise HTTPException(status_code=409, detail=_("Como mucho {p0} programaciones por coche.", p0=MAX_PER_CAR))
    s = ClimateSchedule(vin=vin, time_local=req.time_local, days=",".join(map(str, req.days)), temperature=req.temperature,
                        enabled=req.enabled, label=(req.label or "").strip() or None)
    session.add(s)
    await session.commit()
    return climate_schedule.serialize(s)


@router.put("/climate-schedules/{schedule_id}")
async def update_schedule(schedule_id: int, req: SchedulePatch, session: AsyncSession = Depends(get_db)):
    s = await session.get(ClimateSchedule, schedule_id)
    if s is None:
        raise HTTPException(status_code=404, detail="Programación no encontrada.")
    sent = req.model_fields_set
    if req.time_local is not None:
        s.time_local = req.time_local
    if req.days is not None:
        s.days = ",".join(map(str, req.days))
    if req.temperature is not None:
        s.temperature = req.temperature
    if req.enabled is not None:
        s.enabled = req.enabled
        if req.enabled:
            s.last_run_on = None                          # re-enabled: today counts again
    if "label" in sent:
        s.label = (req.label or "").strip() or None
    await session.commit()
    return climate_schedule.serialize(s)


@router.delete("/climate-schedules/{schedule_id}")
async def delete_schedule(schedule_id: int, session: AsyncSession = Depends(get_db)):
    s = await session.get(ClimateSchedule, schedule_id)
    if s is None:
        raise HTTPException(status_code=404, detail="Programación no encontrada.")
    await session.delete(s)
    await session.commit()
    return {"status": "ok", "deleted_id": schedule_id}
