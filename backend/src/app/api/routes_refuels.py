"""Fuel refuels: those detected by the tank level going up, those added by hand and those imported from a spreadsheet
(services/refuel_import.py). Every route takes the car from its path, so `security/access.py` asks for the «edit» level to change
anything."""
from __future__ import annotations

import datetime
import json
from typing import Any, Optional

from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, Response, UploadFile
from pydantic import BaseModel, Field
from sqlalchemy import desc, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Refuel, Vehicle
from app.db.session import get_db
from app.services import periods, refuel_import, refuel_stations, refuel_stats
from app.services.charge_import import ImportError_
from app.timeutil import utcnow

router = APIRouter(tags=["Refuels"])

LITERS_CHANGED = 0.005                     # litres are kept with two decimals: less than this is the same figure


class RefuelCreate(BaseModel):
    timestamp: Optional[datetime.datetime] = None      # with its offset (the page sends UTC); a naive one is taken as UTC
    liters: Optional[float] = Field(default=None, gt=0, le=300)     # or the price per litre and the total: any two give the third
    price_per_liter: Optional[float] = Field(default=None, ge=0, le=10)
    total_cost: Optional[float] = Field(default=None, ge=0, le=5000)
    odometer: Optional[float] = Field(default=None, ge=0, le=5_000_000)
    station: Optional[str] = Field(default=None, max_length=150)
    notes: Optional[str] = Field(default=None, max_length=2000)
    full_tank: bool = True                     # most manual entries are a fill-up; it is what makes real consumption measurable


class RefuelUpdate(BaseModel):
    timestamp: Optional[datetime.datetime] = None
    liters: Optional[float] = Field(default=None, gt=0, le=300)
    price_per_liter: Optional[float] = Field(default=None, ge=0, le=10)
    total_cost: Optional[float] = Field(default=None, ge=0, le=5000)
    odometer: Optional[float] = Field(default=None, ge=0, le=5_000_000)
    station: Optional[str] = Field(default=None, max_length=150)
    notes: Optional[str] = Field(default=None, max_length=2000)
    full_tank: Optional[bool] = None


def serialize(r: Refuel) -> dict:
    return {
        "id": r.id, "timestamp": r.timestamp.isoformat(), "odometer": r.odometer,
        "fuel_before_pct": r.fuel_before_pct, "fuel_after_pct": r.fuel_after_pct,
        "liters": r.liters, "price_per_liter": r.price_per_liter, "total_cost": r.total_cost,
        "station": r.station, "address": r.address, "notes": r.notes, "detected": r.detected,
        "needs_price": r.total_cost is None,
        # the litres of a detected refuel are the gauge's estimate until the user types others
        "liters_estimated": bool(r.detected and r.fuel_before_pct is not None and not r.liters_entered),
        "full_tank": refuel_stats.is_full(r),
        "full_tank_stated": r.full_tank is not None,
    }


def _utc(moment: datetime.datetime) -> datetime.datetime:
    """The naive UTC the database keeps (the form sends the local day and hour with their offset)."""
    if moment.tzinfo is not None:
        moment = moment.astimezone(datetime.timezone.utc)
    return moment.replace(tzinfo=None)


def _apply_cost(r: Refuel, price: Optional[float], total: Optional[float]) -> None:
    """Price per litre or total: the other one is derived from the litres (with both, the total wins: it is what was paid)."""
    if total is not None:
        r.total_cost = round(total, 2)
        r.price_per_liter = round(total / r.liters, 3) if r.liters else None
    elif price is not None:
        r.price_per_liter = price
        r.total_cost = round(price * r.liters, 2) if r.liters else None


@router.get("/vehicles/{vin}/refuels")
async def list_refuels(vin: str, limit: int = Query(100, ge=1, le=500), session: AsyncSession = Depends(get_db)):
    rows = (await session.execute(select(Refuel).where(Refuel.vin == vin).order_by(desc(Refuel.timestamp)).limit(limit))).scalars().all()
    return [serialize(r) for r in rows]


