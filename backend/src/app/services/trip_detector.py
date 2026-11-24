"""Trip detector and manager."""
from __future__ import annotations

import datetime
import logging
from typing import Dict, Optional

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Drive, DrivePosition, Refuel, TelemetrySnapshot, Vehicle
from app.services.geofence import (
    find_geofence,
    haversine_distance_meters,
    reverse_geocode,
)
from app.timeutil import utcnow

_LOGGER = logging.getLogger(__name__)

# Active trips per VIN
_ACTIVE_DRIVES: Dict[str, int] = {}
# The last closed trip of each car (id, end time, odometer and end position), to move its end if the cloud refreshes the position
_LAST_CLOSED: Dict[str, Dict] = {}
# Last time motion was seen (to know when the car is parked for good)
_LAST_MOTION_TIME: Dict[str, datetime.datetime] = {}
# Last reading WITHOUT motion per VIN: if a trip is detected late (car already moving), its start is recovered from here
_LAST_IDLE: Dict[str, Dict] = {}
# When high voltage was first seen off after the last motion: that is the trip's end time
# (the trip is closed after 5 min, but it ends when the car was switched off)
_HV_OFF_AT: Dict[str, datetime.datetime] = {}
# Cars whose cloud reported the charging cable while they were moving (18-11-2026: one car drove 12 km with the cable
# «connected» and its trip came out in 15 pieces). That report is stale: it does not end a trip until the cable is seen out.
_STALE_CABLE: set = set()
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
# A stopped reading with the odometer of the last trip's end but the car this far away means the cloud had given a stale position
# when the trip closed (04-11-2026: the morning's position for an hour): the end moves there. Only this long after the end.
RELOCATE_METERS = 250.0
RELOCATE_MAX_AGE = datetime.timedelta(hours=6)
# A trip EVBro did not see: the car is read off again with more km than at the last stopped reading and no trip in between (it
# slept between two hourly readings and its live channel was stopped; 16-11-2026: 4 km and 3 battery points more, same spot, with
# data the car had produced 72 minutes after parking). It is recorded as its own trip, marked `unseen`, never added to the
# previous one. Less than this is the odometer ticking (it counts whole km), and faster than this between the two readings is
# the reading that closed the last trip running behind, not a trip.
UNSEEN_MIN_KM = 2
UNSEEN_MAX_SPEED_KMH = 110.0
CAR_ON_STATES = ("driving", "ready")


