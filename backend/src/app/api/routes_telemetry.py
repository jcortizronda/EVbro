"""Telemetry routes (services/telemetry.py, docs/telemetry.md).

* `router` — public, server to server: another installation sends its daily report. Only answers on the project's server
  (`TELEMETRY_RECEIVER`); everywhere else it is a 404. Anonymous by design: no key, a random installation id, a size limit and at most
  one report every 30 minutes per id. The IP is not stored.
* `client_router` — protected: the web app of a signed-in user reports one of its JavaScript errors (core/error-report.js). It only
  counts while this server's reports are on.
"""
from __future__ import annotations

import hashlib
import json
import secrets
from typing import Any, Dict

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.db.session import get_db
from app.security import auth
from app.security.throttle import LoginThrottle
from app.services import telemetry

router = APIRouter(prefix="/telemetry", tags=["Telemetry"])
client_router = APIRouter(prefix="/telemetry", tags=["Telemetry"])

MAX_BODY = telemetry.MAX_REPORT_BYTES + 16 * 1024
MAX_CLIENT_BODY = 8 * 1024
# The receiver is public and takes reports from anybody: one address may send this many an hour (an installation sends one a day;
# several may share an address). The count lives in memory and is kept under a salted hash that changes with every start, so the
# address of whoever reports is never stored or logged: the reports stay anonymous.
REPORTS_PER_IP_HOUR = 20
report_throttle = LoginThrottle(max_failures=REPORTS_PER_IP_HOUR, window_s=3600)
_SALT = secrets.token_bytes(16)


def _sender_key(request: Request) -> str:
    return hashlib.sha256(_SALT + auth.client_ip(request).encode()).hexdigest()[:24]


@router.post("/v1/report")
async def receive_report(request: Request, session: AsyncSession = Depends(get_db)):
    """One report of another installation (`evbro-telemetry/1`)."""
    if not settings.TELEMETRY_RECEIVER:
        raise HTTPException(status_code=404, detail="No encontrado.")
    key = _sender_key(request)
    wait = report_throttle.retry_after(key)
    if wait:
        raise HTTPException(status_code=429, detail="Demasiados informes desde esta dirección. Vuelve a intentarlo más tarde.",
                            headers={"Retry-After": str(wait)})
    report_throttle.record_failure(key)                    # every report counts, accepted or not
    declared = request.headers.get("content-length", "")
    if declared.isdigit() and int(declared) > MAX_BODY:    # said up front: not even read
        raise HTTPException(status_code=413, detail="Informe demasiado grande.")
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
