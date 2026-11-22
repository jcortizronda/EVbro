"""Scheduled departure («Reserva tu viaje»): what the car has and changing it. services/departure.py."""
from __future__ import annotations

from typing import List

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field
from sqlalchemy import desc, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_timezone
from app.db.models import TelemetrySnapshot, Vehicle
from app.db.session import get_db
from app.security.auth import client_ip
from app.services import departure
from app.services.poller import VehiclePoller

router = APIRouter(prefix="/vehicles/{vin}/departure", tags=["Departure"])


class Departure(BaseModel):
    time: str = Field(max_length=5)        # "HH:MM", local time
    enabled: bool = False


class DepartureRequest(BaseModel):
    departures: List[Departure] = Field(min_length=1, max_length=departure.MAX_DEPARTURES)


async def _raw(vin: str, session: AsyncSession) -> dict:
    """The car's latest raw reading: the one in memory, or the last one saved (after a restart nothing is in memory yet)."""
    if (await session.execute(select(Vehicle.vin).where(Vehicle.vin == vin))).first() is None:
        raise HTTPException(status_code=404, detail="Vehículo no encontrado.")
    latest = VehiclePoller.get_instance().get_latest(vin) or {}
    raw = latest.get("raw_data")
    if not raw:
        raw = (await session.execute(select(TelemetrySnapshot.raw_data).where(TelemetrySnapshot.vin == vin)
                                     .order_by(desc(TelemetrySnapshot.timestamp)).limit(1))).scalar_one_or_none()
    return raw or {}


@router.get("")
async def get_departure(vin: str, session: AsyncSession = Depends(get_db)):
    """The scheduled departures the car has (local time). `known` is False if the cloud has not returned them for this car."""
    return departure.read_plan(await _raw(vin, session), get_timezone())


@router.put("")
async def put_departure(vin: str, req: DepartureRequest, request: Request, session: AsyncSession = Depends(get_db)):
    """Changes the time and the switch of the car's scheduled departures (their days stay as the car has them). It is sent with
    the PIN stored on the account, like the charge schedule."""
    raw = await _raw(vin, session)
    tz = get_timezone()
    try:
        key, params = departure.build_order([d.model_dump() for d in req.departures], raw, tz)
    except departure.DepartureError as err:
        return JSONResponse(status_code=422, content={"success": False, "detail": err.message, "reason": err.reason})
    result = await VehiclePoller.get_instance().execute_internal(vin, key, params, origin=f"web {client_ip(request)}")
    if not result.get("success"):
        return JSONResponse(status_code=424, content={"success": False, "detail": result.get("message") or "El coche no aceptó la orden.",
                                                       "reason": result.get("reason") or "error", "code": result.get("code")})
    # What was asked for, as the car will have it: the reading that follows the order confirms it
    sent = {"mainSwitcher": params["mainSwitcher"],
            "appointmentTravelSetVOS": [{"travelTime": e["travelTime"], "travelDate": e["travelWeekday"], "switcher": e["switcher"]}
                                        for e in params["appointmentTravelAOS"]]}
    return {"success": True, **departure.read_plan(sent, tz)}
