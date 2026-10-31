"""The car's timeline (every dated event in one list) and the global search across the account's cars."""
from __future__ import annotations

import datetime
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import AppUser, Vehicle
from app.db.session import get_db
from app.i18n import _
from app.security import auth
from app.security.access import accessible_vehicles
from app.services import periods, timeline

router = APIRouter(tags=["Timeline"])


@router.get("/vehicles/{vin}/timeline")
async def get_timeline(vin: str, kinds: str = Query("", max_length=200), before: Optional[datetime.datetime] = None,
                       limit: int = Query(40, ge=1, le=timeline.MAX_LIMIT), period: Optional[str] = Query(None, max_length=12),
                       frm: Optional[str] = Query(None, alias="from", max_length=10), to: Optional[str] = Query(None, max_length=10),
                       session: AsyncSession = Depends(get_db)):
    """Newest events of the car, all kinds mixed (`kinds=drive,charge,…` narrows; `before` pages backwards; `period`/`from`/`to` bound the dates)."""
    if (await session.execute(select(Vehicle.id).where(Vehicle.vin == vin))).scalar_one_or_none() is None:
        raise HTTPException(status_code=404, detail="Vehículo no encontrado.")
    wanted = [k.strip() for k in kinds.split(",") if k.strip()]
    if any(k not in timeline.KINDS for k in wanted):
        raise HTTPException(status_code=422, detail=_("Tipo no válido; admitidos: {p0}.", p0=', '.join(timeline.KINDS)))
    return await timeline.events(session, vin, wanted, before, limit, periods.resolve(period, frm, to))


@router.get("/search")
async def global_search(q: str = Query("", max_length=120), limit: int = Query(40, ge=1, le=100),
                        session: AsyncSession = Depends(get_db), user: AppUser = Depends(auth.require_auth)):
    """Text search over parts, interventions, expenses, documents, refuels and drives of every car the account can see."""
    levels = await accessible_vehicles(session, user)
    names = {vin: name for vin, name in (await session.execute(select(Vehicle.vin, Vehicle.name).where(Vehicle.vin.in_(list(levels))))).all()} if levels else {}
    hits = await timeline.search(session, names, q, limit)
    return {"query": " ".join(q.split()), "hits": hits}
