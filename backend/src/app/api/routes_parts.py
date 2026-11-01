"""Parts and consumables of a vehicle (the "what does my car carry" sheet) and the list of known categories."""
from __future__ import annotations

from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import CatalogEntry, MaintenanceLog, Vehicle, VehiclePart
from app.db.session import get_db
from app.services.parts import (
    GROUPS,
    PART_CATEGORIES,
    POSITIONS,
    TYRE_SEASONS,
    PartIn,
    apply_fields,
    part_matches,
    record_parts_used,
    serialize_part,
    upsert_part,
)

router = APIRouter(tags=["Parts"])


async def record_parts(session: AsyncSession, vin: str, log: MaintenanceLog, parts: Optional[List[PartIn]], update_sheet: bool) -> None:
    """Used by the intervention endpoints: stores the parts on the log (and the sheet) turning bad input into a 422."""
    try:
        await record_parts_used(session, vin, log, parts, update_sheet)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


def _clean(part: PartIn) -> PartIn:
    try:
        return part.normalized()
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


async def _from_catalog(session: AsyncSession, part: PartIn) -> PartIn:
    """A part copied from the catalogue may only point at an approved entry of the same category (never at someone's proposal)."""
    if part.catalog_id is not None:
        entry = await session.get(CatalogEntry, part.catalog_id)
        if entry is None or entry.status != "approved" or entry.category != part.category:
            part.catalog_id = None
    return part


@router.get("/part-categories")
async def part_categories():
    """Categories offered as suggestions, with their display groups, positions and the field labels each one uses."""
    return {"categories": PART_CATEGORIES, "groups": [{"key": k, "label": v} for k, v in GROUPS.items()],
            "positions": [{"key": k, "label": v} for k, v in POSITIONS.items()],
            "tyre_seasons": [{"key": k, "label": v} for k, v in TYRE_SEASONS.items()]}


@router.get("/vehicles/{vin}/parts")
async def list_parts(vin: str, q: Optional[str] = Query(None, max_length=100), category: Optional[str] = None,
                     session: AsyncSession = Depends(get_db)):
    rows = (await session.execute(select(VehiclePart).where(VehiclePart.vin == vin).order_by(VehiclePart.category, VehiclePart.position, VehiclePart.id))).scalars().all()
    out = [serialize_part(p) for p in rows]
    if category:
        out = [p for p in out if p["category"] == category]
    if q and q.strip():
        out = [p for p in out if part_matches(p, q)]
    return out


@router.post("/vehicles/{vin}/parts")
async def create_part(vin: str, req: PartIn, session: AsyncSession = Depends(get_db)):
    if (await session.execute(select(Vehicle.id).where(Vehicle.vin == vin))).first() is None:
        raise HTTPException(status_code=404, detail="Vehículo no encontrado.")
    row = await upsert_part(session, vin, await _from_catalog(session, _clean(req)))
    await session.commit()
    return serialize_part(row)


@router.put("/parts/{part_id}")
async def update_part(part_id: int, req: PartIn, session: AsyncSession = Depends(get_db)):
    row = await session.get(VehiclePart, part_id)
    if row is None:
        raise HTTPException(status_code=404, detail="Pieza no encontrada.")
    part = await _from_catalog(session, _clean(req))
    row.category, row.position = part.category, part.position
    apply_fields(row, part)
    await session.commit()
    return serialize_part(row)


@router.delete("/parts/{part_id}")
async def delete_part(part_id: int, session: AsyncSession = Depends(get_db)):
    row = await session.get(VehiclePart, part_id)
    if row is None:
        raise HTTPException(status_code=404, detail="Pieza no encontrada.")
    await session.delete(row)
    await session.commit()
    return {"ok": True}

