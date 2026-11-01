"""A geofence created (or moved) after the fact also names the past: the charges and drives that already happened inside it.

Detectors only look geofences up when a charge or a drive starts or ends, so without this a geofence created today would leave
last month's home charges as «Calle Tal, 3». Rules (owner's decision, 2026-11-01):
- Only rows with **no geofence yet** are taken: one that already belongs to another geofence keeps it.
- A charge needs the geofence to be a charging spot (as in the detector); a drive's start and end are checked separately.
- **Prices never change** (owner's decision, 2026-11-01): a past charge keeps the cost it had, fixed, automatic or hand-entered.
  A geofence's tariff may change over time, and that must never rewrite past spending; only new charges take it.
"""
from __future__ import annotations

import logging
import math
from typing import Dict

from sqlalchemy import and_, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import ChargingSession, Drive, Geofence, Vehicle
from app.services.geofence import haversine_distance_meters

_LOGGER = logging.getLogger(__name__)
METERS_PER_DEGREE = 111_320.0


def _box(gf: Geofence):
    """Latitude and longitude ranges that contain the circle (a cheap first filter in SQL)."""
    dlat = gf.radius_meters / METERS_PER_DEGREE
    dlon = gf.radius_meters / (METERS_PER_DEGREE * max(0.01, math.cos(math.radians(gf.latitude))))
    return gf.latitude - dlat, gf.latitude + dlat, gf.longitude - dlon, gf.longitude + dlon


def _inside(gf: Geofence, lat, lon) -> bool:
    return lat is not None and lon is not None and haversine_distance_meters(lat, lon, gf.latitude, gf.longitude) <= gf.radius_meters


async def apply_to_history(session: AsyncSession, gf: Geofence) -> Dict[str, int]:
    """Assigns this geofence to the owner's past charges and drives inside it (name only, never prices); returns how many of each."""
    counts = {"charges": 0, "drives": 0}
    vins = select(Vehicle.vin).where(Vehicle.owner_id == gf.owner_id)
    lat0, lat1, lon0, lon1 = _box(gf)

    if gf.is_charging_spot:
        charges = (await session.execute(select(ChargingSession).where(
            ChargingSession.vin.in_(vins), ChargingSession.geofence_id.is_(None),
            ChargingSession.latitude.between(lat0, lat1), ChargingSession.longitude.between(lon0, lon1),
        ))).scalars().all()
        for c in charges:
            if not _inside(gf, c.latitude, c.longitude):
                continue
            c.geofence_id = gf.id
            c.address = gf.name
            counts["charges"] += 1                      # its cost stays as it was (see above)

    drives = (await session.execute(select(Drive).where(Drive.vin.in_(vins), or_(
        and_(Drive.start_geofence_id.is_(None), Drive.start_lat.between(lat0, lat1), Drive.start_lon.between(lon0, lon1)),
        and_(Drive.end_geofence_id.is_(None), Drive.end_lat.between(lat0, lat1), Drive.end_lon.between(lon0, lon1)),
    )))).scalars().all()
    for d in drives:
        touched = False
        if d.start_geofence_id is None and _inside(gf, d.start_lat, d.start_lon):
            d.start_geofence_id, d.start_address, touched = gf.id, gf.name, True
        if d.end_geofence_id is None and _inside(gf, d.end_lat, d.end_lon):
            d.end_geofence_id, d.end_address, touched = gf.id, gf.name, True
        counts["drives"] += touched

    if any(counts.values()):
        await session.flush()
        _LOGGER.info(f"Geofence #{gf.id} applied to the past: {counts}")
    return counts