def fuel_litres(start_pct: Optional[float], end_pct: Optional[float], tank_liters: Optional[float]) -> float:
    """Litres spent on a trip out of the tank % at the start and the end and the tank size (50 L when the sheet has none)."""
    if start_pct is None or end_pct is None:
        return 0.0
    return round(max(0.0, start_pct - end_pct) / 100.0 * (tank_liters or 50.0), 2)


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
                        "battery": row.battery_level, "fuel": row.fuel_level, "ev_odometer": _raw_ev_odometer(row.raw_data)}
        return None

    @staticmethod
    def _backfill(prev: Optional[Dict], now: datetime.datetime, odometer: Optional[float]) -> Optional[Dict]:
        """Estimated start of a trip detected with the car already moving, or None when there is no reliable base.

        It needs a recent idle reading and the odometer to have moved since then (otherwise the trip started just now). The time is
        estimated from the km covered, never earlier than the idle reading. The reading's position, when it has one, is the start."""
        if not prev:
            return None
        if odometer is None or prev.get("odometer") is None or odometer <= prev["odometer"]:
            return None
        if now - prev["time"] > BACKFILL_MAX_AGE:
            return None
        minutes = min(BACKFILL_MAX_MINUTES, (odometer - prev["odometer"]) / ASSUMED_TRIP_SPEED_KMH * 60.0)
        return {**prev, "start_time": max(prev["time"], now - datetime.timedelta(minutes=minutes))}

    @classmethod
    async def _refuelled_during(cls, session: AsyncSession, drive: Drive, now: datetime.datetime) -> float:
        """Tank points put in by the refuels of this trip: by odometer when both sides have one (the refuel form keeps the day
        but not the hour, so the time alone can place a refuel in the wrong trip), by time otherwise."""
        rows = (await session.execute(
            select(Refuel).where(Refuel.vin == drive.vin, Refuel.fuel_before_pct.is_not(None), Refuel.fuel_after_pct.is_not(None),
                                 Refuel.timestamp >= drive.start_time - datetime.timedelta(days=1), Refuel.timestamp <= now)
        )).scalars().all()
        points = 0.0
        for r in rows:
            in_time = drive.start_time <= r.timestamp <= now
            if r.odometer is None or drive.start_odometer is None or drive.end_odometer is None:
                inside = in_time
            elif drive.start_odometer < r.odometer < drive.end_odometer:
                inside = True
            else:                                       # at the very start or end of the trip: the time decides
                inside = in_time and r.odometer in (drive.start_odometer, drive.end_odometer)
            if inside:
                points += max(0.0, r.fuel_after_pct - r.fuel_before_pct)
        return round(points, 1)

    @classmethod
    async def process_snapshot(
        cls,
        session: AsyncSession,
        vehicle: Vehicle,
        telemetry: Dict,
    ) -> Optional[Drive]:
        """Processes a telemetry snapshot and manages the trip's life cycle."""
        vin = vehicle.vin
        now = utcnow()
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
        cable = bool(telemetry.get("cable_connected"))
        if not cable:
            _STALE_CABLE.discard(vin)
        elif is_moving:
            _STALE_CABLE.add(vin)                                 # a moving car cannot be plugged in (see `_STALE_CABLE`)
        # High voltage: the first reading with it off sets the end time; switching it back on discards it
        hv = telemetry.get("high_voltage_on")
        if hv is False and not is_moving:
            _HV_OFF_AT.setdefault(vin, now)
        else:
            _HV_OFF_AT.pop(vin, None)

        # 1. Check whether a new trip must start. A position is not required: some cars never get one from the cloud (two S800,
        # 08-11-2026) and their trips are still measured by the odometer, the battery and the tank; they just have no route or address
        if active_drive_id is None and is_moving:
            # If the trip is detected late (the reading arrives with the car already moving), it starts at the last idle reading
            idle = await cls._previous_idle(session, vin, now)
            back = cls._backfill(idle, now, odometer)
            s_lat, s_lon = (back["lat"], back["lon"]) if back else (lat, lon)
            if s_lat is None or s_lon is None:
                s_lat, s_lon = lat, lon
            # Levels at departure: the recovered start, or the stopped reading just before (car switched on, then moving)
            levels = back or (idle if idle and now - idle["time"] <= START_LEVELS_MAX_AGE else None)
            start_battery = levels["battery"] if levels and levels.get("battery") is not None else battery
            start_fuel = levels["fuel"] if levels and levels.get("fuel") is not None else telemetry.get("fuel_level")
            # Electric-only odometer: from the same reading as the total odometer, or the two would not match
            start_ev = back.get("ev_odometer") if back else telemetry.get("ev_odometer_km")

            # Look up whether it is inside a geofence (with a position only)
            gf, addr = None, None
            if s_lat is not None and s_lon is not None:
                gf = await find_geofence(session, s_lat, s_lon, owner_id=vehicle.owner_id)
                addr = gf.name if gf else await reverse_geocode(s_lat, s_lon)

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
                start_ev_odometer=start_ev,
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
            if back and s_lat is not None and s_lon is not None:
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

            # Also end it when the car gets plugged in (a charge from the grid). `is_charging` alone is not enough: the combustion
            # engine charges the battery on the road and the car reports that as charging (04-11-2026: one trip cut into five)
            # A cable already reported while the car was moving is stale: the trip then ends by the minutes stopped
            force_end = cable and vin not in _STALE_CABLE

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
                drive.end_ev_odometer = telemetry.get("ev_odometer_km")
                drive.end_battery_pct = battery

                # Distance (odometer only) and duration
                distance = resolve_distance(drive.start_odometer, drive.end_odometer)
                if distance is not None:
                    drive.distance_km = distance

                drive.duration_seconds = max(0, int((drive.end_time - drive.start_time).total_seconds()))

                if drive.start_battery_pct is not None and drive.end_battery_pct is not None:
                    drive.battery_used_pct = max(0.0, round(drive.start_battery_pct - drive.end_battery_pct, 1))
                    drive.energy_consumed_kwh = round((drive.battery_used_pct / 100.0) * (vehicle.battery_capacity_kwh or 19.27), 2)

                # The car reports the tank as a % (`oilSurplus`), like the battery: the litres are the points spent times the
                # tank size of the vehicle sheet (until 0024 the points themselves were stored as litres: 57 % came out as 57 L)
                drive.end_fuel = telemetry.get("fuel_level")
                if drive.start_fuel is not None and drive.end_fuel is not None:
                    # A pump stop on the way puts points in: the litres spent are those consumed before and after it
                    drive.fuel_refuelled_pct = await cls._refuelled_during(session, drive, now)
                    drive.fuel_used_l = fuel_litres(drive.start_fuel + drive.fuel_refuelled_pct, drive.end_fuel, vehicle.fuel_tank_liters)

                # Geocode the destination (with a position only)
                end_gf, end_addr = None, None
                if lat is not None and lon is not None:
                    end_gf = await find_geofence(session, lat, lon, owner_id=vehicle.owner_id)
                    end_addr = end_gf.name if end_gf else await reverse_geocode(lat, lon)
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
                _LAST_CLOSED[vin] = {"id": drive.id, "end_time": now, "odometer": odometer, "lat": lat, "lon": lon}
                _remember_idle(vin, now, lat, lon, odometer, battery, telemetry, is_moving)
                return drive

        if not is_moving:
            await cls._relocate_last_end(session, vehicle, now, lat, lon, odometer)
            if active_drive_id is None:
                await cls._record_unseen_drive(session, vehicle, now, telemetry)
        _remember_idle(vin, now, lat, lon, odometer, battery, telemetry, is_moving)
        return None

    @classmethod
    async def _record_unseen_drive(cls, session: AsyncSession, vehicle: Vehicle, now: datetime.datetime, telemetry: Dict) -> Optional[Drive]:
        """The car is off and reads UNSEEN_MIN_KM or more than at the last stopped reading, with no trip in between: it drove
        while nobody was looking. Records that trip from the two readings (km, battery, fuel, where it was before and where
        it is now), with estimated times and no route, marked `unseen`."""
        vin = vehicle.vin
        odometer = telemetry.get("odometer_km")
        prev = await cls._previous_idle(session, vin, now)
        if (odometer is None or not prev or prev.get("odometer") is None or telemetry.get("high_voltage_on") is True
                or telemetry.get("state") in CAR_ON_STATES):
            return None                                          # still on: if it is driving, the trip starts with the next reading
        km = int(odometer) - int(prev["odometer"])
        hours = max((now - prev["time"]).total_seconds(), 1.0) / 3600.0
        if km < UNSEEN_MIN_KM or km / hours > UNSEEN_MAX_SPEED_KMH:
            return None
        # A trip already recorded since that reading (built from GPS fixes, or closed on this very reading) has those km
        known = (await session.execute(select(Drive.id).where(Drive.vin == vin, Drive.end_time.is_not(None),
                                                               Drive.end_time > prev["time"]).limit(1))).first()
        if known is not None:
            return None

        end_time = _car_clock(telemetry.get("raw_data"))          # when the car produced this data: closer to the arrival than `now`
        if end_time is None or not prev["time"] < end_time <= now:
            end_time = now
        start_time = max(prev["time"], end_time - datetime.timedelta(hours=km / ASSUMED_TRIP_SPEED_KMH))
        lat, lon = telemetry.get("latitude"), telemetry.get("longitude")
        s_lat, s_lon = prev.get("lat"), prev.get("lon")
        start_gf = await find_geofence(session, s_lat, s_lon, owner_id=vehicle.owner_id) if s_lat is not None and s_lon is not None else None
        end_gf = await find_geofence(session, lat, lon, owner_id=vehicle.owner_id) if lat is not None and lon is not None else None
        start_address = start_gf.name if start_gf else (await reverse_geocode(s_lat, s_lon) if s_lat is not None and s_lon is not None else None)
        end_address = end_gf.name if end_gf else (await reverse_geocode(lat, lon) if lat is not None and lon is not None else None)

        battery, fuel = telemetry.get("battery_level"), telemetry.get("fuel_level")
        used = max(0.0, round(prev["battery"] - battery, 1)) if prev.get("battery") is not None and battery is not None else 0.0
        drive = Drive(
            vin=vin, unseen=True, start_estimated=True, start_time=start_time, end_time=end_time,
            duration_seconds=max(0, int((end_time - start_time).total_seconds())),
            start_lat=s_lat, start_lon=s_lon, end_lat=lat, end_lon=lon,
            start_address=start_address, end_address=end_address,
            start_geofence_id=start_gf.id if start_gf else None, end_geofence_id=end_gf.id if end_gf else None,
            start_odometer=prev["odometer"], end_odometer=odometer, distance_km=float(km),
            start_ev_odometer=prev.get("ev_odometer"), end_ev_odometer=telemetry.get("ev_odometer_km"),
            start_battery_pct=prev.get("battery"), end_battery_pct=battery, battery_used_pct=used,
            energy_consumed_kwh=round((used / 100.0) * (vehicle.battery_capacity_kwh or 19.27), 2),
            start_fuel=prev.get("fuel"), end_fuel=fuel,
            fuel_used_l=fuel_litres(prev.get("fuel"), fuel, vehicle.fuel_tank_liters) if prev.get("fuel") is not None and fuel is not None else 0.0,
            outside_temp_avg=telemetry.get("outside_temp"), speed_avg_kmh=0.0, speed_max_kmh=0.0,       # nobody measured the speed
        )
        session.add(drive)
        await session.flush()
        _LAST_CLOSED[vin] = {"id": drive.id, "end_time": now, "odometer": odometer, "lat": lat, "lon": lon}
        _LOGGER.info(f"Unseen trip recorded for {vin} [Drive #{drive.id}]: {km} km between two stopped readings")
        return drive

    @classmethod
    async def _relocate_last_end(cls, session: AsyncSession, vehicle: Vehicle, now: datetime.datetime,
                                 lat: Optional[float], lon: Optional[float], odometer: Optional[float]) -> None:
        """The car has not moved since the last trip closed (same odometer) but the cloud now places it elsewhere: the trip
        ended there. Forgotten on a restart (memory only)."""
        last = _LAST_CLOSED.get(vehicle.vin)
        if (last is None or lat is None or lon is None or odometer is None or last["odometer"] != odometer
                or last["lat"] is None or last["lon"] is None or now - last["end_time"] > RELOCATE_MAX_AGE
                or haversine_distance_meters(last["lat"], last["lon"], lat, lon) <= RELOCATE_METERS):
            return
        drive = await session.get(Drive, last["id"])
        if drive is None:
            _LAST_CLOSED.pop(vehicle.vin, None)
            return
        gf = await find_geofence(session, lat, lon, owner_id=vehicle.owner_id)
        drive.end_lat, drive.end_lon = lat, lon
        drive.end_geofence_id = gf.id if gf else None
        drive.end_address = gf.name if gf else await reverse_geocode(lat, lon)
        last.update({"lat": lat, "lon": lon})
        _LOGGER.info(f"Drive #{drive.id} of {vehicle.vin} now ends where the cloud places the parked car: {drive.end_address}")


def _car_clock(raw: Optional[Dict]) -> Optional[datetime.datetime]:
    """When the car produced a reading (`time`, epoch ms), as naive UTC; None when it does not say."""
    try:
        return datetime.datetime.fromtimestamp(int((raw or {}).get("time")) / 1000, datetime.timezone.utc).replace(tzinfo=None)
    except (TypeError, ValueError, OverflowError, OSError):
        return None


def _remember_idle(vin, now, lat, lon, odometer, battery, telemetry, is_moving) -> None:
    """Stores the last idle reading (the starting point if the next trip is detected late)."""
    if not is_moving:
        _LAST_IDLE[vin] = {"time": now, "lat": lat, "lon": lon, "odometer": odometer, "battery": battery,
                           "fuel": telemetry.get("fuel_level"), "ev_odometer": telemetry.get("ev_odometer_km")}


def _raw_ev_odometer(raw: Optional[Dict]) -> Optional[float]:
    """The electric-only odometer of a stored reading (`electricRange`, the same key core/telemetry.py reads)."""
    try:
        value = float((raw or {}).get("electricRange"))
    except (TypeError, ValueError):
        return None
    return value if value > 0 else None
