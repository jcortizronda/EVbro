"""Charging session detector and manager."""
from __future__ import annotations

import datetime
import logging
from typing import Dict, Optional, Set, Tuple

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import ChargePoint, ChargingSession, Geofence, Vehicle
from app.services.electricity_tariffs import calculate_charging_session_cost
from app.services.geofence import find_geofence, reverse_geocode

_LOGGER = logging.getLogger(__name__)

# Above this power the car cannot be on AC (its on-board charger tops out well below): it is a DC fast charger
DC_POWER_KW = 20.0


def detect_charge_type(telemetry: dict, power_kw: float) -> str:
    """`dc` when the car reports the fast-charging gun or the power is beyond AC; otherwise `ac` (the default when nothing says DC)."""
    if telemetry.get("fast_charging") or (power_kw or 0.0) > DC_POWER_KW:
        return "dc"
    return "ac"

# Active charging sessions per VIN
_ACTIVE_CHARGES: Dict[str, int] = {}
# Cars whose open session in the database has already been looked at since this process started
_RECOVERED: Set[str] = set()
# Session that ended with the cable still plugged in, and when: if the car starts charging again soon without being unplugged,
# it is the same session (a charger hiccup, a stop and restart at the post), not a new one
_PAUSED: Dict[str, Tuple[int, datetime.datetime]] = {}
DEFAULT_CAPACITY_KWH = 19.27
# A session left open across a restart is resumed if its last reading is this recent (and the car is still charging)
RESUME_MAX_AGE = datetime.timedelta(hours=3)
# A pause with the cable plugged in shorter than this joins both halves into one session
PAUSE_MERGE_MAX = datetime.timedelta(minutes=15)


