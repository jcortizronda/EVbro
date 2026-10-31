"""Detector y gestor de sesiones de carga."""
from __future__ import annotations

import datetime
import logging
from typing import Dict, Optional

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

# Memoria de sesiones de carga activas por VIN
_ACTIVE_CHARGES: Dict[str, int] = {}
DEFAULT_CAPACITY_KWH = 19.27


class ChargeDetector:
    @classmethod
    async def process_snapshot(
        cls,
        session: AsyncSession,
        vehicle: Vehicle,
        telemetry: Dict,
    ) -> Optional[ChargingSession]:
        """Procesa una instantánea de telemetría y gestiona el ciclo de vida de la recarga."""
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

        active_charge_id = _ACTIVE_CHARGES.get(vin)

        # 1. Iniciar nueva sesión de recarga
        if active_charge_id is None and (is_charging or (cable_connected and power_kw > 0.5)):
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
            _LOGGER.info(f"Nueva sesión de carga iniciada para {vin} [Charge #{active_charge_id}]")

        # 2. Registrar puntos de la curva de recarga
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

            # 3. Detectar fin de carga (cable desconectado o carga terminada)
            charge_ended = not cable_connected or (not is_charging and power_kw < 0.2)
            if charge_ended:
                charge.end_time = now
                charge.end_battery_pct = battery
                charge.end_range_km = electric_range
                charge.duration_seconds = max(0, int((now - charge.start_time).total_seconds()))

                # Estimación de energía añadida (% ganado y aproximación de capacidad)
                # La mayoría de Ebro / Chery PHEV tienen ~19 kWh, o EVs ~60-70 kWh
                if charge.start_battery_pct is not None and charge.end_battery_pct is not None:
                    pct_gained = max(0.0, charge.end_battery_pct - charge.start_battery_pct)
                    estimated_kwh = round((pct_gained / 100.0) * (vehicle.battery_capacity_kwh or DEFAULT_CAPACITY_KWH), 2)
                    charge.kwh_added = estimated_kwh       # estimada: % ganado × capacidad útil (configurable en el vehículo)
                    # kwh_used (energía de la red) NO se inventa: solo la indica el usuario (ver PUT /charges/{id}/cost)

                    if charge.geofence_id:
                        gf = await session.get(Geofence, charge.geofence_id)
                        cost_res = await calculate_charging_session_cost(
                            gf, charge.start_time, charge.end_time, estimated_kwh
                        )
                        charge.charge_cost = cost_res["total_cost"]
                        charge.effective_cost_per_kwh = cost_res["effective_kwh_cost"]
                        charge.cost_mode = cost_res["cost_mode"]
                    else:
                        # Fuera de geocercas: 0 €/kWh por defecto (se puede indicar el precio real en la carga)
                        charge.effective_cost_per_kwh = 0.0
                        charge.charge_cost = 0.0
                        charge.cost_mode = "default"

                await session.flush()
                _LOGGER.info(
                    f"Carga finalizada para {vin} [Charge #{charge.id}]: "
                    f"+{charge.kwh_added} kWh ({charge.start_battery_pct}% → {charge.end_battery_pct}%) "
                    f"en {charge.duration_seconds // 60} min."
                )
                _ACTIVE_CHARGES.pop(vin, None)
                return charge

        return None
