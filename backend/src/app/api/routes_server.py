"""Server-wide settings that only the administrator touches (admin console): the polling rhythm of the cloud queries."""
from __future__ import annotations

from typing import Optional

from fastapi import APIRouter
from pydantic import BaseModel

from app.services.poller import VehiclePoller

router = APIRouter(prefix="/server", tags=["Server"])


class PollingConfigUpdate(BaseModel):
    parked_min: Optional[int] = None
    plugged_min: Optional[int] = None
    charging_min: Optional[int] = None
    driving_min: Optional[int] = None
    driving_idle_min: Optional[int] = None


@router.get("/polling")
async def get_polling_config():
    """Current adaptive polling configuration and state."""
    return VehiclePoller.get_instance().get_polling_status()


@router.post("/polling")
async def update_polling_config(payload: PollingConfigUpdate):
    """Updates the polling intervals per state."""
    return VehiclePoller.get_instance().update_polling_intervals(
        parked_min=payload.parked_min, plugged_min=payload.plugged_min, charging_min=payload.charging_min,
        driving_min=payload.driving_min, driving_idle_min=payload.driving_idle_min)
