"""Routes for drive history, GPS tracks, editing and advanced analytics."""
from __future__ import annotations

import datetime
from typing import Any, Dict, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import Response
from pydantic import BaseModel
from sqlalchemy import and_, desc, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_timezone
from app.db.models import Drive, DrivePosition, Geofence, Vehicle
from app.db.session import get_db
from app.services import gpx, periods, places
from app.services.stats import MIN_DRIVE_KM

router = APIRouter(tags=["Drives"])


class DriveUpdate(BaseModel):
    start_address: Optional[str] = None
    end_address: Optional[str] = None
    distance_km: Optional[float] = None
    duration_seconds: Optional[int] = None
    speed_avg_kmh: Optional[float] = None
    speed_max_kmh: Optional[float] = None
    start_battery_pct: Optional[float] = None
    end_battery_pct: Optional[float] = None
    battery_used_pct: Optional[float] = None
    energy_consumed_kwh: Optional[float] = None
    start_fuel: Optional[float] = None
    end_fuel: Optional[float] = None
    fuel_used_l: Optional[float] = None
    outside_temp_avg: Optional[float] = None
    max_power_kw: Optional[float] = None
    climate_used: Optional[bool] = None
    notes: Optional[str] = None


def format_drive_dict(d: Drive) -> Dict[str, Any]:
    """Formats a drive with every computed consumption and efficiency metric."""
    dist = d.distance_km or 0.0
    energy_kwh = d.energy_consumed_kwh or 0.0
    fuel_l = d.fuel_used_l or 0.0

    # Wh/km or kWh/100km
    consumption_wh_km = round((energy_kwh * 1000.0) / dist, 1) if dist > 0.1 and energy_kwh > 0 else None
    consumption_l_100km = round((fuel_l * 100.0) / dist, 2) if dist > 0.1 and fuel_l > 0 else None


    return {
        "id": d.id,
        "vin": d.vin,
        "start_time": d.start_time.isoformat() if d.start_time else None,
        "end_time": d.end_time.isoformat() if d.end_time else None,
        "start_address": d.start_address or "Origen no identificado",
        "end_address": d.end_address or "Destino no identificado",
        "duration_seconds": d.duration_seconds or 0,
        "duration_min": round((d.duration_seconds or 0) / 60.0, 1),
        "distance_km": round(dist, 1),
        "start_battery_pct": d.start_battery_pct,
        "end_battery_pct": d.end_battery_pct,
        "battery_used_pct": d.battery_used_pct,
        "energy_consumed_kwh": d.energy_consumed_kwh,
        "energy_consumed_wh": round(energy_kwh * 1000.0, 1) if energy_kwh else None,
        "start_fuel": d.start_fuel,
        "end_fuel": d.end_fuel,
        "fuel_used_l": d.fuel_used_l,
        "fuel_refuelled_pct": d.fuel_refuelled_pct or 0.0,
        "outside_temp_avg": d.outside_temp_avg,
        "speed_avg_kmh": d.speed_avg_kmh or 0.0,
        "speed_max_kmh": d.speed_max_kmh or 0.0,
        "max_power_kw": d.max_power_kw or 0.0,
        "climate_used": d.climate_used or False,
        "notes": d.notes or "",
        "consumption_wh_km": consumption_wh_km,
        "consumption_l_100km": consumption_l_100km,
        "consumption_kwh_100km": round(consumption_wh_km / 10.0, 1) if consumption_wh_km else None,
        "is_incomplete": d.end_time is None,
        "start_estimated": bool(d.start_estimated),
    }


