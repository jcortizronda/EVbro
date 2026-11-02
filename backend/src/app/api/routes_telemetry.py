"""Telemetry routes (services/telemetry.py, docs/telemetry.md).

* `router` — public, server to server: another installation sends its daily report. Only answers on the project's server
  (`TELEMETRY_RECEIVER`); everywhere else it is a 404. Anonymous by design: no key, a random installation id, a size limit and at most
  one report every 30 minutes per id. The IP is not stored.
* `client_router` — protected: the web app of a signed-in user reports one of its JavaScript errors (core/error-report.js). It only
  counts while this server's reports are on.
"""
from __future__ import annotations

import json
from typing import Any, Dict

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.db.session import get_db
from app.services import telemetry

router = APIRouter(prefix="/telemetry", tags=["Telemetry"])
client_router = APIRouter(prefix="/telemetry", tags=["Telemetry"])

MAX_BODY = telemetry.MAX_REPORT_BYTES + 16 * 1024
MAX_CLIENT_BODY = 8 * 1024


@router.post("/v1/report")
async def receive_report(request: Request, session: AsyncSession = Depends(get_db)):
    """One report of another installation (`evbro-telemetry/1`)."""
    if not settings.TELEMETRY_RECEIVER:
        raise HTTPException(status_code=404, detail="No encontrado.")
    body = await request.body()
    if len(body) > MAX_BODY:
        raise HTTPException(status_code=413, detail="Informe demasiado grande.")
    try:
        data = json.loads(body or b"null")
    except ValueError as ex:
        raise HTTPException(status_code=422, detail="El informe no es JSON.") from ex
    try:
        return await telemetry.receive(session, data)
    except telemetry.ReportError as ex:
        raise HTTPException(status_code=ex.status_code, detail=ex.detail) from ex


@client_router.post("/client-error")
async def client_error(request: Request) -> Dict[str, Any]:
    """A JavaScript error of the web app; stored (scrubbed) only while the reports are on."""
    body = await request.body()
    if len(body) > MAX_CLIENT_BODY:
        raise HTTPException(status_code=413, detail="Demasiado grande.")
    try:
        data = json.loads(body or b"null")
    except ValueError as ex:
        raise HTTPException(status_code=422, detail="No es JSON.") from ex
    telemetry.record_web_error(data if isinstance(data, dict) else {})
    return {"ok": True, "collecting": telemetry.is_collecting()}
