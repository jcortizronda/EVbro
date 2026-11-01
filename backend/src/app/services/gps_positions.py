"""What a GPS fix from Traccar does: it is stored, it updates the link's "last seen", and it feeds the drives.

Two modes per car (`TraccarLink.build_trips`):
  * **Trazado** («track», connected cars): the fix only enriches the open drive's track; the car's own telemetry decides when a drive
    starts and ends (services/trip_detector.py).
  * **Viajes** («trips», cars without an Ebro account, or whose cloud gives no position): the GPS builds the drives. A drive opens with
    the first fix in motion (Traccar's `motion` attribute, or speed above `MOVING_KMH`); it closes when the car has been still
    for `stop_minutes` within `STOP_RADIUS_M` of the first still fix, and the end time is that first still fix (not the moment
    we noticed). A gap longer than `stop_minutes` between fixes closes the drive at the last fix and a new one opens if the car
    reappears moving somewhere else. Distance is the GPS distance of the track; addresses and geofences as in the car detector.
"""
from __future__ import annotations

import datetime
import logging
from typing import Dict, Optional, Tuple

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Drive, DrivePosition, GpsPosition, TraccarLink, Vehicle
from app.services.geofence import find_geofence, haversine_distance_meters, reverse_geocode
from app.services.traccar import Fix

_LOGGER = logging.getLogger(__name__)
MOVING_KMH = 3.0
STOP_RADIUS_M = 50.0
MIN_DRIVE_M = 150.0                     # shorter "drives" (a GPS wobble while parked) are discarded

_OPEN: Dict[str, int] = {}                                          # vin → open drive id built from GPS
_LAST: Dict[str, Fix] = {}                                          # vin → last fix seen
_STILL_SINCE: Dict[str, Tuple[datetime.datetime, float, float]] = {}   # vin → first still fix (time, lat, lon)


def reset_state() -> None:
    _OPEN.clear()
    _LAST.clear()
    _STILL_SINCE.clear()


def is_moving(fix: Fix) -> bool:
    if fix.motion is not None:
        return bool(fix.motion)
    return (fix.speed_kmh or 0.0) > MOVING_KMH


async def handle_fix(session: AsyncSession, vin: str, fix: Fix) -> None:
    link = (await session.execute(select(TraccarLink).where(TraccarLink.vin == vin))).scalar_one_or_none()
    vehicle = (await session.execute(select(Vehicle).where(Vehicle.vin == vin))).scalar_one_or_none()
    if link is None or vehicle is None:
        return
    last = _LAST.get(vin)
    if last is not None and fix.timestamp <= last.timestamp:         # Traccar re-sends the last fix on connect: not twice
        return
    if link.last_position_at is not None and fix.timestamp <= link.last_position_at and last is None:
        _LAST[vin] = fix                                              # after a restart: remember it, do not store it again
        return
    session.add(GpsPosition(vin=vin, timestamp=fix.timestamp, latitude=fix.latitude, longitude=fix.longitude, altitude=fix.altitude,
                            speed_kmh=fix.speed_kmh, course=fix.course, accuracy_m=fix.accuracy_m, ignition=fix.ignition,
                            motion=fix.motion, battery_pct=fix.battery_pct))
    link.last_position_at = fix.timestamp
    link.last_latitude, link.last_longitude = fix.latitude, fix.longitude
    link.last_error = None
    if link.build_trips:
        await _build_trips(session, vehicle, link, fix)
    else:
        await _enrich_open_drive(session, vin, fix)
    _LAST[vin] = fix
    await session.commit()


async def _enrich_open_drive(session: AsyncSession, vin: str, fix: Fix) -> None:
    open_id = (await session.execute(
        select(Drive.id).where(Drive.vin == vin, Drive.end_time.is_(None)).order_by(Drive.start_time.desc()).limit(1))).scalar_one_or_none()
    if open_id is not None:
        session.add(_position(open_id, fix))


def _position(drive_id: int, fix: Fix) -> DrivePosition:
    return DrivePosition(drive_id=drive_id, timestamp=fix.timestamp, latitude=fix.latitude, longitude=fix.longitude, altitude=fix.altitude,
                         heading=fix.course, speed_kmh=fix.speed_kmh or 0.0)


