"""Normalisation and decoding of Ebro / Chery vehicle telemetry."""
from __future__ import annotations

import datetime
import logging
from typing import Any, Dict, Optional

_LOGGER = logging.getLogger(__name__)


def safe_float(val: Any, default: Optional[float] = None) -> Optional[float]:
    """Safely convert to float."""
    if val is None or val == "":
        return default
    try:
        return float(val)
    except (ValueError, TypeError):
        return default


def safe_int(val: Any, default: Optional[int] = None) -> Optional[int]:
    """Safely convert to int."""
    if val is None or val == "":
        return default
    try:
        return int(float(val))
    except (ValueError, TypeError):
        return default


def kpa_to_bar(kpa: Optional[float]) -> Optional[float]:
    """Convert kPa to bar."""
    if kpa is None:
        return None
    return round(kpa / 100.0, 2)


CHARGE_STATE_MAP = {"0": "Sin cargar", "1": "Cargando", "2": "Carga completada"}
FAST_GUN_MAP = {"0": "Desconectada", "1": "Conectada (AC/Lenta)", "2": "Conectada (Carga Rápida CCS2)"}
# appointmentChargeState: 4 = the scheduled charge has just ended (seen on three cars right after a scheduled charge stopped, 2 → 4 → 0;
# 08-11-2026). Mirror of the Home Assistant integration: a change here is ported there.
APPOINTMENT_CHARGE_MAP = {"0": "Desactivada", "1": "Activa", "2": "En ejecución", "4": "Finalizada"}