@router.get("/vehicles/{vin}/refuels/summary")
async def refuel_summary(vin: str, period: str = Query("all"), frm: Optional[str] = Query(None, alias="from", max_length=10),
                         to: Optional[str] = Query(None, max_length=10), session: AsyncSession = Depends(get_db)):
    """Real consumption between fill-ups, cost per 100 km, average price per litre, km between refuels and monthly totals.

    Also returns the list of refuels of the period, each with the km since the previous one and, if it closes a stretch
    between fill-ups, its consumption: the Repostajes tab is drawn with a single request."""
    vehicle = (await session.execute(select(Vehicle).where(Vehicle.vin == vin))).scalar_one_or_none()
    if vehicle is None:
        raise HTTPException(status_code=404, detail="Vehículo no encontrado.")
    since, until = periods.resolve(period, frm, to)
    rows = (await session.execute(select(Refuel).where(Refuel.vin == vin))).scalars().all()
    rows = [r for r in rows if until is None or r.timestamp < until]          # the tank history up to the end of the range
    stats = refuel_stats.compute(rows, vehicle.fuel_tank_liters or 50.0, since)
    per = stats.pop("per_refuel")
    selected = sorted((r for r in rows if periods.inside(r.timestamp, (since, until))), key=lambda r: r.timestamp, reverse=True)
    stats["period"], stats["from"], stats["to"] = period, frm, to
    stats["refuels"] = [{**serialize(r), **{k: per[r.id].get(k) for k in ("km_since_previous", "l_per_100km", "km_since_full")},
                         "station_suggestion": refuel_stations.suggest_station(r, rows)} for r in selected]
    stats["stations"] = refuel_stations.used_stations(rows)        # for the «Gasolinera» field: no need to type the same name again
    return stats


@router.post("/vehicles/{vin}/refuels")
async def create_refuel(vin: str, req: RefuelCreate, session: AsyncSession = Depends(get_db)):
    """Adds a refuel by hand (for example, one the car did not detect). Any two of the litres, the price per litre and the total give
    the third; with the three, the litres and the total win (the price follows from them)."""
    vehicle = (await session.execute(select(Vehicle).where(Vehicle.vin == vin))).scalar_one_or_none()
    if vehicle is None:
        raise HTTPException(status_code=404, detail="Vehículo no encontrado.")
    trio = refuel_import.settle(refuel_import.RefuelData(day=None, hour=None, liters=req.liters, price_per_liter=req.price_per_liter,
                                                         total_cost=req.total_cost))
    if not trio.liters:
        raise HTTPException(status_code=422, detail="Indica los litros, o el total y el precio por litro.")
    if not 0 < trio.liters <= refuel_import.LITERS_MAX:
        raise HTTPException(status_code=422, detail="Los litros no son válidos.")
    r = Refuel(vin=vin, timestamp=_utc(req.timestamp or utcnow()), liters=trio.liters, price_per_liter=trio.price_per_liter,
               total_cost=None if trio.total_cost is None else round(trio.total_cost, 2),
               odometer=req.odometer if req.odometer is not None else vehicle.odometer, station=req.station, notes=req.notes, detected=False,
               full_tank=req.full_tank)
    session.add(r)
    await session.commit()
    return serialize(r)


@router.put("/refuels/{refuel_id}")
async def update_refuel(refuel_id: int, req: RefuelUpdate, session: AsyncSession = Depends(get_db)):
    r = await session.get(Refuel, refuel_id)
    if r is None:
        raise HTTPException(status_code=404, detail="Repostaje no encontrado.")
    sent = req.model_fields_set
    if req.timestamp is not None:
        r.timestamp = _utc(req.timestamp)
    if "station" in sent:
        r.station = (req.station or "").strip() or None
    if "notes" in sent:
        r.notes = req.notes
    if req.odometer is not None:
        r.odometer = req.odometer
    if req.full_tank is not None:
        r.full_tank = req.full_tank
    liters = req.liters
    if liters is None and req.price_per_liter and req.total_cost is not None:
        liters = round(req.total_cost / req.price_per_liter, 2)                 # the ticket's total and price give its litres
        if not 0 < liters <= refuel_import.LITERS_MAX:
            raise HTTPException(status_code=422, detail="Los litros no son válidos.")
    if liters is not None:
        # The form always sends the litres it shows, the estimate included: only a different figure is the ticket's (21-11-2026: the
        # «estimados» tag stayed on refuels whose litres had been corrected)
        if r.liters is None or abs(liters - r.liters) >= LITERS_CHANGED:
            r.liters_entered = True
        r.liters = liters
    if req.total_cost is not None or req.price_per_liter is not None:
        _apply_cost(r, req.price_per_liter, req.total_cost)
    elif liters is not None and r.price_per_liter is not None:                # only the litres change: the total is recomputed
        r.total_cost = round(r.price_per_liter * r.liters, 2)
    await session.commit()
    return serialize(r)