async def _open_drive_id(session: AsyncSession, vin: str) -> Optional[int]:
    if vin in _OPEN:
        return _OPEN[vin]
    row = (await session.execute(
        select(Drive.id).where(Drive.vin == vin, Drive.end_time.is_(None)).order_by(Drive.start_time.desc()).limit(1))).scalar_one_or_none()
    if row is not None:
        _OPEN[vin] = row
    return row


async def _build_trips(session: AsyncSession, vehicle: Vehicle, link: TraccarLink, fix: Fix) -> None:
    vin = vehicle.vin
    stop_after = datetime.timedelta(minutes=max(1, link.stop_minutes or 5))
    moving = is_moving(fix)
    open_id = await _open_drive_id(session, vin)
    last = _LAST.get(vin)

    # A long silence ends the drive where the car was last seen
    if open_id is not None and last is not None and fix.timestamp - last.timestamp > stop_after:
        await _close(session, vehicle, open_id, last.timestamp, last.latitude, last.longitude)
        open_id = None

    if open_id is None:
        if moving:
            open_id = await _open(session, vehicle, fix)
        return

    session.add(_position(open_id, fix))
    if moving:
        _STILL_SINCE.pop(vin, None)
        return
    since = _STILL_SINCE.get(vin)
    if since is None or haversine_distance_meters(since[1], since[2], fix.latitude, fix.longitude) > STOP_RADIUS_M:
        _STILL_SINCE[vin] = (fix.timestamp, fix.latitude, fix.longitude)
        return
    if fix.timestamp - since[0] >= stop_after:
        await _close(session, vehicle, open_id, since[0], since[1], since[2])


async def _open(session: AsyncSession, vehicle: Vehicle, fix: Fix) -> int:
    gf = await find_geofence(session, fix.latitude, fix.longitude, owner_id=vehicle.owner_id)
    drive = Drive(vin=vehicle.vin, start_time=fix.timestamp, start_lat=fix.latitude, start_lon=fix.longitude,
                  start_address=await reverse_geocode(fix.latitude, fix.longitude), start_geofence_id=gf.id if gf else None,
                  distance_km=0.0, duration_seconds=0)
    session.add(drive)
    await session.flush()
    session.add(_position(drive.id, fix))
    _OPEN[vehicle.vin] = drive.id
    _STILL_SINCE.pop(vehicle.vin, None)
    _LOGGER.info("Traccar: trip started for %s [Drive #%s]", vehicle.vin[-6:], drive.id)
    return drive.id


async def _close(session: AsyncSession, vehicle: Vehicle, drive_id: int, end_time: datetime.datetime, lat: float, lon: float) -> None:
    vin = vehicle.vin
    _OPEN.pop(vin, None)
    _STILL_SINCE.pop(vin, None)
    drive = await session.get(Drive, drive_id)
    if drive is None:
        return
    await session.flush()
    points = (await session.execute(
        select(DrivePosition).where(DrivePosition.drive_id == drive_id, DrivePosition.timestamp <= end_time).order_by(DrivePosition.timestamp))).scalars().all()
    meters = sum(haversine_distance_meters(a.latitude, a.longitude, b.latitude, b.longitude) for a, b in zip(points, points[1:], strict=False))
    if meters < MIN_DRIVE_M:
        for p in (await session.execute(select(DrivePosition).where(DrivePosition.drive_id == drive_id))).scalars():
            await session.delete(p)
        await session.delete(drive)
        _LOGGER.info("Traccar: trip of %s discarded (%.0f m)", vin[-6:], meters)
        return
    drive.end_time = end_time
    drive.end_lat, drive.end_lon = lat, lon
    drive.end_address = await reverse_geocode(lat, lon)
    gf = await find_geofence(session, lat, lon, owner_id=vehicle.owner_id)
    drive.end_geofence_id = gf.id if gf else None
    drive.distance_km = round(meters / 1000.0, 2)
    drive.duration_seconds = int((end_time - drive.start_time).total_seconds())
    speeds = [p.speed_kmh for p in points if p.speed_kmh]
    drive.speed_max_kmh = round(max(speeds), 1) if speeds else 0.0
    hours = drive.duration_seconds / 3600.0
    drive.speed_avg_kmh = round(drive.distance_km / hours, 1) if hours > 0 else 0.0
    _LOGGER.info("Traccar: trip closed for %s [Drive #%s] %.1f km", vin[-6:], drive_id, drive.distance_km)
