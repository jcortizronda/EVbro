"""Public charging points near the car (or near a point the user gives): Carga → «Cargadores cercanos».

The list is the official one (services/charge_stations.py). The route hangs from the car so `security/access.py` asks for «view»
on it, and without a point it uses the car's last known position.
"""
from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.db.models import Vehicle
from app.db.session import get_db
from app.i18n import _
from app.services import charge_stations

router = APIRouter(tags=["Charging points"])


@router.get("/vehicles/{vin}/charge-stations")
async def charge_stations_near(vin: str,
                               lat: Optional[float] = Query(None, ge=-90, le=90), lon: Optional[float] = Query(None, ge=-180, le=180),
                               radius_km: float = Query(10.0, gt=0, le=50), kind: str = Query(charge_stations.DEFAULT_KIND),
                               min_kw: float = Query(0.0, ge=0, le=400), limit: int = Query(80, ge=1, le=200),
                               session: AsyncSession = Depends(get_db)):
    """The charging sites within `radius_km` with a connector the car can use (`kind`: `all`, `dc` fast or `ac` slow) of `min_kw`
    or more, nearest first."""
    if kind not in charge_stations.KINDS:
        raise HTTPException(status_code=422, detail=_("Tipo de carga no válido."))
    if (lat is None) != (lon is None):
        raise HTTPException(status_code=422, detail=_("Indica la latitud y la longitud."))
    if not settings.CHARGE_STATIONS_ENABLED:
        raise HTTPException(status_code=409, detail=_("La lista de cargadores está desactivada en este servidor."))
    origin = "point"
    if lat is None:
        vehicle = (await session.execute(select(Vehicle).where(Vehicle.vin == vin))).scalar_one_or_none()
        if vehicle is None:
            raise HTTPException(status_code=404, detail="Vehículo no encontrado.")
        if vehicle.latitude is None or vehicle.longitude is None:
            raise HTTPException(status_code=409, detail=_("Todavía no se conoce la posición del coche."))
        lat, lon, origin = vehicle.latitude, vehicle.longitude, "car"
    try:
        all_stations = await charge_stations.stations()
    except charge_stations.Unavailable:
        raise HTTPException(status_code=502, detail=_("No se pudo descargar la lista oficial de cargadores. Inténtalo dentro de un rato.")) from None
    found = charge_stations.nearby(all_stations, lat, lon, radius_km, kind, min_kw, limit)
    return {"center": {"lat": lat, "lon": lon, "from": origin}, "kind": kind, "min_kw": min_kw, "radius_km": radius_km,
            "stations": found, **charge_stations.info()}