def parse_telemetry_data(
    realtime_data: Dict[str, Any],
    pos_data: Optional[Dict[str, Any]] = None,
    mqtt_data: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Merge and normalise the readings from the different channels (Realtime REST, GPS and MQTT).
    Covers the full set of sensors and cables of the Home Assistant integration.
    """
    raw = {}
    if realtime_data:
        raw.update(realtime_data)
    if pos_data:
        raw.update(pos_data)
    if mqtt_data:
        raw.update(mqtt_data)

    online_status = str(raw.get("onlineStatus", "")).strip() in ("1", "1.0", "true")

    # 1. Battery, fuel and ranges (PHEV)
    battery_level = safe_float(raw.get("dumpEnergy"))
    electric_range = safe_float(raw.get("pureElectricRange"))
    fuel_range = safe_float(raw.get("mileageSurplus"))
    # Total range = electric + petrol
    if electric_range is not None and fuel_range is not None:
        total_range = round(electric_range + fuel_range, 1)
    else:
        total_range = safe_float(raw.get("mileageSurplus"))

    odometer = safe_float(raw.get("odometer"))
    ev_odometer = safe_float(raw.get("electricRange"))       # Accumulated in 100% electric mode
    hybrid_odometer = safe_float(raw.get("hybridMileage"))   # Hybrid mileage
    speed = safe_float(raw.get("vehicleSpeed") or raw.get("speed") or raw.get("gpsSpeed"), 0.0)
    fuel_level_pct = safe_float(raw.get("oilSurplus"))       # Fuel level in %
    average_fuel = safe_float(raw.get("averageFuel"))        # Average consumption L/100 km
    average_power = safe_float(raw.get("avgHkPowerKwh50km")) # Average consumption kWh/100 km

    # 2. GPS position and movement
    lat = safe_float(raw.get("lat") or raw.get("latitude"))
    lon = safe_float(raw.get("lon") or raw.get("longitude"))
    altitude = safe_float(raw.get("altitude"))
    heading = safe_float(raw.get("direction") or raw.get("heading"))

    # 3. Cables and charging system
    # chargeGunState: 0=disconnected, 1=cable connected
    cable_connected = str(raw.get("chargeGunState", "")).strip() in ("1", "1.0")
    # fastChargingGunStatus: 0=disconnected, 1=AC connected, 2=DC fast-charge connected
    fast_gun_raw = str(raw.get("fastChargingGunStatus", "0")).strip()
    fast_charging = fast_gun_raw in ("1", "2")
    fast_charging_gun_desc = FAST_GUN_MAP.get(fast_gun_raw, f"Desconocida ({fast_gun_raw})")

    # chargeState: 0=not charging, 1=charging, 2=charge complete
    charge_state_raw = str(raw.get("chargeState", "0")).strip()
    is_charging = charge_state_raw == "1"
    charge_state_desc = CHARGE_STATE_MAP.get(charge_state_raw, f"Desconocido ({charge_state_raw})")

    # appointmentChargeState: scheduled charging
    appt_charge_raw = str(raw.get("appointmentChargeState", "0")).strip()
    appointment_charge_desc = APPOINTMENT_CHARGE_MAP.get(appt_charge_raw, f"Desconocido ({appt_charge_raw})")
    # None = the car has not reported yet (not to be confused with «disabled»)
    appointment_charge_on = (appt_charge_raw in ("1", "2")) if raw.get("appointmentChargeState") not in (None, "") else None

    remaining_charge_min = safe_int(raw.get("remainChargeTime"))
    voltage = safe_float(raw.get("totalVoltage"))
    current = safe_float(raw.get("totalCurrent"))
    power_kw = None
    if voltage is not None and current is not None:
        power_kw = round((voltage * current) / 1000.0, 2)
        if power_kw < 0:
            power_kw = abs(power_kw)

    # 4. Climate, seats and comfort
    climate_on = str(raw.get("frontHVACState", "")).strip() in ("1", "1.0")
    target_temp_left = safe_float(raw.get("frontSetTempLeft"))
    target_temp_right = safe_float(raw.get("frontSetTempRight"))
    target_temp = target_temp_left or safe_float(raw.get("targetTemperature"))
    in_temp = safe_float(raw.get("inCarTemperature"))
    out_temp = safe_float(raw.get("outsideTemperature") or raw.get("outCarTemperature"))

    air_purification = str(raw.get("airPurification", "")).strip() in ("1", "1.0")
    front_windshield_heat = str(raw.get("frontWindshieldHeat") or raw.get("fWinHeatingState", "")).strip() in ("1", "1.0")
    rear_windshield_heat = str(raw.get("rWinHeatingState", "")).strip() in ("1", "1.0")
    steer_wheel_heat = str(raw.get("steerWheelHeating", "")).strip() in ("1", "1.0")

    seat_heating = {
        "driver": str(raw.get("dSeatHeatingState", "")).strip() in ("1", "1.0"),
        "passenger": str(raw.get("pSeatHeatingState", "")).strip() in ("1", "1.0"),
        "rear_left": safe_int(raw.get("lSeatHeatingState2"), 0),
        "rear_right": safe_int(raw.get("rSeatHeatingState2"), 0),
    }
    seat_ventilation = {
        "driver": str(raw.get("dSeatVentilateState", "")).strip() in ("1", "1.0"),
        "passenger": str(raw.get("pSeatVentilateState", "")).strip() in ("1", "1.0"),
        "rear_left": safe_int(raw.get("lSeatVentilateState2"), 0),
        "rear_right": safe_int(raw.get("rSeatVentilateState2"), 0),
    }

    # 5. Locking, doors, windows and body
    # doorLock: 0 = locked, 1 = unlocked. No data → None: the default used to be "0", and
    # the interface claimed «Vehículo cerrado» (and doors, boot and windows closed) without knowing.
    door_lock = raw.get("doorLock")
    is_locked = None if door_lock in (None, "") else str(door_lock).strip() == "0"
    doors = {
        "fl": safe_int(raw.get("frontLeftDoor")),
        "fr": safe_int(raw.get("frontRightDoor")),
        "rl": safe_int(raw.get("backLeftDoor")),
        "rr": safe_int(raw.get("backRightDoor")),
        "trunk": safe_int(raw.get("trunkDoor")),
        "hood": safe_int(raw.get("hood")),
    }
    liftgate_operate = safe_int(raw.get("liftgateOperateState"), 0)

    windows = {
        "fl": safe_int(raw.get("frontLeftWindowState")),
        "fr": safe_int(raw.get("frontRightWindowState")),
        "rl": safe_int(raw.get("backLeftWindowState")),
        "rr": safe_int(raw.get("backRightWindowState")),
        "sunroof": safe_int(raw.get("sunroofState")),
        "sunshade": safe_int(raw.get("sunshadeState")),
    }

    # 6. TPMS tyres (pressure in bar, temperature in °C and warnings)
    tyre_pressures = {
        "fl": kpa_to_bar(safe_float(raw.get("lFrontTyreKpa"))),
        "fr": kpa_to_bar(safe_float(raw.get("rFrontTyreKpa"))),
        "rl": kpa_to_bar(safe_float(raw.get("lRearTyreKpa"))),
        "rr": kpa_to_bar(safe_float(raw.get("rRearTyreKpa"))),
    }
    tyre_pressures_kpa = {
        "fl": safe_float(raw.get("lFrontTyreKpa")),
        "fr": safe_float(raw.get("rFrontTyreKpa")),
        "rl": safe_float(raw.get("lRearTyreKpa")),
        "rr": safe_float(raw.get("rRearTyreKpa")),
    }
    tyre_temps = {
        "fl": safe_float(raw.get("lFrontTyreTemp")),
        "fr": safe_float(raw.get("rFrontTyreTemp")),
        "rl": safe_float(raw.get("lRearTyreTemp")),
        "rr": safe_float(raw.get("rRearTyreTemp")),
    }
    tyre_warnings = {
        "fl": safe_int(raw.get("lFrontTyreCall"), 0) != 0,
        "fr": safe_int(raw.get("rFrontTyreCall"), 0) != 0,
        "rl": safe_int(raw.get("lRearTyreCall"), 0) != 0,
        "rr": safe_int(raw.get("rRearTyreCall"), 0) != 0,
    }
    soc_low_alert = safe_int(raw.get("socLowCall"), 0) == 1
    # electricityCall / oilCall: the car's own «charge needed» and «fuel low» warnings (the HA integration exposes them as realtime
    # binary sensors). 1 = warning on; missing = off.
    charge_needed_alert = safe_int(raw.get("electricityCall"), 0) == 1
    fuel_low_alert = safe_int(raw.get("oilCall"), 0) == 1

    # 7. Combustion engine and operating state
    engine_state = str(raw.get("engineState", "")).strip() in ("1", "1.0")
    # hVoltageState (high voltage): 1 with the car switched on (also when stopped at a traffic light) and 0 when off. It is the
    # reliable «car off» signal: `engineState` is only the combustion engine and is 0 when driving on electric. None = no data.
    hv_raw = raw.get("hVoltageState")
    high_voltage_on = None if hv_raw in (None, "") else str(hv_raw).strip() in ("1", "1.0")

    # Vehicle state deduction (driving, ready, charging, plugged, parked, asleep). See docs/trips.md, «Estados».
    # When the high voltage is known it wins: on = ready/driving; off = parked/asleep (speed or the combustion
    # engine from a stale reading no longer count). Without it the old rule is used (speed or combustion engine).
    state = "parked"
    if cable_connected and is_charging:
        state = "charging"
    elif cable_connected:
        state = "plugged"
    elif high_voltage_on is True:
        state = "driving" if (speed and speed > 1.0) else "ready"
    elif high_voltage_on is None and ((speed and speed > 1.0) or engine_state):
        state = "driving"
    elif not online_status:
        state = "asleep"

    # 8. Honest data-freshness reporting
    from app.core.probe import freshness
    freshness_text = freshness(raw)

    return {
        "online_status": online_status,
        "state": state,
        "high_voltage_on": high_voltage_on,
        "freshness_text": freshness_text,
        "battery_level": battery_level,
        "electric_range_km": electric_range,
        "fuel_range_km": fuel_range,
        "total_range_km": total_range,
        "fuel_level": fuel_level_pct,
        "odometer_km": odometer,
        "ev_odometer_km": ev_odometer,
        "hybrid_odometer_km": hybrid_odometer,
        "average_fuel_l100km": average_fuel,
        "average_power_kwh100km": average_power,
        "speed_kmh": speed,
        "latitude": lat,
        "longitude": lon,
        "altitude": altitude,
        "heading": heading,
        "outside_temp": out_temp,
        "inside_temp": in_temp,
        "climate_on": climate_on,
        "target_temp": target_temp,
        "target_temp_left": target_temp_left,
        "target_temp_right": target_temp_right,
        "air_purification": air_purification,
        "front_windshield_heat": front_windshield_heat,
        "rear_windshield_heat": rear_windshield_heat,
        "steer_wheel_heat": steer_wheel_heat,
        "seat_heating": seat_heating,
        "seat_ventilation": seat_ventilation,
        "battery_voltage": voltage,
        "battery_current": current,
        "charge_power_kw": power_kw,
        "is_charging": is_charging,
        "cable_connected": cable_connected,
        "fast_charging": fast_charging,
        "fast_charging_gun_desc": fast_charging_gun_desc,
        "charge_state_desc": charge_state_desc,
        "appointment_charge_desc": appointment_charge_desc,
        "appointment_charge_on": appointment_charge_on,
        "remaining_charge_time_min": remaining_charge_min,
        "is_locked": is_locked,
        "doors": doors,
        "liftgate_operate": liftgate_operate,
        "windows": windows,
        "tyre_pressures": tyre_pressures,
        "tyre_pressures_kpa": tyre_pressures_kpa,
        "tyre_temps": tyre_temps,
        "tyre_warnings": tyre_warnings,
        "soc_low_alert": soc_low_alert,
        "charge_needed_alert": charge_needed_alert,
        "fuel_low_alert": fuel_low_alert,
        "engine_state": engine_state,
        "raw_data": raw,
    }
