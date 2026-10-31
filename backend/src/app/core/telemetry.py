"""Normalización y decodificación de telemetría de vehículos Ebro / Chery."""
from __future__ import annotations

import datetime
import logging
from typing import Any, Dict, Optional

_LOGGER = logging.getLogger(__name__)


def safe_float(val: Any, default: Optional[float] = None) -> Optional[float]:
    """Convierte de forma segura a float."""
    if val is None or val == "":
        return default
    try:
        return float(val)
    except (ValueError, TypeError):
        return default


def safe_int(val: Any, default: Optional[int] = None) -> Optional[int]:
    """Convierte de forma segura a entero."""
    if val is None or val == "":
        return default
    try:
        return int(float(val))
    except (ValueError, TypeError):
        return default


def kpa_to_bar(kpa: Optional[float]) -> Optional[float]:
    """Convierte kPa a Bar."""
    if kpa is None:
        return None
    return round(kpa / 100.0, 2)


CHARGE_STATE_MAP = {"0": "Sin cargar", "1": "Cargando", "2": "Carga completada"}
FAST_GUN_MAP = {"0": "Desconectada", "1": "Conectada (AC/Lenta)", "2": "Conectada (Carga Rápida CCS2)"}
APPOINTMENT_CHARGE_MAP = {"0": "Desactivada", "1": "Activa", "2": "En ejecución"}


def parse_telemetry_data(
    realtime_data: Dict[str, Any],
    pos_data: Optional[Dict[str, Any]] = None,
    mqtt_data: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Combina y normaliza las lecturas de los distintos canales (Realtime REST, GPS y MQTT).
    Integra el conjunto completo de sensores y cables de la integración de Home Assistant.
    """
    raw = {}
    if realtime_data:
        raw.update(realtime_data)
    if pos_data:
        raw.update(pos_data)
    if mqtt_data:
        raw.update(mqtt_data)

    online_status = str(raw.get("onlineStatus", "")).strip() in ("1", "1.0", "true")

    # 1. Batería, Combustible y Rangos (PHEV)
    battery_level = safe_float(raw.get("dumpEnergy"))
    electric_range = safe_float(raw.get("pureElectricRange"))
    fuel_range = safe_float(raw.get("mileageSurplus"))
    # Autonomía total = eléctrica + gasolina
    if electric_range is not None and fuel_range is not None:
        total_range = round(electric_range + fuel_range, 1)
    else:
        total_range = safe_float(raw.get("mileageSurplus"))

    odometer = safe_float(raw.get("odometer"))
    ev_odometer = safe_float(raw.get("electricRange"))       # Acumulado modo 100% eléctrico
    hybrid_odometer = safe_float(raw.get("hybridMileage"))   # Kilometraje híbrido
    speed = safe_float(raw.get("vehicleSpeed") or raw.get("speed") or raw.get("gpsSpeed"), 0.0)
    fuel_level_pct = safe_float(raw.get("oilSurplus"))       # Nivel de combustible en %
    average_fuel = safe_float(raw.get("averageFuel"))        # Consumo medio L/100 km
    average_power = safe_float(raw.get("avgHkPowerKwh50km")) # Consumo medio kWh/100 km

    # 2. Posición GPS y Movimiento
    lat = safe_float(raw.get("lat") or raw.get("latitude"))
    lon = safe_float(raw.get("lon") or raw.get("longitude"))
    altitude = safe_float(raw.get("altitude"))
    heading = safe_float(raw.get("direction") or raw.get("heading"))

    # 3. Cables y Sistema de Carga
    # chargeGunState: 0=desconectado, 1=cable conectado
    cable_connected = str(raw.get("chargeGunState", "")).strip() in ("1", "1.0")
    # fastChargingGunStatus: 0=desconectada, 1=conectada AC, 2=conectada DC carga rápida
    fast_gun_raw = str(raw.get("fastChargingGunStatus", "0")).strip()
    fast_charging = fast_gun_raw in ("1", "2")
    fast_charging_gun_desc = FAST_GUN_MAP.get(fast_gun_raw, f"Desconocida ({fast_gun_raw})")

    # chargeState: 0=sin cargar, 1=cargando, 2=carga completada
    charge_state_raw = str(raw.get("chargeState", "0")).strip()
    is_charging = charge_state_raw == "1"
    charge_state_desc = CHARGE_STATE_MAP.get(charge_state_raw, f"Desconocido ({charge_state_raw})")

    # appointmentChargeState: carga programada
    appt_charge_raw = str(raw.get("appointmentChargeState", "0")).strip()
    appointment_charge_desc = APPOINTMENT_CHARGE_MAP.get(appt_charge_raw, f"Desconocido ({appt_charge_raw})")
    # None = el coche aún no ha informado (no confundir con «desactivada»)
    appointment_charge_on = (appt_charge_raw in ("1", "2")) if raw.get("appointmentChargeState") not in (None, "") else None

    remaining_charge_min = safe_int(raw.get("remainChargeTime"))
    voltage = safe_float(raw.get("totalVoltage"))
    current = safe_float(raw.get("totalCurrent"))
    power_kw = None
    if voltage is not None and current is not None:
        power_kw = round((voltage * current) / 1000.0, 2)
        if power_kw < 0:
            power_kw = abs(power_kw)

    # 4. Climatización, Asientos y Confort
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

    # 5. Bloqueo, Puertas, Ventanillas y Carrocería
    # doorLock: 0 = bloqueado, 1 = desbloqueado. Sin dato → None: antes el valor por defecto era "0", y
    # la interfaz afirmaba «Vehículo cerrado» (y puertas, maletero y ventanas cerrados) sin saberlo.
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

    # 6. Neumáticos TPMS (Presión en Bar, Temperatura en °C y Avisos)
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

    # 7. Motor Térmico y Estado Operativo
    engine_state = str(raw.get("engineState", "")).strip() in ("1", "1.0")
    # hVoltageState (alta tensión): 1 con el coche encendido (también parado en un semáforo) y 0 apagado. Es la señal fiable de
    # «coche apagado»: `engineState` es solo el motor térmico y vale 0 conduciendo en eléctrico. None = sin dato.
    hv_raw = raw.get("hVoltageState")
    high_voltage_on = None if hv_raw in (None, "") else str(hv_raw).strip() in ("1", "1.0")

    # Deducción del estado del vehículo (driving, ready, charging, plugged, parked, asleep). Ver docs/trips.md, «Estados».
    # Con la alta tensión conocida, manda ella: encendido = ready/driving; apagado = parked/asleep (la velocidad o el motor
    # térmico de una lectura vieja ya no cuentan). Sin ese dato se usa la regla antigua (velocidad o motor térmico).
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

    # 8. Reporte honesto de frescura de los datos (Honest status reporting)
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
