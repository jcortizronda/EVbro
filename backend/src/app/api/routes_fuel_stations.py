"""Fuel stations near the car (or near a point the user gives) with the price of one fuel: Repostajes → «Gasolineras cercanas».

The list is the Ministry's (services/fuel_stations.py). The route hangs from the car so `security/access.py` asks for «view» on it,
and without a point it uses the car's last known position.
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
from app.services import fuel_stations

router = APIRouter(tags=["Fuel stations"])


@router.get("/vehicles/{vin}/fuel-stations")
async def fuel_stations_near(vin: str,
                             lat: Optional[float] = Query(None, ge=-90, le=90), lon: Optional[float] = Query(None, ge=-180, le=180),
                             radius_km: float = Query(5.0, gt=0, le=25), fuel: str = Query(fuel_stations.DEFAULT_FUEL),
                             limit: int = Query(80, ge=1, le=200), session: AsyncSession = Depends(get_db)):
    """The stations within `radius_km` that sell `fuel`, nearest first, with its price; the cheapest and dearest of them."""
    if fuel not in fuel_stations.FUELS:
        raise HTTPException(status_code=422, detail=_("Combustible no válido."))
    if (lat is None) != (lon is None):
        raise HTTPException(status_code=422, detail=_("Indica la latitud y la longitud."))
    if not settings.FUEL_PRICES_ENABLED:
        raise HTTPException(status_code=409, detail=_("Los precios de las gasolineras están desactivados en este servidor."))
    origin = "point"
    if lat is None:
        vehicle = (await session.execute(select(Vehicle).where(Vehicle.vin == vin))).scalar_one_or_none()
        if vehicle is None:
            raise HTTPException(status_code=404, detail="Vehículo no encontrado.")
        if vehicle.latitude is None or vehicle.longitude is None:
            raise HTTPException(status_code=409, detail=_("Todavía no se conoce la posición del coche."))
        lat, lon, origin = vehicle.latitude, vehicle.longitude, "car"
    try:
        all_stations = await fuel_stations.stations()
    except fuel_stations.Unavailable:
        raise HTTPException(status_code=502, detail=_("No se pudo descargar la lista de gasolineras del Ministerio. Inténtalo dentro de un rato.")) from None
    found = fuel_stations.nearby(all_stations, lat, lon, radius_km, fuel, limit)
    prices = [s["price"] for s in found]
    return {"center": {"lat": lat, "lon": lon, "from": origin}, "fuel": fuel, "fuels": list(fuel_stations.FUELS), "radius_km": radius_km,
            "stations": found, "cheapest": min(prices) if prices else None, "dearest": max(prices) if prices else None,
            **fuel_stations.info()}
