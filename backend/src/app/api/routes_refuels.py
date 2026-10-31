"""Repostajes de combustible: los detectados por la subida del depósito y los añadidos a mano."""
from __future__ import annotations

import datetime
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import desc, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Refuel, Vehicle
from app.db.session import get_db
from app.services import periods, refuel_stats

router = APIRouter(tags=["Refuels"])


class RefuelCreate(BaseModel):
    timestamp: Optional[datetime.datetime] = None
    liters: float = Field(gt=0, le=300)
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
        "liters_estimated": bool(r.detected and r.fuel_before_pct is not None),     # los litros de un repostaje detectado son estimados
        "full_tank": refuel_stats.is_full(r),
        "full_tank_stated": r.full_tank is not None,
    }


def _apply_cost(r: Refuel, price: Optional[float], total: Optional[float]) -> None:
    """Precio por litro o total (no ambos): el otro se deduce de los litros."""
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
    """Consumo real entre llenados, coste por 100 km, precio medio del litro, km entre repostajes y totales por mes.

    Devuelve también la lista de repostajes del periodo, cada uno con los km desde el anterior y, si cierra un tramo entre
    llenados, su consumo: la pestaña Repostajes se pinta con una sola petición."""
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
    stats["refuels"] = [{**serialize(r), **{k: per[r.id].get(k) for k in ("km_since_previous", "l_per_100km", "km_since_full")}} for r in selected]
    return stats


@router.post("/vehicles/{vin}/refuels")
async def create_refuel(vin: str, req: RefuelCreate, session: AsyncSession = Depends(get_db)):
    """Añade un repostaje a mano (por ejemplo, uno que el coche no detectó)."""
    if req.price_per_liter is not None and req.total_cost is not None:
        raise HTTPException(status_code=422, detail="Indica el precio por litro o el coste total, no los dos.")
    vehicle = (await session.execute(select(Vehicle).where(Vehicle.vin == vin))).scalar_one_or_none()
    if vehicle is None:
        raise HTTPException(status_code=404, detail="Vehículo no encontrado.")
    r = Refuel(vin=vin, timestamp=(req.timestamp or datetime.datetime.utcnow()).replace(tzinfo=None), liters=req.liters,
               odometer=req.odometer if req.odometer is not None else vehicle.odometer, station=req.station, notes=req.notes, detected=False,
               full_tank=req.full_tank)
    _apply_cost(r, req.price_per_liter, req.total_cost)
    session.add(r)
    await session.commit()
    return serialize(r)


@router.put("/refuels/{refuel_id}")
async def update_refuel(refuel_id: int, req: RefuelUpdate, session: AsyncSession = Depends(get_db)):
    if req.price_per_liter is not None and req.total_cost is not None:
        raise HTTPException(status_code=422, detail="Indica el precio por litro o el coste total, no los dos.")
    r = await session.get(Refuel, refuel_id)
    if r is None:
        raise HTTPException(status_code=404, detail="Repostaje no encontrado.")
    sent = req.model_fields_set
    if req.timestamp is not None:
        r.timestamp = req.timestamp.replace(tzinfo=None)
    if "station" in sent:
        r.station = (req.station or "").strip() or None
    if "notes" in sent:
        r.notes = req.notes
    if req.odometer is not None:
        r.odometer = req.odometer
    if req.full_tank is not None:
        r.full_tank = req.full_tank
    if req.liters is not None:
        r.liters = req.liters
    if req.total_cost is not None or req.price_per_liter is not None:
        _apply_cost(r, req.price_per_liter, req.total_cost)
    elif req.liters is not None and r.price_per_liter is not None:            # solo cambian los litros: el total se recalcula
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
