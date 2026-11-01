"""Status and control of each manager's car MQTT channel."""
from __future__ import annotations

from fastapi import APIRouter, Depends

from app.db.models import AppUser
from app.security import auth
from app.services.car_events import CarEventHub
from app.services.poller import VehiclePoller

router = APIRouter(prefix="/car-events", tags=["CarEvents"])


@router.get("/status")
async def car_events_status(user: AppUser = Depends(auth.require_auth)):
    """Status of the connection to this account's car broker and of what has been received."""
    return CarEventHub.get_instance().status_for(user.id)


@router.post("/reconnect")
async def car_events_reconnect(user: AppUser = Depends(auth.require_auth)):
    """Reconnects (after solving a clientId conflict, or after changing the account)."""
    hub = CarEventHub.get_instance()
    await hub.restart_owner(user.id, VehiclePoller.get_instance())
    return hub.status_for(user.id)
