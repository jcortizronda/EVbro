"""Servicio de Geocercas y Geocodificación Inversa (estilo Traccar)."""
from __future__ import annotations

import logging
import math
from typing import Dict, Optional, Tuple

import aiohttp
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.db.models import Geofence

_LOGGER = logging.getLogger(__name__)

# Caché en memoria para evitar peticiones redundantes a Nominatim
_GEOCODE_CACHE: Dict[Tuple[float, float], str] = {}


def haversine_distance_meters(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Calcula la distancia en metros entre dos coordenadas geográficas."""
    R = 6371000.0  # Radio de la Tierra en metros
    phi1 = math.radians(lat1)
    phi2 = math.radians(lat2)
    delta_phi = math.radians(lat2 - lat1)
    delta_lambda = math.radians(lon2 - lon1)

    a = (math.sin(delta_phi / 2.0) ** 2 +
         math.cos(phi1) * math.cos(phi2) * math.sin(delta_lambda / 2.0) ** 2)
    c = 2.0 * math.atan2(math.sqrt(a), math.sqrt(1.0 - a))
    return R * c


async def find_geofence(
    session: AsyncSession,
    lat: Optional[float],
    lon: Optional[float],
    only_charging_spots: bool = False,
    owner_id: Optional[int] = None,
) -> Optional[Geofence]:
    """Busca si una coordenada cae dentro de alguna geocerca del propietario del coche (cada gestor tiene las suyas)."""
    if lat is None or lon is None:
        return None

    stmt = select(Geofence).where(Geofence.owner_id == owner_id)
    result = await session.execute(stmt)
    geofences = result.scalars().all()

    # Si hay geocercas coincidentes, ordenar por cercanía al centro
    matching = []
    for gf in geofences:
        if only_charging_spots and not getattr(gf, "is_charging_spot", True):
            continue
        dist = haversine_distance_meters(lat, lon, gf.latitude, gf.longitude)
        if dist <= gf.radius_meters:
            matching.append((dist, gf))

    if matching:
        matching.sort(key=lambda x: x[0])
        return matching[0][1]

    return None



async def reverse_geocode(lat: Optional[float], lon: Optional[float]) -> Optional[str]:
    """Convierte coordenadas en dirección legible usando OpenStreetMap Nominatim."""
    if lat is None or lon is None or not settings.NOMINATIM_ENABLED:
        return None

    # Redondear a 4 decimales (~11 metros de precisión) para la caché
    cache_key = (round(lat, 4), round(lon, 4))
    if cache_key in _GEOCODE_CACHE:
        return _GEOCODE_CACHE[cache_key]

    url = "https://nominatim.openstreetmap.org/reverse"
    params = {
        "lat": str(lat),
        "lon": str(lon),
        "format": "jsonv2",
        "zoom": "18",
        "addressdetails": "1",
    }
    headers = {"User-Agent": settings.NOMINATIM_USER_AGENT}

    try:
        timeout = aiohttp.ClientTimeout(total=6)
        async with aiohttp.ClientSession(timeout=timeout) as client:
            async with client.get(url, params=params, headers=headers) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    name = data.get("display_name")
                    if name:
                        # Extraer formato conciso (calle + municipio)
                        address = data.get("address", {})
                        road = address.get("road") or address.get("pedestrian") or ""
                        house_number = address.get("house_number", "")
                        city = address.get("city") or address.get("town") or address.get("village") or ""
                        if road and city:
                            short_addr = f"{road} {house_number}, {city}".replace(" ,", ",").strip()
                        else:
                            short_addr = name.split(",")[0] + ", " + (city or "")

                        _GEOCODE_CACHE[cache_key] = short_addr
                        return short_addr
    except Exception as e:
        _LOGGER.debug(f"Error en geocodificación inversa para ({lat}, {lon}): {e}")

    return None