@router.get("/vehicles/{vin}/drives")
async def list_vehicle_drives(
    vin: str,
    geofence_id: Optional[int] = Query(None),
    location: Optional[str] = Query(None),
    min_distance: Optional[float] = Query(None),
    min_speed: Optional[float] = Query(None),
    period: Optional[str] = Query("all"),
    frm: Optional[str] = Query(None, alias="from", max_length=10),
    to: Optional[str] = Query(None, max_length=10),
    start_date: Optional[str] = Query(None),
    end_date: Optional[str] = Query(None),
    limit: int = Query(100, ge=1, le=500),
    offset: int = Query(0, ge=0),
    session: AsyncSession = Depends(get_db),
):
    """Lists drives with advanced filters and the totals of the period."""
    filters = [Drive.vin == vin]

    # filter by geofence
    if geofence_id:
        filters.append(or_(Drive.start_geofence_id == geofence_id, Drive.end_geofence_id == geofence_id))

    # search filter by location
    if location and location.strip():
        term = f"%{location.strip()}%"
        filters.append(or_(Drive.start_address.ilike(term), Drive.end_address.ilike(term)))

    # minimum distance filter
    if min_distance is not None and min_distance > 0:
        filters.append(Drive.distance_km >= min_distance)

    # minimum speed filter
    if min_speed is not None and min_speed > 0:
        filters.append(Drive.speed_max_kmh >= min_speed)

    # period filter: preset or range (services/periods.py); start_date/end_date are kept for compatibility
    if period in periods.PRESETS or frm or to:
        filters.append(periods.condition(Drive.start_time, periods.resolve(period if period in periods.PRESETS else None, frm, to)))
    elif start_date:
        try:
            s_dt = datetime.datetime.fromisoformat(start_date)
            filters.append(Drive.start_time >= s_dt)
        except Exception:
            pass
        if end_date:
            try:
                e_dt = datetime.datetime.fromisoformat(end_date)
                filters.append(Drive.start_time <= e_dt)
            except Exception:
                pass

    stmt = select(Drive).where(and_(*filters)).order_by(desc(Drive.start_time))
    result = await session.execute(stmt)
    all_drives = result.scalars().all()

    # split complete and incomplete drives
    complete_drives = []
    incomplete_drives = []

    total_energy_kwh = 0.0
    total_fuel_l = 0.0
    total_duration_sec = 0
    total_distance_km = 0.0
    eff_km = 0.0          # km and kWh of the drives that enter the average consumption: the same rule as services/stats.py,
    eff_kwh = 0.0         # so the «Resumen del periodo» and the Energía / Estadísticas figures agree for the same period

    for d in all_drives:
        fmt = format_drive_dict(d)
        if d.end_time is None:
            incomplete_drives.append(fmt)
        else:
            complete_drives.append(fmt)
            total_energy_kwh += (d.energy_consumed_kwh or 0.0)
            total_fuel_l += (d.fuel_used_l or 0.0)
            total_duration_sec += (d.duration_seconds or 0)
            total_distance_km += (d.distance_km or 0.0)
            if (d.distance_km or 0.0) >= MIN_DRIVE_KM and d.energy_consumed_kwh:
                eff_km += d.distance_km
                eff_kwh += d.energy_consumed_kwh

    # averages for the period summary banner. Electric consumption only over drives of ≥ MIN_DRIVE_KM with measured energy:
    # a drive without kWh would add km and no energy, and in a very short one a single battery point and the odometer's
    # whole-km rounding give meaningless figures.
    avg_wh_km = round((eff_kwh * 1000.0) / eff_km, 1) if eff_km >= MIN_DRIVE_KM else 0.0
    avg_fuel_l_100 = round((total_fuel_l * 100.0) / total_distance_km, 2) if total_distance_km > 0 else 0.0

    # pagination over the complete drives
    paginated_drives = complete_drives[offset : offset + limit]

    return {
        "summary": {
            "total_energy_kwh": round(total_energy_kwh, 2),
            "total_fuel_l": round(total_fuel_l, 2),
            "total_duration_seconds": total_duration_sec,
            "total_duration_hours": round(total_duration_sec / 3600.0, 1),
            "total_duration_days": round(total_duration_sec / 86400.0, 1),
            "total_distance_km": round(total_distance_km, 1),
            "avg_consumption_wh_km": avg_wh_km,
            "avg_fuel_consumption_l_100km": avg_fuel_l_100,
            "count": len(complete_drives),
        },
        "drives": paginated_drives,
        "has_more": offset + limit < len(complete_drives),
        "months": _month_totals([d for d in all_drives if d.end_time is not None]),
        "incomplete_drives": incomplete_drives,
    }


def _month_totals(drives: list[Drive]) -> list[Dict[str, Any]]:
    """Totals per local calendar month (newest first) for the month headers of the trip list; they cover every
    filtered trip, not only the page on screen."""
    tz = get_timezone()
    months: Dict[str, Dict[str, Any]] = {}
    for d in drives:
        key = d.start_time.replace(tzinfo=datetime.timezone.utc).astimezone(tz).strftime("%Y-%m")
        m = months.setdefault(key, {"month": key, "count": 0, "distance_km": 0.0, "energy_kwh": 0.0, "fuel_l": 0.0})
        m["count"] += 1
        m["distance_km"] += d.distance_km or 0.0
        m["energy_kwh"] += d.energy_consumed_kwh or 0.0
        m["fuel_l"] += d.fuel_used_l or 0.0
    for m in months.values():
        for k in ("distance_km", "energy_kwh", "fuel_l"):
            m[k] = round(m[k], 1)
    return sorted(months.values(), key=lambda m: m["month"], reverse=True)


