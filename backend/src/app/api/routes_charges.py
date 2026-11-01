"""Routes for the history and telemetry of charging sessions."""
from __future__ import annotations

import datetime
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import desc, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_timezone
from app.db.models import ChargePoint, ChargingSession, Vehicle
from app.db.session import get_db
from app.services import periods

router = APIRouter(tags=["Charging"])


class ChargeOut(BaseModel):
    id: int
    vin: str
    start_time: datetime.datetime
    end_time: Optional[datetime.datetime] = None
    address: Optional[str] = None
    latitude: Optional[float] = None
    longitude: Optional[float] = None
    start_battery_pct: Optional[float] = None
    end_battery_pct: Optional[float] = None
    start_range_km: Optional[float] = None
    end_range_km: Optional[float] = None
    kwh_added: float
    kwh_used: Optional[float] = None
    duration_seconds: int
    max_charge_power_kw: float
    charge_cost: float
    cost_mode: Optional[str] = None
    effective_cost_per_kwh: Optional[float] = None
    charge_type: Optional[str] = None          # ac | dc (None = unknown, shown as AC)

    class Config:
        from_attributes = True


COST_SOURCES = {"manual": "Introducida por ti", "default": "Sin tarifa: 0 €/kWh por defecto"}


def _serialize_session_details(charge: ChargingSession, points: List[ChargePoint], capacity_kwh: Optional[float] = None) -> Dict[str, Any]:
    """Computes aggregated KPIs and builds the enriched response."""
    curve = [
        {
            "ts": p.timestamp.isoformat(),
            "battery_pct": p.battery_pct,
            "voltage": p.voltage,
            "current": p.current,
            "power_kw": p.power_kw,
            "range_km": p.range_km,
        }
        for p in points
    ]

    dur_sec = charge.duration_seconds or 0
    if dur_sec == 0 and charge.end_time and charge.start_time:
        dur_sec = max(0, int((charge.end_time - charge.start_time).total_seconds()))

    kwh_add = charge.kwh_added or 0.0
    # grid energy: ONLY what the user enters (kWh from their meter). It used to be made up as added/0.95 and the
    # "efficiency" always came out as 95 %: without a meter it cannot be measured and is shown as unknown.
    kwh_use = charge.kwh_used if charge.kwh_used and charge.kwh_used > 0 else None
    efficiency = round(kwh_add / kwh_use * 100.0, 1) if kwh_use and kwh_add > 0 else None

    # average and peak power (kW)
    valid_powers = [p.power_kw for p in points if p.power_kw and p.power_kw > 0.1]
    if valid_powers:
        avg_power = round(sum(valid_powers) / len(valid_powers), 1)
        max_power = round(max(valid_powers), 1)
    elif dur_sec > 60 and kwh_add > 0:
        avg_power = round(kwh_add / (dur_sec / 3600.0), 1)
        max_power = charge.max_charge_power_kw or avg_power
    else:
        avg_power = charge.max_charge_power_kw or 0.0
        max_power = charge.max_charge_power_kw or 0.0

    # battery and range
    start_pct = charge.start_battery_pct if charge.start_battery_pct is not None else (points[0].battery_pct if points else 0.0)
    end_pct = charge.end_battery_pct if charge.end_battery_pct is not None else (points[-1].battery_pct if points else start_pct)
    added_pct = max(0.0, round(end_pct - start_pct, 1))

    start_rng = charge.start_range_km
    end_rng = charge.end_range_km
    if start_rng is None and points and points[0].range_km is not None:
        start_rng = points[0].range_km
    if end_rng is None and points and points[-1].range_km is not None:
        end_rng = points[-1].range_km
    if start_rng is None:
        start_rng = round(start_pct * 0.8, 0)
    if end_rng is None:
        end_rng = round(end_pct * 0.8, 0)
    added_rng = max(0.0, round(end_rng - start_rng, 0))

    return {
        "charge": {
            "id": charge.id,
            "vin": charge.vin,
            "is_active": charge.end_time is None,
            "start_time": charge.start_time.isoformat(),
            "end_time": charge.end_time.isoformat() if charge.end_time else None,
            "address": charge.address or "Punto de carga",
            "charge_type": charge.charge_type or "ac",
            "latitude": charge.latitude,
            "longitude": charge.longitude,
            "start_battery_pct": start_pct,
            "end_battery_pct": end_pct,
            "added_battery_pct": added_pct,
            "start_range_km": start_rng,
            "end_range_km": end_rng,
            "added_range_km": added_rng,
            "kwh_added": kwh_add,
            "kwh_used": kwh_use,                      # None = no meter (unknown)
            "efficiency_pct": efficiency,             # None = cannot be computed
            "battery_capacity_kwh": capacity_kwh,     # used to estimate kwh_added from the battery %
            "duration_seconds": dur_sec,
            "max_charge_power_kw": max_power,
            "avg_charge_power_kw": avg_power,
            "charge_cost": round(charge.charge_cost or 0.0, 2),
            "cost_mode": charge.cost_mode or "Sin tarifa",
            "cost_source": COST_SOURCES.get(charge.cost_mode or "", "Geocerca" if charge.geofence_id else "Sin tarifa"),
            "has_cost": bool(charge.cost_mode),
            "effective_cost_per_kwh": charge.effective_cost_per_kwh,
        },
        "curve": curve,
    }


