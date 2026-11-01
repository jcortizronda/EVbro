"""Charge control: battery target, stop/start and scheduling (all through the car's charge schedule)."""
from __future__ import annotations

import re
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Vehicle
from app.db.session import get_db
from app.security.auth import client_ip
from app.services.charge_manager import ChargeError, ChargeManager
from app.vehicle.charging import duration_between

router = APIRouter(prefix="/vehicles/{vin}/charge", tags=["ChargePlan"])

_HHMM = re.compile(r"^([01]\d|2[0-3]):([0-5]\d)$")


class TargetRequest(BaseModel):
    target_pct: int


class ScheduleRequest(BaseModel):
    start: str                              # "HH:MM", local time
    end: Optional[str] = None               # "HH:MM"; may go past midnight
    duration_min: Optional[int] = None      # alternativa a `end`


def _minutes(hhmm: str, field: str) -> int:
    m = _HHMM.match((hhmm or "").strip())
    if not m:
        raise ChargeError("time_invalid", f"La hora «{field}» no es válida (usa HH:MM).")
    return int(m.group(1)) * 60 + int(m.group(2))


async def _check_vehicle(vin: str, session: AsyncSession) -> None:
    if (await session.execute(select(Vehicle.vin).where(Vehicle.vin == vin))).first() is None:
        raise HTTPException(status_code=404, detail="Vehículo no encontrado.")


def _error(err: ChargeError) -> JSONResponse:
    return JSONResponse(status_code=err.status, content={"success": False, "detail": err.message, "reason": err.reason})


@router.get("/plan")
async def get_plan(vin: str, live: bool = False, session: AsyncSession = Depends(get_db)):
    """Charge control status. `live=true` adds the schedule the car actually has (asks the cloud)."""
    await _check_vehicle(vin, session)
    return await ChargeManager.get_instance().state(vin, live=live)


@router.put("/target")
async def put_target(vin: str, req: TargetRequest, request: Request, session: AsyncSession = Depends(get_db)):
    """Battery target. 100 % removes the limit; below that, charging pauses when it is reached."""
    await _check_vehicle(vin, session)
    try:
        return await ChargeManager.get_instance().set_target(vin, req.target_pct, f"web {client_ip(request)}")
    except ChargeError as err:
        return _error(err)


@router.post("/stop")
async def post_stop(vin: str, request: Request, session: AsyncSession = Depends(get_db)):
    """Stop charging: schedules a window that has already ended (the car has no direct stop)."""
    await _check_vehicle(vin, session)
    try:
        return await ChargeManager.get_instance().stop(vin, f"web {client_ip(request)}")
    except ChargeError as err:
        return _error(err)


@router.post("/start")
async def post_start(vin: str, request: Request, session: AsyncSession = Depends(get_db)):
    """Start charging: removes the schedule or the pause so the car charges now."""
    await _check_vehicle(vin, session)
    try:
        return await ChargeManager.get_instance().start(vin, f"web {client_ip(request)}")
    except ChargeError as err:
        return _error(err)


@router.put("/schedule")
async def put_schedule(vin: str, req: ScheduleRequest, request: Request, session: AsyncSession = Depends(get_db)):
    """Schedules charging with a start time and (an end time or a duration)."""
    await _check_vehicle(vin, session)
    try:
        start = _minutes(req.start, "inicio")
        if (req.end is None) == (req.duration_min is None):
            raise ChargeError("schedule_invalid", "Indica la hora de fin o la duración (solo una de las dos).")
        duration = req.duration_min if req.end is None else duration_between(start, _minutes(req.end, "fin"))
        if req.end is not None and duration == 0:
            raise ChargeError("schedule_invalid", "La hora de inicio y la de fin no pueden ser la misma.")
        return await ChargeManager.get_instance().set_schedule(vin, start, int(duration), f"web {client_ip(request)}")
    except ChargeError as err:
        return _error(err)


@router.delete("/schedule")
async def delete_schedule(vin: str, request: Request, session: AsyncSession = Depends(get_db)):
    """Removes the charge schedule."""
    await _check_vehicle(vin, session)
    try:
        return await ChargeManager.get_instance().clear_schedule(vin, f"web {client_ip(request)}")
    except ChargeError as err:
        return _error(err)
