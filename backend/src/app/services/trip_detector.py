"""Trip detector and manager."""
from __future__ import annotations

import datetime
import logging
from typing import Dict, Optional

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Drive, DrivePosition, TelemetrySnapshot, Vehicle
from app.services.geofence import (
    find_geofence,
    reverse_geocode,
)

_LOGGER = logging.getLogger(__name__)

# Active trips per VIN
_ACTIVE_DRIVES: Dict[str, int] = {}
# Last time motion was seen (to know when the car is parked for good)
_LAST_MOTION_TIME: Dict[str, datetime.datetime] = {}
# Last reading WITHOUT motion per VIN: if a trip is detected late (car already moving), its start is recovered from here
_LAST_IDLE: Dict[str, Dict] = {}
# When high voltage was first seen off after the last motion: that is the trip's end time
# (the trip is closed after 5 min, but it ends when the car was switched off)
_HV_OFF_AT: Dict[str, datetime.datetime] = {}
# Minutes stopped before a trip counts as finished
IDLE_MINUTES_TO_END_TRIP = 5
# A trip left open across a restart is only resumed if there was activity less than this ago
RESUME_MAX_AGE = datetime.timedelta(minutes=30)

# Recovering the start of a trip detected late
ASSUMED_TRIP_SPEED_KMH = 35.0          # to estimate how long it had been moving from the km covered since the idle reading
BACKFILL_MAX_AGE = datetime.timedelta(hours=6)       # an older idle reading is no longer a valid starting point
BACKFILL_MAX_MINUTES = 90.0
# The battery and fuel a trip starts with come from the last stopped reading if it is this recent. Switching the car on (and
# the first minute of warming up) often takes a point before the first moving reading arrives: 80 % parked and ready, 79 % at
# the first reading in motion. That point belongs to the trip, not to the time the car spent parked.
START_LEVELS_MAX_AGE = datetime.timedelta(minutes=15)

# A «trip» shorter than this and without kilometres is noise (a parking manoeuvre, a single moving reading)
MIN_TRIP_SECONDS = 60


def resolve_distance(start_odometer: Optional[float], end_odometer: Optional[float]) -> Optional[float]:
    """Distance of a drive: the odometer alone, in whole kilometres, never negative.

    The GPS track is deliberately left out. The car's odometer only shows whole kilometres, so adding the GPS decimals on top
    (as earlier versions did) counted them twice: the next drive's odometer step already included them, and sums of drives
    drifted above the car's own total. The track is kept for the map, the replay and the geofences, where a coverage gap is
    visible instead of silently wrong. Short hops may read 0 km, exactly as on the dashboard."""
    if start_odometer is None or end_odometer is None:
        return None
    return float(max(0, int(end_odometer) - int(start_odometer)))