async def _capacity(session: AsyncSession, vin: str) -> Optional[float]:
    vehicle = (await session.execute(select(Vehicle).where(Vehicle.vin == vin))).scalar_one_or_none()
    return vehicle.battery_capacity_kwh if vehicle else None


class ChargeCostUpdate(BaseModel):
    """Cost and/or energy of a charge entered by hand (when there is no geofence with a tariff). One or more fields are sent."""
    price_per_kwh: Optional[float] = Field(default=None, ge=0, le=5)      # €/kWh
    total_cost: Optional[float] = Field(default=None, ge=0, le=10000)     # € on the invoice/receipt
    kwh_meter: Optional[float] = Field(default=None, gt=0, le=500)        # kWh shown by your meter/charger (grid)


@router.put("/charges/{charge_id}/cost")
async def update_charge_cost(charge_id: int, req: ChargeCostUpdate, session: AsyncSession = Depends(get_db)):
    """Sets the cost of a charge: €/kWh or total in €, and optionally the meter kWh (used to compute the real efficiency)."""
    if req.price_per_kwh is None and req.total_cost is None and req.kwh_meter is None:
        raise HTTPException(status_code=422, detail="Indica el precio por kWh, el coste total o los kWh del contador.")
    if req.price_per_kwh is not None and req.total_cost is not None:
        raise HTTPException(status_code=422, detail="Indica el precio por kWh o el coste total, no los dos.")
    charge = await session.get(ChargingSession, charge_id)
    if not charge:
        raise HTTPException(status_code=404, detail="Sesión de recarga no encontrada.")

    if req.kwh_meter is not None:
        charge.kwh_used = round(req.kwh_meter, 2)
    energy = charge.kwh_used or charge.kwh_added or 0.0            # what is paid for is the grid energy, if known
    if req.total_cost is not None:
        charge.charge_cost = round(req.total_cost, 2)
        charge.effective_cost_per_kwh = round(req.total_cost / energy, 4) if energy > 0 else None
        charge.cost_mode = "manual"
    elif req.price_per_kwh is not None:
        charge.effective_cost_per_kwh = req.price_per_kwh
        charge.charge_cost = round(req.price_per_kwh * energy, 2)
        charge.cost_mode = "manual"
    elif charge.cost_mode == "manual" and charge.effective_cost_per_kwh:        # only the kWh change: the cost is recomputed
        charge.charge_cost = round(charge.effective_cost_per_kwh * energy, 2)
    await session.commit()
    return _serialize_session_details(charge, [], await _capacity(session, charge.vin))["charge"]


class ChargeTypeUpdate(BaseModel):
    charge_type: str = Field(pattern="^(ac|dc)$")


@router.put("/charges/{charge_id}/type")
async def update_charge_type(charge_id: int, req: ChargeTypeUpdate, session: AsyncSession = Depends(get_db)):
    """AC or DC (fast) by hand, when the car did not make it clear (a charge is AC unless something says DC)."""
    charge = await session.get(ChargingSession, charge_id)
    if not charge:
        raise HTTPException(status_code=404, detail="Sesión de recarga no encontrada.")
    charge.charge_type = req.charge_type
    await session.commit()
    return _serialize_session_details(charge, [], await _capacity(session, charge.vin))["charge"]


