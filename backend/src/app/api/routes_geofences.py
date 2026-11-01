"""Geofence management routes (home, work, charger, electricity tariffs)."""
from __future__ import annotations

import datetime
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import AppUser, Geofence, GeofenceEvent
from app.db.session import get_db
from app.security import auth
from app.security.access import space_owner_id
from app.services.electricity_tariffs import get_pvpc_hourly_price, get_time_of_use_period

router = APIRouter(prefix="/geofences", tags=["Geofences"])


class GeofenceCreate(BaseModel):
    name: str
    latitude: float
    longitude: float
    radius_meters: float = 100.0
    notify_enter: bool = True
    notify_exit: bool = True
    is_charging_spot: bool = True
    cost_mode: str = "fixed"  # 'fixed', 'free', 'time_of_use', 'pvpc'
    cost_per_kwh: float = 0.15
    cost_valle: float = 0.08
    cost_llano: float = 0.14
    cost_punta: float = 0.22
    pvpc_zone: str = "Peninsula"
    cost_margin: float = 0.0
    cost_fallback: float = 0.15


class GeofenceUpdate(BaseModel):
    name: Optional[str] = None
    latitude: Optional[float] = None
    longitude: Optional[float] = None
    radius_meters: Optional[float] = None
    notify_enter: Optional[bool] = None
    notify_exit: Optional[bool] = None
    is_charging_spot: Optional[bool] = None
    cost_mode: Optional[str] = None
    cost_per_kwh: Optional[float] = None
    cost_valle: Optional[float] = None
    cost_llano: Optional[float] = None
    cost_punta: Optional[float] = None
    pvpc_zone: Optional[str] = None
    cost_margin: Optional[float] = None
    cost_fallback: Optional[float] = None


class GeofenceOut(BaseModel):
    id: int
    name: str
    latitude: float
    longitude: float
    radius_meters: float
    notify_enter: bool = True
    notify_exit: bool = True
    is_charging_spot: bool = True
    cost_mode: str = "fixed"
    cost_per_kwh: float = 0.15
    cost_valle: float = 0.08
    cost_llano: float = 0.14
    cost_punta: float = 0.22
    pvpc_zone: str = "Peninsula"
    cost_margin: float = 0.0
    cost_fallback: float = 0.15
    created_at: datetime.datetime

    class Config:
        from_attributes = True


@router.get("", response_model=List[GeofenceOut])
async def list_geofences(session: AsyncSession = Depends(get_db), user: AppUser = Depends(auth.require_auth)):
    """Lists the geofences of this account's space (those of its manager)."""
    stmt = select(Geofence).where(Geofence.owner_id == space_owner_id(user)).order_by(Geofence.name)
    result = await session.execute(stmt)
    return result.scalars().all()


@router.get("/pvpc/current")
async def current_pvpc_info(zone: str = "Peninsula"):
    """Returns the current regulated PVPC electricity price and the 2.0TD period in force."""
    now = datetime.datetime.utcnow()
    period, period_desc = get_time_of_use_period(now)
    price, label = await get_pvpc_hourly_price(now, zone=zone)
    return {
        "timestamp": now.isoformat(),
        "period": period,
        "period_description": period_desc,
        "pvpc_price_kwh": price,
        "pvpc_label": label,
        "zone": zone,
    }


@router.get("/reverse")
async def reverse_geocode_endpoint(lat: float, lon: float):
    """Returns the readable address of some coordinates for the geofence map."""
    from app.services.geofence import reverse_geocode
    addr = await reverse_geocode(lat, lon)
    return {"address": addr or f"{lat:.4f}, {lon:.4f}"}


@router.get("/search-address")
async def search_address_endpoint(q: str = Query(max_length=200)):
    """Searches addresses or places in OpenStreetMap Nominatim to place geofences on the map."""
    if not q or len(q.strip()) < 2:
        return []

    import aiohttp

    from app.config import settings

    url = "https://nominatim.openstreetmap.org/search"
    params = {
        "q": q.strip(),
        "format": "jsonv2",
        "limit": "5",
        "addressdetails": "1",
    }
    headers = {"User-Agent": settings.NOMINATIM_USER_AGENT}
    try:
        timeout = aiohttp.ClientTimeout(total=6)
        async with aiohttp.ClientSession(timeout=timeout) as client:
            async with client.get(url, params=params, headers=headers) as resp:
                if resp.status == 200:
                    items = await resp.json()
                    return [
                        {
                            "name": it.get("display_name"),
                            "lat": float(it.get("lat")),
                            "lon": float(it.get("lon")),
                        }
                        for it in items
                        if it.get("lat") and it.get("lon")
                    ]
    except Exception:
        pass
    return []


@router.post("", response_model=GeofenceOut)
async def create_geofence(req: GeofenceCreate, session: AsyncSession = Depends(get_db), user: AppUser = Depends(auth.require_auth)):
    """Creates a new geofence with support for notices and advanced tariffs (fixed, 2.0TD, PVPC)."""
    gf = Geofence(
        owner_id=space_owner_id(user),
        name=req.name,
        latitude=req.latitude,
        longitude=req.longitude,
        radius_meters=req.radius_meters,
        notify_enter=req.notify_enter,
        notify_exit=req.notify_exit,
        is_charging_spot=req.is_charging_spot,
        cost_mode=req.cost_mode,
        cost_per_kwh=req.cost_per_kwh,
        cost_valle=req.cost_valle,
        cost_llano=req.cost_llano,
        cost_punta=req.cost_punta,
        pvpc_zone=req.pvpc_zone,
        cost_margin=req.cost_margin,
        cost_fallback=req.cost_fallback,
    )
    session.add(gf)
    await session.commit()
    await session.refresh(gf)
    return gf


@router.put("/{geofence_id}", response_model=GeofenceOut)
async def update_geofence(
    geofence_id: int, req: GeofenceUpdate, session: AsyncSession = Depends(get_db)
):
    """Updates the settings, notifications or tariffs of an existing geofence."""
    gf = await session.get(Geofence, geofence_id)
    if not gf:
        raise HTTPException(status_code=404, detail="Geocerca no encontrada.")

    update_data = req.model_dump(exclude_unset=True)
    for field, value in update_data.items():
        setattr(gf, field, value)

    await session.commit()
    await session.refresh(gf)
    return gf


@router.delete("/{geofence_id}")
async def delete_geofence(geofence_id: int, session: AsyncSession = Depends(get_db)):
    """Deletes an existing geofence."""
    gf = await session.get(Geofence, geofence_id)
    if not gf:
        raise HTTPException(status_code=404, detail="Geocerca no encontrada.")
    await session.delete(gf)
    await session.commit()
    return {"status": "ok", "deleted_id": geofence_id}



@router.get("/events/{vin}")
async def list_geofence_events(vin: str, limit: int = Query(50, ge=1, le=500), session: AsyncSession = Depends(get_db)):
    """Latest entries into and exits from geofences, with their time (newest first)."""
    rows = (await session.execute(select(GeofenceEvent).where(GeofenceEvent.vin == vin)
                                  .order_by(GeofenceEvent.timestamp.desc(), GeofenceEvent.id.desc()).limit(limit))).scalars().all()
    return [{"id": e.id, "geofence_id": e.geofence_id, "geofence_name": e.geofence_name, "kind": e.kind,
             "timestamp": e.timestamp.isoformat(), "latitude": e.latitude, "longitude": e.longitude,
             "battery_pct": e.battery_pct, "odometer_km": e.odometer_km} for e in rows]
