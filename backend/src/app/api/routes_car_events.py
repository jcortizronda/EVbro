"""Estado y control del canal MQTT del coche de cada gestor."""
from __future__ import annotations

from fastapi import APIRouter, Depends

from app.db.models import AppUser
from app.security import auth
from app.services.car_events import CarEventHub
from app.services.poller import VehiclePoller

router = APIRouter(prefix="/car-events", tags=["CarEvents"])


@router.get("/status")
async def car_events_status(user: AppUser = Depends(auth.require_auth)):
    """Estado de la conexión con el broker del coche de esta cuenta y de lo recibido."""
    return CarEventHub.get_instance().status_for(user.id)


@router.post("/reconnect")
async def car_events_reconnect(user: AppUser = Depends(auth.require_auth)):
    """Reconecta (tras resolver un conflicto de clientId, o tras cambiar la cuenta)."""
    hub = CarEventHub.get_instance()
    await hub.restart_owner(user.id, VehiclePoller.get_instance())
    return hub.status_for(user.id)