@router.get("/drives/{drive_id}")
async def get_drive_details(drive_id: int, session: AsyncSession = Depends(get_db)):
    """Gets the full details of a drive."""
    drive = await session.get(Drive, drive_id)
    if not drive:
        raise HTTPException(status_code=404, detail="Viaje no encontrado.")
    return format_drive_dict(drive)


@router.put("/drives/{drive_id}")
async def update_drive(drive_id: int, req: DriveUpdate, session: AsyncSession = Depends(get_db)):
    """Edits and corrects any field of a drive (addresses, km, consumption, notes)."""
    drive = await session.get(Drive, drive_id)
    if not drive:
        raise HTTPException(status_code=404, detail="Viaje no encontrado.")

    data = req.model_dump(exclude_unset=True) if hasattr(req, "model_dump") else req.dict(exclude_unset=True)
    for field, val in data.items():
        setattr(drive, field, val)

    await session.commit()
    await session.refresh(drive)
    return format_drive_dict(drive)


@router.delete("/drives/{drive_id}")
async def delete_drive(drive_id: int, session: AsyncSession = Depends(get_db)):
    """Deletes a drive from the history."""
    drive = await session.get(Drive, drive_id)
    if not drive:
        raise HTTPException(status_code=404, detail="Viaje no encontrado.")
    await session.delete(drive)
    await session.commit()
    return {"status": "ok", "deleted_id": drive_id}


@router.get("/drives/{drive_id}/track")
async def get_drive_track(drive_id: int, session: AsyncSession = Depends(get_db)):
    """Gets the detailed GPS track (GeoJSON LineString and points) of a drive."""
    drive = await session.get(Drive, drive_id)
    if not drive:
        raise HTTPException(status_code=404, detail="Viaje no encontrado.")

    stmt = select(DrivePosition).where(DrivePosition.drive_id == drive_id).order_by(DrivePosition.timestamp)
    result = await session.execute(stmt)
    positions = result.scalars().all()

    coordinates = [[p.longitude, p.latitude] for p in positions]
    points = [
        {
            "lat": p.latitude,
            "lon": p.longitude,
            "alt": p.altitude,
            "speed": p.speed_kmh,
            "battery": p.battery_pct,
            "ts": p.timestamp.isoformat(),
        }
        for p in positions
    ]

    geojson = {
        "type": "Feature",
        "geometry": {
            "type": "LineString",
            "coordinates": coordinates,
        },
        "properties": {
            "drive_id": drive.id,
            "distance_km": drive.distance_km,
            "duration_s": drive.duration_seconds,
            "speed_max": drive.speed_max_kmh,
            "speed_avg": drive.speed_avg_kmh,
        },
    }

    return {
        "geojson": geojson,
        "points": points,
    }


@router.get("/drives/{drive_id}/gpx")
async def get_drive_gpx(drive_id: int, session: AsyncSession = Depends(get_db)):
    """The drive's track as a GPX 1.1 file (download)."""
    drive = await session.get(Drive, drive_id)
    if not drive:
        raise HTTPException(status_code=404, detail="Viaje no encontrado.")
    positions = (await session.execute(
        select(DrivePosition).where(DrivePosition.drive_id == drive_id).order_by(DrivePosition.timestamp))).scalars().all()
    if not positions:
        raise HTTPException(status_code=404, detail="Este viaje no tiene puntos de ruta guardados.")
    day = drive.start_time.strftime("%Y-%m-%d")
    name = f"Viaje {day}" + (f" · {drive.start_address} → {drive.end_address}" if drive.start_address and drive.end_address else "")
    body = gpx.build_gpx(name, positions, description=f"{drive.distance_km or 0:.1f} km")
    return Response(content=body, media_type="application/gpx+xml",
                    headers={"Content-Disposition": f'attachment; filename="evbro-viaje-{drive_id}-{day}.gpx"'})


@router.get("/vehicles/{vin}/places")
async def get_frequent_places(vin: str, limit: int = Query(8, ge=1, le=30), session: AsyncSession = Depends(get_db)):
    """Where the drives end most often: by geofence, and elsewhere grouped within about 110 m (services/places.py)."""
    drives = (await session.execute(select(Drive).where(Drive.vin == vin, Drive.end_time.is_not(None)))).scalars().all()
    vehicle = (await session.execute(select(Vehicle).where(Vehicle.vin == vin))).scalar_one_or_none()
    geofences = (await session.execute(select(Geofence).where(Geofence.owner_id == vehicle.owner_id))).scalars().all() if vehicle else []
    return {"places": places.frequent_places(drives, limit=limit, geofences=geofences)}
