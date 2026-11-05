"""Refuel detector: a rise of the tank level with the car stopped.

The car reports the level as a % (`oilSurplus`) that drops while driving and rises when refuelling. The LOWEST level since the last
refuel is kept and, when a reading with the car stopped is at least `FUEL_JUMP_PCT` points above it, there is a refuel. The litres
are an estimate (% points × tank size, configurable on the vehicle); the user adds the price later.

The rise may only show up once the car is moving again: the cloud is read every minute or two, and a quick stop at the pump
can end before the next reading (05-11-2026: 5 % stopped at the station, 43 % two minutes later at 139 km/h; the refuel was
only «seen» at the next stop, 140 km on, as 5 % → 23 %). So a rise counts when the car is stopped now OR was stopped in the
previous reading; in the second case the refuel takes the time, position and odometer of that stop.

Caveats: a 0 % reading that later «comes back» is not a refuel (the car sometimes sends it before reading the tank), and two
refuels less than `MIN_GAP` apart are not recorded.
"""
from __future__ import annotations

import datetime
import logging
from typing import Dict, Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Refuel, TelemetrySnapshot, Vehicle
from app.services.geofence import reverse_geocode

_LOGGER = logging.getLogger(__name__)

FUEL_JUMP_PCT = 8.0                                  # ≈ 4 L in a 50 L tank; the gauge wobbles by 2-3 points
MIN_GAP = datetime.timedelta(minutes=30)

# lowest level seen since the last refuel, per VIN
_FUEL_LOW: Dict[str, float] = {}
_LAST_REFUEL_AT: Dict[str, datetime.datetime] = {}
# the previous reading, when the car was stopped in it (time, position, odometer and tank level); None after a moving reading
_PREV_STOP: Dict[str, Optional[Dict]] = {}


class RefuelDetector:
    @classmethod
    async def _baseline(cls, session: AsyncSession, vin: str, now: datetime.datetime) -> Optional[float]:
        """After a restart there is no level in memory: take the one of the last stored reading."""
        if vin in _FUEL_LOW:
            return _FUEL_LOW[vin]
        row = (await session.execute(
            select(TelemetrySnapshot.fuel_level)
            .where(TelemetrySnapshot.vin == vin, TelemetrySnapshot.timestamp < now - datetime.timedelta(seconds=1),
                   TelemetrySnapshot.fuel_level.is_not(None), TelemetrySnapshot.fuel_level > 0)
            .order_by(TelemetrySnapshot.timestamp.desc()).limit(1)
        )).first()
        return row[0] if row else None

    @classmethod
    async def process_snapshot(cls, session: AsyncSession, vehicle: Vehicle, telemetry: Dict) -> Optional[Refuel]:
        vin = vehicle.vin
        fuel = telemetry.get("fuel_level")
        if fuel is None:
            return None
        now = datetime.datetime.utcnow()
        stopped = (telemetry.get("speed_kmh") or 0.0) <= 2.0 and telemetry.get("state") != "driving"
        prev_stop = _PREV_STOP.get(vin)
        _PREV_STOP[vin] = {"time": now, "fuel": fuel, "odometer": telemetry.get("odometer_km"),
                           "lat": telemetry.get("latitude"), "lon": telemetry.get("longitude")} if stopped else None
        low = await cls._baseline(session, vin, now)
        if low is None or (fuel <= 0 and low > 2.0):
            # first reading, or a 0 % from a tank that was not nearly empty: an invalid reading, it counts for nothing
            if low is None and fuel > 0:
                _FUEL_LOW[vin] = fuel
            return None

        if fuel <= low:
            _FUEL_LOW[vin] = fuel                                       # consumption: the minimum goes down
            return None
        if fuel - low < FUEL_JUMP_PCT:
            return None                                                 # wobble
        # The pump stop may have ended before this reading: the rise is a refuel if the car is stopped now or was in the
        # previous reading (measured against that reading, so a wobble while stopped plus sloshing on the road is not one)
        at = None
        if stopped:
            at = {"time": now, "odometer": telemetry.get("odometer_km"), "lat": telemetry.get("latitude"), "lon": telemetry.get("longitude")}
        elif prev_stop is not None and fuel - prev_stop["fuel"] >= FUEL_JUMP_PCT:
            at = prev_stop
        if at is None:
            return None                                                 # a rise while moving: nothing
        last = _LAST_REFUEL_AT.get(vin)
        if last and now - last < MIN_GAP:
            _FUEL_LOW[vin] = fuel
            return None

        lat, lon = at["lat"], at["lon"]
        address = await reverse_geocode(lat, lon) if lat is not None and lon is not None else None
        liters = round((fuel - low) / 100.0 * (vehicle.fuel_tank_liters or 50.0), 1)
        refuel = Refuel(vin=vin, timestamp=at["time"], odometer=at["odometer"], fuel_before_pct=low, fuel_after_pct=fuel,
                        liters=liters, latitude=lat, longitude=lon, address=address, detected=True,
                        full_tank=None)                 # the gauge decides (>= 95 %) until the user says otherwise

        session.add(refuel)
        await session.flush()
        _FUEL_LOW[vin] = fuel
        _LAST_REFUEL_AT[vin] = now
        _LOGGER.info(f"Refuel detected for {vin}: {low:.0f}% -> {fuel:.0f}% (~{liters} L) [Refuel #{refuel.id}]")
        return refuel
