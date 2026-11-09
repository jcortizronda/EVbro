"""Home Assistant MQTT discovery: the car as a device with the SAME entities as the Home Assistant integration.

EVBro already publishes one retained JSON per car (`<prefix>/cars/<VIN>/json`, services/mqtt_publisher.py). This module describes
that JSON to Home Assistant through MQTT discovery (https://www.home-assistant.io/integrations/mqtt/#mqtt-discovery), so the car
appears without any configuration, and it keeps the names, `unique_id`s and `entity_id`s of the integration
`unique_id = "<VIN>_<suffix>"`, `entity_id = "<domain>.ebro_<last 4 of VIN>_<key>"`
(sent as `default_entity_id`, the key Home Assistant ≥ 2025.8 reads, and as the older `object_id`), device identifiers `("ebro", VIN)`.
The entity_id only applies when the entity is created: Home Assistant keeps the id of an entity it already knows. Automations and dashboards made for the integration keep working once that integration is removed
(Home Assistant never lets two entities share an `entity_id`; with the integration still installed the new ones would get `_2`).

Pure functions: they build (topic, payload) pairs and parse command topics; the MQTT client lives in `MqttPublisher`.
Commands from Home Assistant reach `<prefix>/cars/<VIN>/cmd/<entity>` and become the catalogue commands sent with the stored PIN
(like the timetabled pre-conditioning), audited with origin `mqtt`.
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional, Tuple

DOMAIN = "ebro"
AVAILABILITY_TOPIC_SUFFIX = "status"          # <prefix>/status: "online" / "offline" (last will)

# Jinja helpers for the value templates: a missing value is "" (unknown in Home Assistant), never a wrong OFF
_B = "{% set v = value_json.{path} %}{{ '' if v is none else ('ON' if v else 'OFF') }}"
_N = "{{ value_json.{path} if value_json.{path} is not none else '' }}"
_R2 = "{{ (value_json.{path} | round(2)) if value_json.{path} is not none else '' }}"


def _sensor(suffix: str, key: str, name: str, path: str, *, unit: Optional[str] = None, device_class: Optional[str] = None,
            state_class: Optional[str] = None, icon: Optional[str] = None, diagnostic: bool = False, template: Optional[str] = None,
            raw: Optional[List[str]] = None) -> Dict[str, Any]:
    d: Dict[str, Any] = {"component": "sensor", "suffix": suffix, "key": key, "name": name,
                         "value_template": (template or _N).replace("{path}", path)}
    if raw:
        d["raw"] = raw
    if unit:
        d["unit_of_measurement"] = unit
    if device_class:
        d["device_class"] = device_class
    if state_class:
        d["state_class"] = state_class
    if icon:
        d["icon"] = icon
    if diagnostic:
        d["entity_category"] = "diagnostic"
    return d


def _binary(suffix: str, key: str, name: str, path: str, *, device_class: Optional[str] = None, icon: Optional[str] = None,
            diagnostic: bool = False, raw: Optional[List[str]] = None) -> Dict[str, Any]:
    d: Dict[str, Any] = {"component": "binary_sensor", "suffix": suffix, "key": key, "name": name, "value_template": _B.replace("{path}", path)}
    if raw:
        d["raw"] = raw
    if device_class:
        d["device_class"] = device_class
    if icon:
        d["icon"] = icon
    if diagnostic:
        d["entity_category"] = "diagnostic"
    return d


_TIME_LEFT = ("{% set m = value_json.remaining_charge_time_min %}{% if m is none %}{% elif m < 60 %}{{ m | int }} min"
              "{% else %}{{ (m // 60) | int }} h{% if m % 60 %} {{ (m % 60) | int }} min{% endif %}{% endif %}")

# ───────────────────────── the entity list (names, suffixes and keys as in the integration) ─────────────────────────
SENSORS: List[Dict[str, Any]] = [
    _sensor("battery", "bateria", "Batería", "battery_level", unit="%", device_class="battery", state_class="measurement"),
    _sensor("speed", "velocidad", "Velocidad", "speed_kmh", unit="km/h", state_class="measurement", icon="mdi:speedometer"),
    _sensor("rt_autonomia_electrica", "autonomia_electrica", "Autonomía eléctrica", "electric_range_km", unit="km", device_class="distance",
            state_class="measurement", icon="mdi:map-marker-distance"),
    _sensor("rt_autonomia_gasolina", "autonomia_gasolina", "Autonomía gasolina", "fuel_range_km", unit="km", device_class="distance",
            state_class="measurement", icon="mdi:gas-station"),
    _sensor("rt_autonomia_total", "autonomia_total", "Autonomía total", "total_range_km", unit="km", device_class="distance",
            state_class="measurement", icon="mdi:map-marker-distance"),
    _sensor("rt_odometro", "cuentakilometros", "Cuentakilómetros", "odometer_km", unit="km", device_class="distance", state_class="total_increasing",
            icon="mdi:counter"),
    _sensor("rt_km_hibrido", "kilometraje_hibrido", "Kilometraje híbrido", "hybrid_odometer_km", unit="km", device_class="distance",
            state_class="total_increasing", icon="mdi:counter", diagnostic=True),
    _sensor("rt_km_electrico", "cuentakilometros_ev", "Cuentakilómetros EV", "ev_odometer_km", unit="km", device_class="distance",
            state_class="total_increasing", icon="mdi:counter"),
    _sensor("rt_neumatico_del_izq_presion", "presion_neumatico_del_izquierdo", "Presión neumático del. izquierdo", "tyre_pressures.fl", unit="bar",
            device_class="pressure", state_class="measurement", icon="mdi:car-tire-alert", template=_R2),
    _sensor("rt_neumatico_del_der_presion", "presion_neumatico_del_derecho", "Presión neumático del. derecho", "tyre_pressures.fr", unit="bar",
            device_class="pressure", state_class="measurement", icon="mdi:car-tire-alert", template=_R2),
    _sensor("rt_neumatico_tras_izq_presion", "presion_neumatico_tras_izquierdo", "Presión neumático tras. izquierdo", "tyre_pressures.rl", unit="bar",
            device_class="pressure", state_class="measurement", icon="mdi:car-tire-alert", template=_R2),
    _sensor("rt_neumatico_tras_der_presion", "presion_neumatico_tras_derecho", "Presión neumático tras. derecho", "tyre_pressures.rr", unit="bar",
            device_class="pressure", state_class="measurement", icon="mdi:car-tire-alert", template=_R2),
    _sensor("rt_neumatico_del_izq_temp", "temperatura_neumatico_del_izquierdo", "Temperatura neumático del. izquierdo", "tyre_temps.fl", unit="°C",
            device_class="temperature", state_class="measurement", icon="mdi:thermometer", diagnostic=True),
    _sensor("rt_neumatico_del_der_temp", "temperatura_neumatico_del_derecho", "Temperatura neumático del. derecho", "tyre_temps.fr", unit="°C",
            device_class="temperature", state_class="measurement", icon="mdi:thermometer", diagnostic=True),
    _sensor("rt_neumatico_tras_izq_temp", "temperatura_neumatico_tras_izquierdo", "Temperatura neumático tras. izquierdo", "tyre_temps.rl", unit="°C",
            device_class="temperature", state_class="measurement", icon="mdi:thermometer", diagnostic=True),
    _sensor("rt_neumatico_tras_der_temp", "temperatura_neumatico_tras_derecho", "Temperatura neumático tras. derecho", "tyre_temps.rr", unit="°C",
            device_class="temperature", state_class="measurement", icon="mdi:thermometer", diagnostic=True),
    _sensor("rt_consumo_combustible", "consumo_medio_de_combustible", "Consumo medio de combustible", "average_fuel_l100km", unit="L/100 km",
            state_class="measurement", icon="mdi:gas-station"),
    _sensor("rt_consumo_electrico", "consumo_medio_electrico", "Consumo medio eléctrico", "average_power_kwh100km", unit="kWh/100 km",
            state_class="measurement", icon="mdi:lightning-bolt"),
    _sensor("rt_combustible_restante", "combustible_restante", "Combustible restante", "fuel_level", unit="%", state_class="measurement", icon="mdi:fuel"),
    _sensor("rt_tension_hv", "tension_bateria_at", "Tensión batería AT", "battery_voltage", unit="V", device_class="voltage", state_class="measurement",
            icon="mdi:flash", diagnostic=True),
    _sensor("rt_corriente_hv", "corriente_bateria_at", "Corriente batería AT", "battery_current", unit="A", device_class="current", state_class="measurement",
            icon="mdi:current-dc", diagnostic=True),
    _sensor("rt_tiempo_carga", "tiempo_de_carga_restante", "Tiempo de carga restante", "remaining_charge_time_min", icon="mdi:timer-sand", template=_TIME_LEFT),
    _sensor("rt_estado_carga", "estado_de_carga", "Estado de carga", "charge_state_desc", icon="mdi:ev-station", diagnostic=True),
    _sensor("rt_carga_programada_estado", "estado_de_carga_programada", "Estado de carga programada", "appointment_charge_desc", icon="mdi:calendar-clock",
            diagnostic=True),
    _sensor("rt_toma_rapida", "puerto_de_carga_rapida", "Puerto de carga rápida", "fast_charging_gun_desc", icon="mdi:ev-plug-ccs2", diagnostic=True),
    _sensor("rt_temp_fijada_izq", "temperatura_fijada_izquierda", "Temperatura fijada izquierda", "target_temp_left", unit="°C", device_class="temperature",
            state_class="measurement", icon="mdi:thermometer", diagnostic=True),
    _sensor("rt_temp_fijada_der", "temperatura_fijada_derecha", "Temperatura fijada derecha", "target_temp_right", unit="°C", device_class="temperature",
            state_class="measurement", icon="mdi:thermometer", diagnostic=True),
    _sensor("lastseen", "ultimo_contacto", "Último contacto", "last_contact", device_class="timestamp", icon="mdi:car-clock", diagnostic=True),
    # Extra over the integration: the two temperatures the car reports
    _sensor("rt_temp_exterior", "temperatura_exterior", "Temperatura exterior", "outside_temp", unit="°C", device_class="temperature", state_class="measurement",
            raw=["outsideTemperature", "outCarTemperature"]),
    _sensor("rt_temp_interior", "temperatura_interior", "Temperatura interior", "inside_temp", unit="°C", device_class="temperature", state_class="measurement",
            raw=["inCarTemperature"]),
    _sensor("state", "estado", "Estado", "state", icon="mdi:car-info"),
]

BINARY_SENSORS: List[Dict[str, Any]] = [
    _binary("frontLeftDoor", "puerta_del_izquierda", "Puerta del. izquierda", "doors.fl", device_class="door"),
    _binary("frontRightDoor", "puerta_del_derecha", "Puerta del. derecha", "doors.fr", device_class="door"),
    _binary("backLeftDoor", "puerta_tras_izquierda", "Puerta tras. izquierda", "doors.rl", device_class="door"),
    _binary("backRightDoor", "puerta_tras_derecha", "Puerta tras. derecha", "doors.rr", device_class="door"),
    _binary("hood", "capo", "Capó", "doors.hood", device_class="opening", raw=["hood"]),
    _binary("liftgateOperateState", "porton_en_movimiento", "Portón en movimiento", "liftgate_operate", device_class="moving"),
    _binary("frontLeftWindowState", "ventanilla_del_izquierda", "Ventanilla del. izquierda", "windows.fl", device_class="window"),
    _binary("frontRightWindowState", "ventanilla_del_derecha", "Ventanilla del. derecha", "windows.fr", device_class="window"),
    _binary("backLeftWindowState", "ventanilla_tras_izquierda", "Ventanilla tras. izquierda", "windows.rl", device_class="window"),
    _binary("backRightWindowState", "ventanilla_tras_derecha", "Ventanilla tras. derecha", "windows.rr", device_class="window"),
    _binary("sunshadeState", "cortina_del_techo", "Cortina del techo", "windows.sunshade", device_class="window", diagnostic=True, raw=["sunshadeState"]),
    _binary("airPurification", "purificacion_de_aire", "Purificación de aire", "air_purification", device_class="running"),
    _binary("chargeGunState", "cable_de_carga_conectado", "Cable de carga conectado", "cable_connected", device_class="plug"),
    _binary("engineState", "motor", "Motor", "engine_state", device_class="running", icon="mdi:engine"),
    _binary("online", "conexion", "Conexión", "online_status", device_class="connectivity", diagnostic=True),
    _binary("rt_alta_tension", "alta_tension_activa", "Alta tensión activa", "high_voltage_on", device_class="running", diagnostic=True),
    _binary("rt_aviso_neumatico_del_izq", "aviso_neumatico_del_izquierdo", "Aviso neumático del. izquierdo", "tyre_warnings.fl", device_class="problem", diagnostic=True),
    _binary("rt_aviso_neumatico_del_der", "aviso_neumatico_del_derecho", "Aviso neumático del. derecho", "tyre_warnings.fr", device_class="problem", diagnostic=True),
    _binary("rt_aviso_neumatico_tras_izq", "aviso_neumatico_tras_izquierdo", "Aviso neumático tras. izquierdo", "tyre_warnings.rl", device_class="problem", diagnostic=True),
    _binary("rt_aviso_neumatico_tras_der", "aviso_neumatico_tras_derecho", "Aviso neumático tras. derecho", "tyre_warnings.rr", device_class="problem", diagnostic=True),
    _binary("rt_bateria_baja", "bateria_baja", "Batería baja", "soc_low_alert", device_class="battery", diagnostic=True),
    _binary("rt_aviso_carga", "aviso_de_carga_necesaria", "Aviso de carga necesaria", "charge_needed_alert", device_class="problem", diagnostic=True),
    _binary("rt_aviso_combustible_bajo", "aviso_de_combustible_bajo", "Aviso de combustible bajo", "fuel_low_alert", device_class="problem", diagnostic=True),
    _binary("charging", "cargando", "Cargando", "is_charging", device_class="battery_charging"),
]

# Comfort switches: on/off commands of the catalogue, state read from the JSON
SWITCHES: List[Dict[str, Any]] = [
    {"suffix": "frontWindshieldHeat", "key": "desempanado_del_parabrisas", "name": "Desempañado del parabrisas", "path": "front_windshield_heat",
     "on": "defrost_parabrisas", "off": "defrost_parabrisas_off", "icon": "mdi:car-defrost-front"},
    {"suffix": "rWinHeatingState", "key": "calefaccion_de_la_luneta", "name": "Calefacción de la luneta", "path": "rear_windshield_heat",
     "on": "defrost_luneta", "off": "defrost_luneta_off", "icon": "mdi:car-defrost-rear", "raw": ["rWinHeatingState"]},
    {"suffix": "steerWheelHeating", "key": "calefaccion_del_volante", "name": "Calefacción del volante", "path": "steer_wheel_heat",
     "on": "volante_caliente", "off": "volante_caliente_off", "icon": "mdi:steering"},
    {"suffix": "dSeatHeatingState", "key": "calefaccion_asiento_conductor", "name": "Calefacción asiento conductor", "path": "seat_heating.driver",
     "on": "asiento_conductor_caliente", "off": "asiento_conductor_caliente_off", "icon": "mdi:car-seat-heater"},
    {"suffix": "dSeatVentilateState", "key": "ventilacion_asiento_conductor", "name": "Ventilación asiento conductor", "path": "seat_ventilation.driver",
     "on": "asiento_conductor_ventilacion", "off": "asiento_conductor_ventilacion_off", "icon": "mdi:car-seat-cooler"},
    {"suffix": "pSeatHeatingState", "key": "calefaccion_asiento_pasajero", "name": "Calefacción asiento pasajero", "path": "seat_heating.passenger",
     "on": "asiento_pasajero_caliente", "off": "asiento_pasajero_caliente_off", "icon": "mdi:car-seat-heater"},
    {"suffix": "pSeatVentilateState", "key": "ventilacion_asiento_pasajero", "name": "Ventilación asiento pasajero", "path": "seat_ventilation.passenger",
     "on": "asiento_pasajero_ventilacion", "off": "asiento_pasajero_ventilacion_off", "icon": "mdi:car-seat-cooler"},
    {"suffix": "lSeatHeatingState2", "key": "calefaccion_asiento_tras_izquierdo", "name": "Calefacción asiento tras. izquierdo", "path": "seat_heating.rear_left",
     "on": "asiento_tras_izq_caliente", "off": "asiento_tras_izq_caliente_off", "icon": "mdi:car-seat-heater"},
    {"suffix": "rSeatHeatingState2", "key": "calefaccion_asiento_tras_derecho", "name": "Calefacción asiento tras. derecho", "path": "seat_heating.rear_right",
     "on": "asiento_tras_der_caliente", "off": "asiento_tras_der_caliente_off", "icon": "mdi:car-seat-heater"},
    {"suffix": "lSeatVentilateState2", "key": "ventilacion_asiento_tras_izquierdo", "name": "Ventilación asiento tras. izquierdo", "path": "seat_ventilation.rear_left",
     "on": "asiento_tras_izq_ventilacion", "off": "asiento_tras_izq_ventilacion_off", "icon": "mdi:car-seat-cooler"},
    {"suffix": "rSeatVentilateState2", "key": "ventilacion_asiento_tras_derecho", "name": "Ventilación asiento tras. derecho", "path": "seat_ventilation.rear_right",
     "on": "asiento_tras_der_ventilacion", "off": "asiento_tras_der_ventilacion_off", "icon": "mdi:car-seat-cooler"},
    {"suffix": "carga_programada", "key": "carga_programada", "name": "Carga programada", "path": "appointment_charge_on",
     "on": "carga_prog_on", "off": "carga_prog_off", "icon": "mdi:calendar-clock"},
]

COVERS: List[Dict[str, Any]] = [
    {"suffix": "maletero", "key": "maletero", "name": "Maletero", "path": "doors.trunk", "device_class": "door", "icon": "mdi:car-back",
     "open": "maletero_abrir", "close": "maletero_cerrar"},
    {"suffix": "ventanillas", "key": "ventanillas", "name": "Ventanillas", "path": None, "device_class": "window", "icon": "mdi:car-door",
     "open": "ventanillas_abrir", "close": "ventanillas_cerrar",
     "template": "{% set w = value_json.windows %}{% set vals = [w.fl, w.fr, w.rl, w.rr] | reject('none') | list %}"
                 "{{ '' if not vals else ('open' if vals | select | list else 'closed') }}"},
    {"suffix": "techo", "key": "techo_solar", "name": "Techo solar", "path": "windows.sunroof", "device_class": "shade", "icon": "mdi:car-select",
     "open": "techo_abrir", "close": "techo_cerrar"},
]

BUTTONS: List[Dict[str, Any]] = [
    {"suffix": "cmd_encontrar_coche_luces", "key": "encontrar_coche_luces", "name": "Buscar coche", "command": "encontrar_coche_luces", "icon": "mdi:car-light-high"},
    {"suffix": "cmd_localizar_coche_gps", "key": "localizar_coche_gps", "name": "Localizar coche (GPS)", "command": "localizar_coche_gps", "icon": "mdi:crosshairs-gps"},
    {"suffix": "cmd_ventilar_ventanillas", "key": "ventilar_ventanillas", "name": "Ventilar ventanillas", "command": "ventilar_ventanillas", "icon": "mdi:car-door"},
    # The two refresh buttons of the integration are cloud probes (they never wake the car): both map to a forced poll here
    {"suffix": "refresh_pos", "key": "actualizar_ubicacion", "name": "Actualizar ubicación", "command": "__poll__", "icon": "mdi:map-marker-refresh"},
    {"suffix": "refresh_full", "key": "actualizar_estado_completo", "name": "Actualizar estado completo", "command": "__poll__", "icon": "mdi:car-info"},
]

# Command topics: <prefix>/cars/<VIN>/cmd/<entity>  → (catalogue command, params) per payload
COVER_PAYLOADS = {"OPEN": "open", "CLOSE": "close"}


def vin4(vin: str) -> str:
    return vin[-4:].lower()


def object_id(vin: str, key: str) -> str:
    return f"ebro_{vin4(vin)}_{key}"


def device(vin: str, name: Optional[str], brand: Optional[str], model: Optional[str]) -> Dict[str, Any]:
    return {"identifiers": [f"{DOMAIN}_{vin}"], "name": name or "Ebro Auto", "manufacturer": brand or "Ebro", "model": model or None,
            "sw_version": "EVBro"}


def topics(prefix: str, vin: str) -> Dict[str, str]:
    base = f"{prefix}/cars/{vin}"
    return {"json": f"{base}/json", "location": f"{base}/location", "cmd": f"{base}/cmd", "availability": f"{prefix}/{AVAILABILITY_TOPIC_SUFFIX}"}


def reports(entry: Dict[str, Any], reported_keys: Optional[List[str]]) -> bool:
    """Whether this car can feed the entity. `raw` names the car keys behind it; an entity without `raw` is common to every model.
    With no list of keys yet (a car never read) everything is announced, as before."""
    raw = entry.get("raw")
    if not raw or not reported_keys:
        return True
    return any(k in reported_keys for k in raw)


def discovery_messages(prefix: str, ha_prefix: str, vin: str, name: Optional[str], brand: Optional[str], model: Optional[str],
                       reported_keys: Optional[List[str]] = None) -> List[Tuple[str, Dict[str, Any]]]:
    """Every (discovery topic, config payload) for one car. Retained by the caller.

    `reported_keys`: the raw keys the cloud has sent for this car (`vehicles.reported_keys`); entities whose keys it never sends are
    left out, so a S900 does not get a cabin temperature that would stay unknown for ever."""
    t = topics(prefix, vin)
    dev = device(vin, name, brand, model)
    common = {"device": dev, "availability_topic": t["availability"], "state_topic": t["json"]}
    out: List[Tuple[str, Dict[str, Any]]] = []
    node = f"ebro_{vin}"

    def cfg(component: str, suffix: str, key: str, payload: Dict[str, Any]) -> None:
        # `default_entity_id` is what Home Assistant ≥ 2025.8 honours; `object_id` is kept for older cores (unknown keys are dropped).
        body = {**common, **payload, "unique_id": f"{vin}_{suffix}", "object_id": object_id(vin, key),
                "default_entity_id": f"{component}.{object_id(vin, key)}"}
        out.append((f"{ha_prefix}/{component}/{node}/{suffix}/config", body))

    for s in SENSORS:
        if not reports(s, reported_keys):
            continue
        payload = {k: v for k, v in s.items() if k not in ("component", "suffix", "key", "raw")}
        cfg("sensor", s["suffix"], s["key"], payload)
    for b in BINARY_SENSORS:
        if not reports(b, reported_keys):
            continue
        payload = {k: v for k, v in b.items() if k not in ("component", "suffix", "key", "raw")}
        cfg("binary_sensor", b["suffix"], b["key"], payload)
    for sw in SWITCHES:
        if not reports(sw, reported_keys):
            continue
        cfg("switch", sw["suffix"], sw["key"], {"name": sw["name"], "icon": sw["icon"], "value_template": _B.replace("{path}", sw["path"]),
                                                "state_on": "ON", "state_off": "OFF", "command_topic": f"{t['cmd']}/{sw['key']}",
                                                "payload_on": "ON", "payload_off": "OFF", "optimistic": False})
    for c in COVERS:
        template = c.get("template") or ("{% set v = value_json." + c["path"] + " %}{{ '' if v is none else ('open' if v else 'closed') }}")
        cfg("cover", c["suffix"], c["key"], {"name": c["name"], "icon": c["icon"], "device_class": c["device_class"], "value_template": template,
                                             "state_open": "open", "state_closed": "closed", "command_topic": f"{t['cmd']}/{c['key']}",
                                             "payload_open": "OPEN", "payload_close": "CLOSE", "payload_stop": None, "optimistic": False})
    cfg("lock", "lock", "cierre_centralizado", {"name": "Cierre centralizado", "icon": "mdi:car-door-lock",
                                                "value_template": "{% set v = value_json.is_locked %}{{ '' if v is none else ('LOCKED' if v else 'UNLOCKED') }}",
                                                "state_locked": "LOCKED", "state_unlocked": "UNLOCKED", "command_topic": f"{t['cmd']}/cierre_centralizado",
                                                "payload_lock": "LOCK", "payload_unlock": "UNLOCK", "optimistic": False})
    cfg("climate", "climate", "climatizacion", {"name": "Climatización", "icon": "mdi:air-conditioner", "modes": ["off", "heat_cool"],
                                                "mode_state_topic": t["json"],
                                                "mode_state_template": "{% set v = value_json.climate_on %}{{ '' if v is none else ('heat_cool' if v else 'off') }}",
                                                "mode_command_topic": f"{t['cmd']}/climatizacion_modo",
                                                "temperature_state_topic": t["json"], "temperature_state_template": _N.replace("{path}", "target_temp"),
                                                "temperature_command_topic": f"{t['cmd']}/climatizacion_temperatura",
                                                "current_temperature_topic": t["json"], "current_temperature_template": _N.replace("{path}", "inside_temp"),
                                                "min_temp": 16, "max_temp": 30, "temp_step": 1, "temperature_unit": "C", "precision": 1.0,
                                                "optimistic": False})
    for b in BUTTONS:
        cfg("button", b["suffix"], b["key"], {"name": b["name"], "icon": b["icon"], "command_topic": f"{t['cmd']}/{b['key']}", "payload_press": "PRESS"})
    cfg("device_tracker", "position", "ubicacion", {"name": "Ubicación", "icon": "mdi:car", "json_attributes_topic": t["location"],
                                                    "source_type": "gps", "state_topic": t["location"],
                                                    "value_template": "{{ 'not_home' if value_json.latitude is not none else '' }}"})
    for o in out:
        o[1].pop("state_topic", None) if o[0].startswith(f"{ha_prefix}/button/") else None
    return out


def location_payload(telemetry: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    lat, lon = telemetry.get("latitude"), telemetry.get("longitude")
    if lat is None or lon is None:
        return None
    return {"latitude": lat, "longitude": lon, "gps_accuracy": 25, "speed_kmh": telemetry.get("speed_kmh"), "heading": telemetry.get("heading")}


def command_from(prefix: str, topic: str, payload: str, last_target_temp: Optional[float]) -> Optional[Tuple[str, str, Optional[Dict[str, Any]]]]:
    """(vin, catalogue command or `__poll__`, params) for a message on a command topic; None when it is not one."""
    head = f"{prefix}/cars/"
    if not topic.startswith(head):
        return None
    rest = topic[len(head):].split("/")
    if len(rest) != 3 or rest[1] != "cmd":
        return None
    vin, entity = rest[0], rest[2]
    value = (payload or "").strip()
    for sw in SWITCHES:
        if entity == sw["key"]:
            return (vin, sw["on"] if value.upper() == "ON" else sw["off"], None) if value.upper() in ("ON", "OFF") else None
    for c in COVERS:
        if entity == c["key"]:
            return (vin, c[COVER_PAYLOADS[value.upper()]], None) if value.upper() in COVER_PAYLOADS else None
    if entity == "cierre_centralizado":
        return (vin, "bloquear" if value.upper() == "LOCK" else "desbloquear", None) if value.upper() in ("LOCK", "UNLOCK") else None
    if entity == "climatizacion_modo":
        if value == "off":
            return (vin, "clima_off", None)
        if value == "heat_cool":
            return (vin, "clima_on", {"temperature": f"{last_target_temp or 21.0:.1f}"})
        return None
    if entity == "climatizacion_temperatura":
        try:
            temp = max(16.0, min(30.0, float(value)))
        except ValueError:
            return None
        return (vin, "clima_on", {"temperature": f"{temp:.1f}"})
    for b in BUTTONS:
        if entity == b["key"]:
            return (vin, b["command"], None)
    return None
