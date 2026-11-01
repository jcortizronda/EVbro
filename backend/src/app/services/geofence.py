"""Geofences and reverse geocoding (Traccar style)."""
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

# In-memory cache to avoid repeated requests to Nominatim
_GEOCODE_CACHE: Dict[Tuple[float, float], str] = {}


def haversine_distance_meters(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Distance in metres between two geographic coordinates."""
    R = 6371000.0  # Earth's radius in metres
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
    """Finds whether a coordinate falls inside one of the car owner's geofences (each manager has their own)."""
    if lat is None or lon is None:
        return None

    stmt = select(Geofence).where(Geofence.owner_id == owner_id)
    result = await session.execute(stmt)
    geofences = result.scalars().all()

    # If several geofences match, sort them by distance to the centre
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
    """Turns coordinates into a readable address using OpenStreetMap Nominatim."""
    if lat is None or lon is None or not settings.NOMINATIM_ENABLED:
        return None

    # Round to 4 decimals (~11 metres of precision) for the cache
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
                        # Build a short form (street + town)
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
        _LOGGER.debug(f"Reverse geocoding error for ({lat}, {lon}): {e}")

    return None