class ChargeDetector:
    @classmethod
    async def _last_reading(cls, session: AsyncSession, charge: ChargingSession) -> Tuple[datetime.datetime, Optional[float], Optional[float]]:
        """Time, battery and range of the session's last curve point (its start values when it has none)."""
        point = (await session.execute(
            select(ChargePoint).where(ChargePoint.charging_session_id == charge.id).order_by(ChargePoint.timestamp.desc()).limit(1)
        )).scalar_one_or_none()
        if point is None:
            return charge.start_time, charge.start_battery_pct, charge.start_range_km
        return point.timestamp, point.battery_pct, point.range_km

    @classmethod
    async def _recover_open_session(cls, session: AsyncSession, vehicle: Vehicle, now: datetime.datetime, charging_now: bool) -> Optional[int]:
        """After a restart the active session is no longer in memory. If the car is still charging and the open session had a
        recent reading, it carries on; otherwise it is closed at its last reading, so it never stays open forever."""
        vin = vehicle.vin
        _RECOVERED.add(vin)
        open_charges = (await session.execute(
            select(ChargingSession).where(ChargingSession.vin == vin, ChargingSession.end_time.is_(None))
            .order_by(ChargingSession.start_time.desc())
        )).scalars().all()
        resumed: Optional[int] = None
        for charge in open_charges:
            last_at, battery, rng = await cls._last_reading(session, charge)
            if resumed is None and charging_now and now - last_at <= RESUME_MAX_AGE:
                resumed = charge.id
                _ACTIVE_CHARGES[vin] = charge.id
                _LOGGER.info(f"Open charging session resumed after a restart for {vin} [Charge #{charge.id}]")
            else:
                await cls._close(session, vehicle, charge, last_at, battery, rng)
                _LOGGER.info(f"Charging session left open by a restart closed at its last reading for {vin} [Charge #{charge.id}]")
        return resumed

    @classmethod
    async def _close(cls, session: AsyncSession, vehicle: Vehicle, charge: ChargingSession, at: datetime.datetime,
                     battery: Optional[float], electric_range: Optional[float]) -> None:
        """Sets the end of a session, the energy gained and its cost."""
        charge.end_time = at
        charge.end_battery_pct = battery
        charge.end_range_km = electric_range
        charge.duration_seconds = max(0, int((at - charge.start_time).total_seconds()))

        # Estimate of the energy added (% gained times an approximate capacity)
        # Most Ebro / Chery PHEVs have ~19 kWh, EVs ~60-70 kWh
        if charge.start_battery_pct is not None and charge.end_battery_pct is not None:
            pct_gained = max(0.0, charge.end_battery_pct - charge.start_battery_pct)
            estimated_kwh = round((pct_gained / 100.0) * (vehicle.battery_capacity_kwh or DEFAULT_CAPACITY_KWH), 2)
            charge.kwh_added = estimated_kwh       # estimated: % gained × usable capacity (set on the vehicle)
            # kwh_used (energy from the grid) is NOT made up: only the user enters it (see PUT /charges/{id}/cost)

            if charge.geofence_id:
                gf = await session.get(Geofence, charge.geofence_id)
                cost_res = await calculate_charging_session_cost(
                    gf, charge.start_time, charge.end_time, estimated_kwh
                )
                charge.charge_cost = cost_res["total_cost"]
                charge.effective_cost_per_kwh = cost_res["effective_kwh_cost"]
                charge.cost_mode = cost_res["cost_mode"]
            else:
                # Outside geofences: 0 €/kWh by default (the real price can be entered on the charge)
                charge.effective_cost_per_kwh = 0.0
                charge.charge_cost = 0.0
                charge.cost_mode = "default"
        await session.flush()

    @classmethod
    async def process_snapshot(
        cls,
        session: AsyncSession,
        vehicle: Vehicle,
        telemetry: Dict,
    ) -> Optional[ChargingSession]:
        """Processes a telemetry snapshot and manages the charging session's life cycle."""
        vin = vehicle.vin
        now = datetime.datetime.utcnow()

        is_charging = telemetry.get("is_charging", False)
        cable_connected = telemetry.get("cable_connected", False)
        battery = telemetry.get("battery_level")
        voltage = telemetry.get("battery_voltage")
        current = telemetry.get("battery_current")
        power_kw = telemetry.get("charge_power_kw", 0.0) or 0.0
        lat = telemetry.get("latitude")
        lon = telemetry.get("longitude")

        charging_now = bool(is_charging or (cable_connected and power_kw > 0.5))
        if not cable_connected:
            _PAUSED.pop(vin, None)                                # unplugged: whatever comes next is a new session

        active_charge_id = _ACTIVE_CHARGES.get(vin)
        if active_charge_id is None and vin not in _RECOVERED:
            active_charge_id = await cls._recover_open_session(session, vehicle, now, charging_now)

        # 1b. Charging again shortly after a pause, without unplugging: the same session carries on
        paused = _PAUSED.get(vin)
        if active_charge_id is None and charging_now and paused and now - paused[1] <= PAUSE_MERGE_MAX:
            charge = await session.get(ChargingSession, paused[0])
            if charge is not None:
                charge.end_time = None
                charge.end_battery_pct = None
                charge.end_range_km = None
                active_charge_id = charge.id
                _ACTIVE_CHARGES[vin] = charge.id
                _LOGGER.info(f"Charging resumed after a short pause for {vin} [Charge #{charge.id}]")
            _PAUSED.pop(vin, None)

        # 1. Start a new charging session
        if active_charge_id is None and charging_now:
            gf = await find_geofence(session, lat, lon, only_charging_spots=True, owner_id=vehicle.owner_id)
            addr = await reverse_geocode(lat, lon)
            if gf:
                addr = gf.name

            electric_range = telemetry.get("electric_range_km") or telemetry.get("total_range_km")
            new_session = ChargingSession(
                vin=vin,
                start_time=now,
                latitude=lat,
                longitude=lon,
                address=addr,
                geofence_id=gf.id if gf else None,
                start_battery_pct=battery,
                start_range_km=electric_range,
                max_charge_power_kw=power_kw,
                charge_type=detect_charge_type(telemetry, power_kw),
            )
            session.add(new_session)
            await session.flush()

            active_charge_id = new_session.id
            _ACTIVE_CHARGES[vin] = active_charge_id
            _LOGGER.info(f"New charging session started for {vin} [Charge #{active_charge_id}]")

        # 2. Record the points of the charging curve
        if active_charge_id is not None:
            charge = await session.get(ChargingSession, active_charge_id)
            if not charge:
                _ACTIVE_CHARGES.pop(vin, None)
                return None

            electric_range = telemetry.get("electric_range_km") or telemetry.get("total_range_km")
            if battery is not None:
                point = ChargePoint(
                    charging_session_id=active_charge_id,
                    timestamp=now,
                    battery_pct=battery,
                    voltage=voltage,
                    current=current,
                    power_kw=power_kw,
                    range_km=electric_range,
                )
                session.add(point)

            if power_kw > (charge.max_charge_power_kw or 0.0):
                charge.max_charge_power_kw = round(power_kw, 2)
            if charge.charge_type != "dc" and detect_charge_type(telemetry, power_kw) == "dc":
                charge.charge_type = "dc"                         # the fast gun (or a DC power level) showed up mid-session

            # 3. Detect the end of the charge (cable unplugged or charge finished)
            charge_ended = not cable_connected or (not is_charging and power_kw < 0.2)
            if charge_ended:
                await cls._close(session, vehicle, charge, now, battery, electric_range)
                if cable_connected:
                    _PAUSED[vin] = (charge.id, now)
                _LOGGER.info(
                    f"Charge finished for {vin} [Charge #{charge.id}]: "
                    f"+{charge.kwh_added} kWh ({charge.start_battery_pct}% → {charge.end_battery_pct}%) "
                    f"in {charge.duration_seconds // 60} min."
                )
                _ACTIVE_CHARGES.pop(vin, None)
                return charge

        return None
