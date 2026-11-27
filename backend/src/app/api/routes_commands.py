"""Routes to run remote commands and read the audit log."""
from __future__ import annotations

import asyncio
import datetime
import logging
import time
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import desc, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core import commands
from app.core.catalog import CMD_MAP, COMMANDS
from app.db.models import AppUser, CommandAudit, Vehicle
from app.db.session import get_db
from app.i18n import _
from app.security import auth
from app.security.access import space_owner_id
from app.services import cloud_usage, command_policy
from app.services.cloud_pool import CLOUD_EXECUTOR
from app.services.command_policy import requires_pin
from app.services.poller import VehiclePoller

_LOGGER = logging.getLogger(__name__)

router = APIRouter(tags=["Commands"])


class CommandRequest(BaseModel):
    command: str = Field(max_length=64)
    pin: Optional[str] = Field(default=None, max_length=32)
    params: Optional[Dict[str, Any]] = None             # whitelisted per command in `command_policy.sanitize_params`


class CommandAuditOut(BaseModel):
    id: int
    vin: str
    timestamp: datetime.datetime
    command: str
    params: Optional[Dict[str, Any]] = None
    success: bool
    code: Optional[str] = None
    message: Optional[str] = None
    actor: Optional[str] = None                         # the account that sent it (None: a schedule, MQTT, or before 26-11-2026)
    car_result: Optional[str] = None                    # what the car answered over its channel, if its confirmation was seen
    car_result_at: Optional[datetime.datetime] = None

    model_config = ConfigDict(from_attributes=True)


@router.get("/commands/catalog")
async def get_commands_catalog(session: AsyncSession = Depends(get_db), user: AppUser = Depends(auth.require_auth)):
    """Returns the catalogue of every command Ebro supports; `requires_pin` follows the policy of the account's manager."""
    owner_id = space_owner_id(user)
    policy = (await session.execute(select(AppUser.pin_policy).where(AppUser.id == owner_id))).scalar_one_or_none() if owner_id else None
    policy = policy if policy in command_policy.POLICIES else command_policy.DEFAULT_POLICY
    catalog = []
    for key, data in COMMANDS:
        catalog.append({
            "key": key,
            "name": data.get("name"),
            "group": data.get("group"),
            "icon": data.get("icon"),
            "requires_pin": requires_pin(key, policy),
            # what is sent to the car (the command's own fields; the API adds vin, taskId and signature)
            "endpoint": data.get("endpoint") or data.get("path"),
            "body": data.get("body"),
        })
    return catalog


# HTTP status from the stable `reason` of the result. 401 is reserved for "you are not logged in
# to EVbro" (the web app reacts to it by showing the login), which is why an expired Ebro account
# session uses 424.
_STATUS_BY_REASON = {
    "pin_required": 422,
    "params_invalid": 422,
    "pin_invalid": 403,
    "pin_locked": 429,
    "pin": 422,
    "reauth": 424,
    "config": 422,
}


@router.post("/vehicles/{vin}/commands")
async def execute_vehicle_command(
    vin: str,
    req: CommandRequest,
    request: Request,
    session: AsyncSession = Depends(get_db),
    user: AppUser = Depends(auth.require_auth),
):
    """Sends a remote command to the vehicle (lock, climate, lights, etc.).

    Commands that open or disarm (`requires_pin` in the catalogue) require the PIN in the request
    itself. See `services/command_policy.py`."""
    if req.command not in CMD_MAP:
        raise HTTPException(
            status_code=400,
            detail=_("Comando desconocido: '{p0}'. Consulta /api/commands/catalog", p0=req.command),
        )
    if (await session.execute(select(Vehicle.vin).where(Vehicle.vin == vin))).first() is None:
        raise HTTPException(status_code=404, detail="Vehículo no encontrado.")

    poller = VehiclePoller.get_instance()

    result = await poller.execute_command(
        vin=vin,
        command_key=req.command,
        pin=req.pin or "",
        params=req.params,
        client_ip=auth.client_ip(request),
        actor=user.username,
    )

    if not result.get("success"):
        reason = result.get("reason")
        headers = {"Retry-After": str(result["retry_after"])} if result.get("retry_after") else None
        return JSONResponse(
            status_code=_STATUS_BY_REASON.get(reason, 422),
            headers=headers,
            content={
                "success": False,
                "detail": result.get("message") or _("Error al ejecutar el comando en el vehículo."),
                "reason": reason,
                "code": result.get("code"),           # the cloud's code (A00084…): the page names what it means (texts audit, 27-11-2026)
            },
        )

    return result


# How long one reading of the alarm serves every page that asks (the alarm is not in the telemetry: each reading is a question to
# the cloud). An alarm order sent through EVbro drops it; one made from the car itself shows within this time.
ALARM_CACHE_S = 120


@router.get("/vehicles/{vin}/alarm")
async def get_alarm_state(vin: str, session: AsyncSession = Depends(get_db)):
    """Real state of the anti-theft alarm (read only: runs nothing and needs no PIN).

    The alarm is not part of the telemetry: the car has to be asked. `enabled` is `None` if it could not be read."""
    if (await session.execute(select(Vehicle.vin).where(Vehicle.vin == vin))).first() is None:
        raise HTTPException(status_code=404, detail="Vehículo no encontrado.")
    poller = VehiclePoller.get_instance()
    cached = poller._alarm_cache.get(vin)
    if cached is not None and time.monotonic() - cached[0] < ALARM_CACHE_S:
        return {"enabled": bool(cached[1])}
    ctx = poller._get_or_create_ctx(vin)
    loop = asyncio.get_running_loop()
    try:
        cloud_usage.count(cloud_usage.QUERY)
        async with poller.cloud_lock(vin):                   # one conversation with the cloud per account at a time
            value = await loop.run_in_executor(CLOUD_EXECUTOR, commands.query_theft_switch, ctx)
    except OSError as exc:
        # No Ebro session stored for this car (its account was never connected) or no network: «unknown», not a server error.
        # The page asks every few minutes while Inicio is on screen, so this used to fill the log with tracebacks (seen with the demo car, 09-11-2026).
        _LOGGER.info("Alarm of %s not read: %s", vin[-6:], type(exc).__name__)
        value = None
    if value is not None:
        poller._alarm_cache[vin] = (time.monotonic(), value)
    return {"enabled": None if value is None else bool(value)}


@router.get("/vehicles/{vin}/commands/history", response_model=List[CommandAuditOut])
async def get_commands_history(
    vin: str,
    limit: int = Query(50, ge=1, le=500),
    session: AsyncSession = Depends(get_db),
):
    """Reads the audit log of commands run on the vehicle."""
    stmt = (
        select(CommandAudit)
        .where(CommandAudit.vin == vin)
        .order_by(desc(CommandAudit.timestamp))
        .limit(limit)
    )
    result = await session.execute(stmt)
    return result.scalars().all()
