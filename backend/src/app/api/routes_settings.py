"""Application settings (not of a vehicle): history retention."""
from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import get_db
from app.services import retention

router = APIRouter(prefix="/settings", tags=["Settings"])


class RetentionUpdate(BaseModel):
    """Days each type of history is kept; 0 = forever. Fields not sent are left alone."""
    trips_days: Optional[int] = Field(default=None, ge=0, le=retention.MAX_DAYS)
    charges_days: Optional[int] = Field(default=None, ge=0, le=retention.MAX_DAYS)
    snapshots_days: Optional[int] = Field(default=None, ge=0, le=retention.MAX_DAYS)


async def _state(session: AsyncSession):
    return {"config": await retention.get_config(session), "stored": await retention.counts(session), "max_days": retention.MAX_DAYS}


@router.get("/retention")
async def get_retention(session: AsyncSession = Depends(get_db)):
    """Current retention periods and how much of each type is stored."""
    return await _state(session)


@router.put("/retention")
async def put_retention(req: RetentionUpdate, session: AsyncSession = Depends(get_db)):
    await retention.set_config(session, req.model_dump(exclude_unset=True))
    return await _state(session)


@router.post("/retention/run")
async def run_retention(session: AsyncSession = Depends(get_db)):
    """Applies the retention periods now and says how many records were deleted."""
    deleted = await retention.apply(session)
    return {"deleted": deleted, **await _state(session)}
