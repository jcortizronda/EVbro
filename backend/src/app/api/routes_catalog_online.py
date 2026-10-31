"""Server to server: another EVBro installation reads this server's approved catalogue or sends a proposal with its catalogue key.

No session and no cookies: the caller is a server, authenticated by `Authorization: Bearer ebc_…` (services/catalog_remote.py).
A wrong key answers 401 and counts against the caller's IP like a failed login.
"""
from __future__ import annotations

from typing import Any, Dict

from fastapi import APIRouter, Depends, Header, HTTPException, Request
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import CatalogKey
from app.db.session import get_db
from app.security import auth
from app.security.throttle import LoginThrottle
from app.services import catalog, catalog_remote

router = APIRouter(prefix="/catalog/online", tags=["Catalog online"])
ip_throttle = LoginThrottle(max_failures=10)


async def _key(request: Request, authorization: str = Header(default=""), session: AsyncSession = Depends(get_db)) -> CatalogKey:
    ip = auth.client_ip(request)
    wait = ip_throttle.retry_after(ip)
    if wait:
        raise HTTPException(status_code=429, detail="Demasiados intentos; espera un momento.", headers={"Retry-After": str(wait)})
    key = await catalog_remote.authenticate(session, authorization)
    if key is None:
        ip_throttle.record_failure(ip)
        raise HTTPException(status_code=401, detail="Clave del catálogo no válida.")
    return key


@router.get("/export")
async def online_export(key: CatalogKey = Depends(_key), session: AsyncSession = Depends(get_db)):
    """This server's approved catalogue (`evbro-catalog/2`) for an installation that holds a key."""
    data = await catalog.export_approved(session)
    await catalog_remote.mark_download(session, key)
    return data


@router.post("/propose")
async def online_propose(payload: Dict[str, Any], key: CatalogKey = Depends(_key), session: AsyncSession = Depends(get_db)):
    """A proposal from another installation enters this server's review queue, tagged with the key's note."""
    if not isinstance(payload, dict) or len(str(payload)) > 20_000:
        raise HTTPException(status_code=422, detail="Propuesta no válida.")
    try:
        entry = await catalog_remote.receive_proposal(session, key, payload)
    except catalog_remote.RemoteError as ex:
        raise HTTPException(status_code=ex.status_code, detail=ex.detail) from ex
    return {"id": entry.id, "status": entry.status}
