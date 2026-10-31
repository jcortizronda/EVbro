"""Your data: export the whole account as a ZIP and import such a ZIP (Ajustes → Cuenta → «Tus datos»)."""
from __future__ import annotations

import datetime

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from fastapi.responses import Response
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import AppUser, Vehicle
from app.db.session import get_db
from app.security import auth
from app.services import account_transfer, uploads

router = APIRouter(prefix="/account", tags=["Account data"])


@router.get("/export")
async def export_account(session: AsyncSession = Depends(get_db), user: AppUser = Depends(auth.require_auth)):
    """Everything of the manager's cars, decrypted files included, without secrets. The file is the user's: no size cap."""
    data = await account_transfer.export_account(session, user.id, user.username)
    name = f"evbro-{user.username}-{datetime.date.today().isoformat()}.zip"
    return Response(content=data, media_type="application/zip", headers={"Content-Disposition": uploads.content_disposition(name)})


@router.post("/import")
async def import_account(file: UploadFile = File(...), session: AsyncSession = Depends(get_db), user: AppUser = Depends(auth.require_auth)):
    blob = await uploads.read_limited(file, account_transfer.MAX_IMPORT_BYTES, "El fichero es demasiado grande (máximo 500 MB).")
    owned = (await session.execute(select(func.count()).select_from(Vehicle).where(Vehicle.owner_id == user.id))).scalar_one()
    room = None if user.vehicle_limit < 0 else max(0, user.vehicle_limit - owned)
    try:
        return await account_transfer.import_account(session, user.id, blob, room)
    except account_transfer.ImportError_ as ex:
        raise HTTPException(status_code=ex.status_code, detail=ex.detail) from ex
