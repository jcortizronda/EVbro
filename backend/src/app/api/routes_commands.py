"""Routes to run remote commands and read the audit log."""
from __future__ import annotations

import asyncio
import datetime
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field
from sqlalchemy import desc, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core import commands
from app.core.catalog import CMD_MAP, COMMANDS
from app.db.models import AppUser, CommandAudit, Vehicle
from app.db.session import get_db
from app.i18n import _
from app.security import auth
from app.security.access import space_owner_id
from app.services import command_policy
from app.services.command_policy import requires_pin
from app.services.poller import VehiclePoller

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

    class Config:
        from_attributes = True


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
# to EVBro" (the web app reacts to it by showing the login), which is why an expired Ebro account
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
    )

    if not result.get("success"):
        reason = result.get("reason")
        headers = {"Retry-After": str(result["retry_after"])} if result.get("retry_after") else None
        return JSONResponse(
            status_code=_STATUS_BY_REASON.get(reason, 422),
            headers=headers,
            content={
                "success": False,
                "detail": result.get("message") or "Error al ejecutar el comando en el vehículo.",
                "reason": reason,
            },
        )

    return result


@router.get("/vehicles/{vin}/alarm")
async def get_alarm_state(vin: str, session: AsyncSession = Depends(get_db)):
    """Real state of the anti-theft alarm (read only: runs nothing and needs no PIN).

    The alarm is not part of the telemetry: the car has to be asked. `enabled` is `None` if it could not be read."""
    if (await session.execute(select(Vehicle.vin).where(Vehicle.vin == vin))).first() is None:
        raise HTTPException(status_code=404, detail="Vehículo no encontrado.")
    ctx = VehiclePoller.get_instance()._get_or_create_ctx(vin)
    loop = asyncio.get_running_loop()
    value = await loop.run_in_executor(None, commands.query_theft_switch, ctx)
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