@router.get("/vehicles/{vin}/charges", response_model=List[ChargeOut])
async def list_vehicle_charges(
    vin: str,
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    period: Optional[str] = Query(None, max_length=12),
    frm: Optional[str] = Query(None, alias="from", max_length=10),
    to: Optional[str] = Query(None, max_length=10),
    session: AsyncSession = Depends(get_db),
):
    """Lists a vehicle's charging sessions (`period` or `from`/`to` filter by start date; services/periods.py)."""
    stmt = (
        select(ChargingSession)
        .where(ChargingSession.vin == vin, periods.condition(ChargingSession.start_time, periods.resolve(period, frm, to)))
        .order_by(desc(ChargingSession.start_time))
        .offset(offset)
        .limit(limit)
    )
    result = await session.execute(stmt)
    return result.scalars().all()


@router.get("/vehicles/{vin}/charges/summary")
async def charges_summary(vin: str, session: AsyncSession = Depends(get_db)):
    """Totals of every charge of the car and per local calendar month (newest first): the history list is paged,
    so its header and month separators cannot add up only what is on screen."""
    rows = (await session.execute(
        select(ChargingSession.start_time, ChargingSession.kwh_added, ChargingSession.charge_cost, ChargingSession.charge_type)
        .where(ChargingSession.vin == vin)
    )).all()
    tz = get_timezone()
    months: Dict[str, Dict[str, Any]] = {}
    for start, kwh, cost, _kind in rows:
        key = start.replace(tzinfo=datetime.timezone.utc).astimezone(tz).strftime("%Y-%m")
        m = months.setdefault(key, {"month": key, "count": 0, "kwh_added": 0.0, "cost": 0.0})
        m["count"] += 1
        m["kwh_added"] += kwh or 0.0
        m["cost"] += cost or 0.0
    for m in months.values():
        m["kwh_added"] = round(m["kwh_added"], 1)
        m["cost"] = round(m["cost"], 2)
    return {
        "count": len(rows),
        "kwh_added": round(sum(r[1] or 0.0 for r in rows), 1),
        "cost": round(sum(r[2] or 0.0 for r in rows), 2),
        "dc_count": sum(1 for r in rows if r[3] == "dc"),
        "dc_kwh_added": round(sum(r[1] or 0.0 for r in rows if r[3] == "dc"), 1),
        "months": sorted(months.values(), key=lambda m: m["month"], reverse=True),
    }


@router.get("/vehicles/{vin}/charges/latest")
async def get_latest_charge(vin: str, session: AsyncSession = Depends(get_db)):
    """Gets the most recent charging session (active or last completed) together with previous sessions."""
    stmt_recent = (
        select(ChargingSession)
        .where(ChargingSession.vin == vin)
        .order_by(desc(ChargingSession.start_time))
        .limit(15)
    )
    recent_res = await session.execute(stmt_recent)
    all_sessions = recent_res.scalars().all()

    if not all_sessions:
        return {
            "charge": None,
            "curve": [],
            "recent_sessions": [],
        }

    latest = all_sessions[0]
    stmt_pts = select(ChargePoint).where(ChargePoint.charging_session_id == latest.id).order_by(ChargePoint.timestamp)
    pts_res = await session.execute(stmt_pts)
    points = pts_res.scalars().all()

    data = _serialize_session_details(latest, points, await _capacity(session, vin))
    data["recent_sessions"] = [
        {
            "id": s.id,
            "start_time": s.start_time.isoformat(),
            "address": s.address or "Punto de carga",
            "kwh_added": s.kwh_added,
            "is_active": s.end_time is None,
        }
        for s in all_sessions
    ]
    return data


@router.get("/charges/{charge_id}")
async def get_charge_details(charge_id: int, session: AsyncSession = Depends(get_db)):
    """Gets the details of a charging session with its time curve and statistics."""
    charge = await session.get(ChargingSession, charge_id)
    if not charge:
        raise HTTPException(status_code=404, detail="Sesión de recarga no encontrada.")

    stmt = select(ChargePoint).where(ChargePoint.charging_session_id == charge_id).order_by(ChargePoint.timestamp)
    result = await session.execute(stmt)
    points = result.scalars().all()

    stmt_recent = (
        select(ChargingSession)
        .where(ChargingSession.vin == charge.vin)
        .order_by(desc(ChargingSession.start_time))
        .limit(15)
    )
    recent_res = await session.execute(stmt_recent)
    all_sessions = recent_res.scalars().all()

    data = _serialize_session_details(charge, points, await _capacity(session, charge.vin))
    data["recent_sessions"] = [
        {
            "id": s.id,
            "start_time": s.start_time.isoformat(),
            "address": s.address or "Punto de carga",
            "kwh_added": s.kwh_added,
            "is_active": s.end_time is None,
        }
        for s in all_sessions
    ]
    return data