@router.delete("/refuels/{refuel_id}")
async def delete_refuel(refuel_id: int, session: AsyncSession = Depends(get_db)):
    r = await session.get(Refuel, refuel_id)
    if r is None:
        raise HTTPException(status_code=404, detail="Repostaje no encontrado.")
    await session.delete(r)
    await session.commit()
    return {"status": "ok", "deleted_id": refuel_id}


# ───────────────────────── from a spreadsheet ─────────────────────────
async def _vehicle(session: AsyncSession, vin: str) -> Vehicle:
    vehicle = (await session.execute(select(Vehicle).where(Vehicle.vin == vin))).scalar_one_or_none()
    if vehicle is None:
        raise HTTPException(status_code=404, detail="Vehículo no encontrado.")
    return vehicle


@router.get("/vehicles/{vin}/refuels/import/template")
async def refuel_import_template(vin: str, session: AsyncSession = Depends(get_db)):
    """An example Excel book to fill in."""
    await _vehicle(session, vin)
    return Response(content=refuel_import.template(), media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                    headers={"Content-Disposition": 'attachment; filename="evbro-repostajes.xlsx"', "Cache-Control": "no-store"})


async def _reviewed(session: AsyncSession, vehicle: Vehicle, file: UploadFile, mapping: Optional[str]):
    data = await file.read(refuel_import.MAX_BYTES + 1)
    try:
        titles, rows = refuel_import.read_table(file.filename or "", data, refuel_import.TOO_MANY)
    except ImportError_ as err:
        raise HTTPException(status_code=422, detail=str(err)) from err
    chosen: Any = None
    if mapping:
        try:
            chosen = json.loads(mapping)
        except ValueError as err:
            raise HTTPException(status_code=422, detail="La asignación de columnas no es válida.") from err
    columns = refuel_import.clean_mapping(chosen, len(titles)) if chosen is not None else refuel_import.guess_mapping(titles)
    return titles, columns, await refuel_import.review(session, vehicle, rows, columns)


@router.post("/vehicles/{vin}/refuels/import/preview")
async def refuel_import_preview(vin: str, file: UploadFile = File(...), mapping: Optional[str] = Form(default=None),
                                session: AsyncSession = Depends(get_db)):
    """Reads the file and says how it was understood: what each column is and what would happen to each row. Stores nothing."""
    vehicle = await _vehicle(session, vin)
    titles, columns, reviewed = await _reviewed(session, vehicle, file, mapping)
    return {"filename": (file.filename or "")[:120], **refuel_import.summary(titles, columns, reviewed)}


@router.post("/vehicles/{vin}/refuels/import")
async def refuel_import_store(vin: str, file: UploadFile = File(...), mapping: Optional[str] = Form(default=None),
                              session: AsyncSession = Depends(get_db)):
    """Stores the rows of the file that are ready, as refuels added by hand of one batch (so the import can be undone whole)."""
    vehicle = await _vehicle(session, vin)
    _titles, columns, reviewed = await _reviewed(session, vehicle, file, mapping)
    if refuel_import.missing_columns(columns):
        raise HTTPException(status_code=422, detail="Indica qué columna tiene la fecha y cuál los litros (o el total y el precio por litro).")
    try:
        result = await refuel_import.store(session, vehicle, reviewed)
    except ImportError_ as err:
        raise HTTPException(status_code=422, detail=str(err)) from err
    await session.commit()
    return result


@router.delete("/vehicles/{vin}/refuels/import")
async def refuel_import_undo(vin: str, batch: str = Query(max_length=32), session: AsyncSession = Depends(get_db)):
    """Deletes every refuel an import brought in."""
    await _vehicle(session, vin)
    removed = await refuel_import.undo(session, vin, batch)
    if not removed:
        raise HTTPException(status_code=404, detail="Esa importación no existe o ya se deshizo.")
    await session.commit()
    return {"status": "ok", "removed": removed}
