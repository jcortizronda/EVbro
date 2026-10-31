"""Shared catalogue of consumables, as the app sees it: the approved entries, my proposals, and proposing one of my parts."""
from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import AppUser, VehiclePart
from app.db.session import get_db
from app.security import auth
from app.services import catalog, catalog_remote
from app.services.parts import CATEGORY_BY_KEY

router = APIRouter(tags=["Catalog"])


class ProposalIn(BaseModel):
    years: Optional[str] = Field(default=None, max_length=20)
    source: str = Field(default="manual", max_length=30)


@router.get("/catalog")
async def list_catalog(model: Optional[str] = Query(default=None, max_length=80), category: Optional[str] = Query(default=None, max_length=30),
                       limit: int = Query(20, ge=1, le=100), session: AsyncSession = Depends(get_db)):
    """Approved entries for a model (case-insensitive) and category: what other owners of the same car use."""
    if category and category not in CATEGORY_BY_KEY:
        raise HTTPException(status_code=422, detail="Categoría desconocida.")
    return await catalog.serialize_many(session, await catalog.approved(session, model, category, limit))


@router.get("/catalog/mine")
async def my_proposals(session: AsyncSession = Depends(get_db), user: AppUser = Depends(auth.require_auth)):
    return await catalog.serialize_many(session, await catalog.mine(session, user.id))


@router.post("/parts/{part_id}/propose")
async def propose_part(part_id: int, req: ProposalIn, session: AsyncSession = Depends(get_db), user: AppUser = Depends(auth.require_auth)):
    """Send one of my parts to the catalogue; public only when the administrator approves it."""
    part = await session.get(VehiclePart, part_id)
    if part is None:
        raise HTTPException(status_code=404, detail="Pieza no encontrada.")
    try:
        entry = await catalog.propose_from_part(session, part, user.id, years=req.years, source=req.source)
    except catalog.CatalogError as ex:
        raise HTTPException(status_code=ex.status_code, detail=ex.detail) from ex
    await catalog_remote.maybe_forward(session, entry)          # online catalogue: the proposal also goes to that server's queue
    return await catalog.serialize_one(session, entry)