class TripDetector:
    @staticmethod
    def has_open_trip(vin: str) -> bool:
        """True while a trip is open: the poller shortens its interval so it can close it after 5 min stopped."""
        return vin in _ACTIVE_DRIVES

    @classmethod
    async def _resume_open_drive(cls, session: AsyncSession, vin: str, now: datetime.datetime) -> Optional[int]:
        """After a restart: resumes this car's open trip if there was recent activity (otherwise it is left as it is)."""
        drive = (await session.execute(
            select(Drive).where(Drive.vin == vin, Drive.end_time.is_(None)).order_by(Drive.start_time.desc()).limit(1)
        )).scalar_one_or_none()
        if drive is None:
            return None
        last = (await session.execute(
            select(DrivePosition).where(DrivePosition.drive_id == drive.id).order_by(DrivePosition.timestamp.desc()).limit(1)
        )).scalar_one_or_none()
        last_at = last.timestamp if last else drive.start_time
        if now - last_at > RESUME_MAX_AGE:
            return None
        _ACTIVE_DRIVES[vin] = drive.id
        _LAST_MOTION_TIME[vin] = last_at
        _LOGGER.info(f"Open trip resumed after a restart for {vin} [Drive #{drive.id}]")
        return drive.id

    @classmethod
    async def _previous_idle(cls, session: AsyncSession, vin: str, now: datetime.datetime) -> Optional[Dict]:
        """Last idle reading before this one: from memory or, after a restart, from the database."""
        prev = _LAST_IDLE.get(vin)
        if prev is not None:
            return prev
        stmt = (
            select(TelemetrySnapshot)
            .where(TelemetrySnapshot.vin == vin, TelemetrySnapshot.timestamp < now - datetime.timedelta(seconds=1))
            .order_by(TelemetrySnapshot.timestamp.desc())
            .limit(5)
        )
        for row in (await session.execute(stmt)).scalars():
            if (row.speed_kmh or 0.0) <= 2.0:
                return {"time": row.timestamp, "lat": row.latitude, "lon": row.longitude, "odometer": row.odometer_km,
                        "battery": row.battery_level, "fuel": row.fuel_level}
        return None

    @staticmethod
    def _backfill(prev: Optional[Dict], now: datetime.datetime, odometer: Optional[float]) -> Optional[Dict]:
        """Estimated start of a trip detected with the car already moving, or None when there is no reliable base.

        It needs a recent idle reading with a position and the odometer to have moved since then (otherwise the trip
        started just now). The time is estimated from the km covered, never earlier than the idle reading."""
        if not prev or prev.get("lat") is None or prev.get("lon") is None:
            return None
        if odometer is None or prev.get("odometer") is None or odometer <= prev["odometer"]:
            return None
        if now - prev["time"] > BACKFILL_MAX_AGE:
            return None
        minutes = min(BACKFILL_MAX_MINUTES, (odometer - prev["odometer"]) / ASSUMED_TRIP_SPEED_KMH * 60.0)
        return {**prev, "start_time": max(prev["time"], now - datetime.timedelta(minutes=minutes))}

    @classmethod
    async def process_snapshot(
        cls,
        session: AsyncSession,
        vehicle: Vehicle,
        telemetry: Dict,
    ) -> Optional[Drive]:
        """Processes a telemetry snapshot and manages the trip's life cycle."""
        vin = vehicle.vin
        now = datetime.datetime.utcnow()
        speed = telemetry.get("speed_kmh", 0.0) or 0.0
        lat = telemetry.get("latitude")
        lon = telemetry.get("longitude")
        alt = telemetry.get("altitude")
        heading = telemetry.get("heading")
        battery = telemetry.get("battery_level")
        odometer = telemetry.get("odometer_km")

        active_drive_id = _ACTIVE_DRIVES.get(vin)
        if active_drive_id is None and vin not in _LAST_IDLE:
            active_drive_id = await cls._resume_open_drive(session, vin, now)
        is_moving = speed > 2.0 or telemetry.get("state") == "driving"

        if is_moving:
            _LAST_MOTION_TIME[vin] = now
        # High voltage: the first reading with it off sets the end time; switching it back on discards it
        hv = telemetry.get("high_voltage_on")
        if hv is False and not is_moving:
            _HV_OFF_AT.setdefault(vin, now)
        else:
            _HV_OFF_AT.pop(vin, None)

        # 1. Check whether a new trip must start
        if active_drive_id is None and is_moving and lat is not None and lon is not None:
            # If the trip is detected late (the reading arrives with the car already moving), it starts at the last idle reading
            idle = await cls._previous_idle(session, vin, now)
            back = cls._backfill(idle, now, odometer)
            s_lat, s_lon = (back["lat"], back["lon"]) if back else (lat, lon)
            # Levels at departure: the recovered start, or the stopped reading just before (car switched on, then moving)
            levels = back or (idle if idle and now - idle["time"] <= START_LEVELS_MAX_AGE else None)
            start_battery = levels["battery"] if levels and levels.get("battery") is not None else battery
            start_fuel = levels["fuel"] if levels and levels.get("fuel") is not None else telemetry.get("fuel_level")

            # Look up whether it is inside a geofence
            gf = await find_geofence(session, s_lat, s_lon, owner_id=vehicle.owner_id)
            addr = await reverse_geocode(s_lat, s_lon)
            if gf:
                addr = gf.name

            temp = telemetry.get("outside_temp")
            climate = telemetry.get("climate_on", False)

            new_drive = Drive(
                vin=vin,
                start_time=back["start_time"] if back else now,
                start_lat=s_lat,
                start_lon=s_lon,
                start_address=addr,
                start_geofence_id=gf.id if gf else None,
                start_odometer=back["odometer"] if back else odometer,
                start_battery_pct=start_battery,
                start_fuel=start_fuel,
                start_estimated=bool(back),
                outside_temp_avg=temp,
                climate_used=climate,
                speed_max_kmh=speed,
                speed_avg_kmh=speed,
            )
            session.add(new_drive)
            await session.flush()
            if back:
                # The track starts at the starting point, not halfway
                session.add(DrivePosition(drive_id=new_drive.id, timestamp=new_drive.start_time, latitude=s_lat, longitude=s_lon,
                                          speed_kmh=0.0, battery_pct=back["battery"]))

            active_drive_id = new_drive.id
            _ACTIVE_DRIVES[vin] = active_drive_id
            _LOGGER.info(f"New trip started for {vin} [Drive #{active_drive_id}]" + (" (estimated start)" if back else ""))

        # 2. With an active trip, record the point and check whether it ends
        if active_drive_id is not None:
            drive = await session.get(Drive, active_drive_id)
            if not drive:
                _ACTIVE_DRIVES.pop(vin, None)
                return None

            # Add a position when the coordinates are valid
            if lat is not None and lon is not None:
                pos = DrivePosition(
                    drive_id=active_drive_id,
                    timestamp=now,
                    latitude=lat,
                    longitude=lon,
                    altitude=alt,
                    heading=heading,
                    speed_kmh=speed,
                    battery_pct=battery,
                )
                session.add(pos)

                # Update the top speed
                if speed > (drive.speed_max_kmh or 0.0):
                    drive.speed_max_kmh = round(speed, 1)

            # 3. Check the end-of-trip condition (stopped for more than IDLE_MINUTES_TO_END_TRIP)
            last_motion = _LAST_MOTION_TIME.get(vin, drive.start_time)
            stopped_duration = (now - last_motion).total_seconds() / 60.0

            # Also end it if the car starts charging or gets plugged in
            force_end = telemetry.get("is_charging") or telemetry.get("cable_connected")

            if stopped_duration >= IDLE_MINUTES_TO_END_TRIP or force_end:
                # Close the trip
                hv_off = _HV_OFF_AT.get(vin)
                if stopped_duration >= IDLE_MINUTES_TO_END_TRIP:
                    # The trip ends when the car was switched off (if seen), never before the last motion
                    drive.end_time = hv_off if hv_off and last_motion <= hv_off <= now else last_motion
                else:
                    drive.end_time = now
                drive.end_lat = lat
                drive.end_lon = lon
                drive.end_odometer = odometer
                drive.end_battery_pct = battery

                # Distance (odometer only) and duration
                distance = resolve_distance(drive.start_odometer, drive.end_odometer)
                if distance is not None:
                    drive.distance_km = distance

                drive.duration_seconds = max(0, int((drive.end_time - drive.start_time).total_seconds()))

                if drive.start_battery_pct is not None and drive.end_battery_pct is not None:
                    drive.battery_used_pct = max(0.0, round(drive.start_battery_pct - drive.end_battery_pct, 1))
                    drive.energy_consumed_kwh = round((drive.battery_used_pct / 100.0) * (vehicle.battery_capacity_kwh or 19.27), 2)

                drive.end_fuel = telemetry.get("fuel_level")
                if drive.start_fuel is not None and drive.end_fuel is not None:
                    drive.fuel_used_l = max(0.0, round(drive.start_fuel - drive.end_fuel, 2))

                # Geocode the destination
                end_gf = await find_geofence(session, lat, lon, owner_id=vehicle.owner_id)
                end_addr = await reverse_geocode(lat, lon)
                if end_gf:
                    end_addr = end_gf.name
                drive.end_geofence_id = end_gf.id if end_gf else None
                drive.end_address = end_addr

                # Average speed
                if drive.duration_seconds > 0 and drive.distance_km > 0:
                    hours = drive.duration_seconds / 3600.0
                    drive.speed_avg_kmh = round(drive.distance_km / hours, 1)

                if drive.duration_seconds < MIN_TRIP_SECONDS and not (distance and distance > 0):
                    # Noise: a manoeuvre or a single moving reading, without kilometres. Not a trip.
                    _LOGGER.info(f"Discarded a {drive.duration_seconds} s trip without kilometres for {vin} [Drive #{drive.id}]")
                    await session.flush()      # (this reading's point was not saved yet: save it and delete it with the rest)
                    await session.execute(delete(DrivePosition).where(DrivePosition.drive_id == drive.id))
                    await session.delete(drive)
                    await session.flush()
                    _ACTIVE_DRIVES.pop(vin, None)
                    _LAST_MOTION_TIME.pop(vin, None)
                    _HV_OFF_AT.pop(vin, None)
                    _remember_idle(vin, now, lat, lon, odometer, battery, telemetry, is_moving)
                    return None

                await session.flush()
                _LOGGER.info(
                    f"Trip completed for {vin} [Drive #{drive.id}]: "
                    f"{drive.distance_km} km in {drive.duration_seconds // 60} min."
                )
                _ACTIVE_DRIVES.pop(vin, None)
                _LAST_MOTION_TIME.pop(vin, None)
                _HV_OFF_AT.pop(vin, None)
                _remember_idle(vin, now, lat, lon, odometer, battery, telemetry, is_moving)
                return drive

        _remember_idle(vin, now, lat, lon, odometer, battery, telemetry, is_moving)
        return None


def _remember_idle(vin, now, lat, lon, odometer, battery, telemetry, is_moving) -> None:
    """Stores the last idle reading (the starting point if the next trip is detected late)."""
    if not is_moving:
        _LAST_IDLE[vin] = {"time": now, "lat": lat, "lon": lon, "odometer": odometer, "battery": battery,
                           "fuel": telemetry.get("fuel_level")}
