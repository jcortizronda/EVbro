"""Home Assistant MQTT discovery: the same entities as the Home Assistant integration, and commands coming back from HA."""
from __future__ import annotations

import asyncio
import json

import pytest

from app.config import settings
from app.core import catalog
from app.services import ha_discovery
from app.services.mqtt_publisher import MqttPublisher

VIN = "LSJA0000000001234"


class FakeClient:
    def __init__(self) -> None:
        self.published: list[tuple[str, str, bool]] = []

    def publish(self, topic, payload, qos=0, retain=False):
        self.published.append((topic, payload, retain))


def test_discovery_keeps_the_integration_names_ids_and_device() -> None:
    msgs = ha_discovery.discovery_messages("evbro", "homeassistant", VIN, "Mi Ebro", "Ebro", "S900 PHEV")
    by_topic = {t: p for t, p in msgs}
    # the same unique_id / entity_id scheme as the integration: <VIN>_<suffix> and ebro_<vin4>_<key>
    bat = by_topic[f"homeassistant/sensor/ebro_{VIN}/battery/config"]
    assert bat["unique_id"] == f"{VIN}_battery" and bat["object_id"] == "ebro_1234_bateria" and bat["name"] == "Batería"
    assert bat["default_entity_id"] == "sensor.ebro_1234_bateria"                     # what HA ≥ 2025.8 reads (object_id is ignored there)
    assert all(p["default_entity_id"] == f"{t.split('/')[1]}.{p['object_id']}" for t, p in msgs)
    assert bat["device_class"] == "battery" and bat["unit_of_measurement"] == "%" and bat["state_topic"] == f"evbro/cars/{VIN}/json"
    assert bat["device"] == {"identifiers": [f"ebro_{VIN}"], "name": "Mi Ebro", "manufacturer": "Ebro", "model": "S900 PHEV", "sw_version": "EVBro"}
    assert bat["availability_topic"] == "evbro/status"
    odo = by_topic[f"homeassistant/sensor/ebro_{VIN}/rt_odometro/config"]
    assert odo["object_id"] == "ebro_1234_cuentakilometros" and odo["state_class"] == "total_increasing"
    door = by_topic[f"homeassistant/binary_sensor/ebro_{VIN}/frontLeftDoor/config"]
    assert door["object_id"] == "ebro_1234_puerta_del_izquierda" and door["device_class"] == "door" and "value_json.doors.fl" in door["value_template"]
    lock = by_topic[f"homeassistant/lock/ebro_{VIN}/lock/config"]
    assert lock["object_id"] == "ebro_1234_cierre_centralizado" and lock["command_topic"] == f"evbro/cars/{VIN}/cmd/cierre_centralizado"
    trunk = by_topic[f"homeassistant/cover/ebro_{VIN}/maletero/config"]
    assert trunk["object_id"] == "ebro_1234_maletero" and trunk["payload_open"] == "OPEN" and trunk["device_class"] == "door"
    clima = by_topic[f"homeassistant/climate/ebro_{VIN}/climate/config"]
    assert clima["object_id"] == "ebro_1234_climatizacion" and clima["modes"] == ["off", "heat_cool"] and clima["min_temp"] == 16 and clima["max_temp"] == 30
    pos = by_topic[f"homeassistant/device_tracker/ebro_{VIN}/position/config"]
    assert pos["object_id"] == "ebro_1234_ubicacion" and pos["json_attributes_topic"] == f"evbro/cars/{VIN}/location"
    # rear seats, window venting, the GPS request and the two car warnings carry the integration's ids too
    rear = by_topic[f"homeassistant/switch/ebro_{VIN}/lSeatHeatingState2/config"]
    assert rear["default_entity_id"] == "switch.ebro_1234_calefaccion_asiento_tras_izquierdo" and "seat_heating.rear_left" in rear["value_template"]
    assert by_topic[f"homeassistant/button/ebro_{VIN}/cmd_ventilar_ventanillas/config"]["command_topic"] == f"evbro/cars/{VIN}/cmd/ventilar_ventanillas"
    assert by_topic[f"homeassistant/button/ebro_{VIN}/cmd_localizar_coche_gps/config"]["default_entity_id"] == "button.ebro_1234_localizar_coche_gps"
    assert by_topic[f"homeassistant/binary_sensor/ebro_{VIN}/rt_aviso_carga/config"]["default_entity_id"] == "binary_sensor.ebro_1234_aviso_de_carga_necesaria"
    assert "value_json.fuel_low_alert" in by_topic[f"homeassistant/binary_sensor/ebro_{VIN}/rt_aviso_combustible_bajo/config"]["value_template"]
    # the two refresh buttons are cloud probes, never a command that wakes the car
    assert {b["command"] for b in ha_discovery.BUTTONS if b["suffix"].startswith("refresh_")} == {"__poll__"}
    # every unique_id is unique, every component is one Home Assistant knows
    ids = [p["unique_id"] for _, p in msgs]
    assert len(ids) == len(set(ids)) and len(ids) > 60
    assert {t.split("/")[1] for t, _ in msgs} <= {"sensor", "binary_sensor", "switch", "cover", "lock", "climate", "button", "device_tracker"}
    # every command referenced by a switch, cover or button exists in the catalogue
    cmds = {c for sw in ha_discovery.SWITCHES for c in (sw["on"], sw["off"])} | {c[k] for c in ha_discovery.COVERS for k in ("open", "close")}
    cmds |= {b["command"] for b in ha_discovery.BUTTONS if b["command"] != "__poll__"} | {"bloquear", "desbloquear", "clima_on", "clima_off"}
    assert cmds <= set(catalog.CMD_MAP), cmds - set(catalog.CMD_MAP)


@pytest.mark.parametrize("topic, payload, expected", [
    (f"evbro/cars/{VIN}/cmd/cierre_centralizado", "LOCK", (VIN, "bloquear", None)),
    (f"evbro/cars/{VIN}/cmd/cierre_centralizado", "UNLOCK", (VIN, "desbloquear", None)),
    (f"evbro/cars/{VIN}/cmd/maletero", "OPEN", (VIN, "maletero_abrir", None)),
    (f"evbro/cars/{VIN}/cmd/ventanillas", "CLOSE", (VIN, "ventanillas_cerrar", None)),
    (f"evbro/cars/{VIN}/cmd/techo_solar", "OPEN", (VIN, "techo_abrir", None)),
    (f"evbro/cars/{VIN}/cmd/calefaccion_del_volante", "ON", (VIN, "volante_caliente", None)),
    (f"evbro/cars/{VIN}/cmd/calefaccion_del_volante", "OFF", (VIN, "volante_caliente_off", None)),
    (f"evbro/cars/{VIN}/cmd/carga_programada", "on", (VIN, "carga_prog_on", None)),
    (f"evbro/cars/{VIN}/cmd/climatizacion_modo", "off", (VIN, "clima_off", None)),
    (f"evbro/cars/{VIN}/cmd/climatizacion_modo", "heat_cool", (VIN, "clima_on", {"temperature": "22.0"})),
    (f"evbro/cars/{VIN}/cmd/climatizacion_temperatura", "24", (VIN, "clima_on", {"temperature": "24.0"})),
    (f"evbro/cars/{VIN}/cmd/climatizacion_temperatura", "99", (VIN, "clima_on", {"temperature": "30.0"})),
    (f"evbro/cars/{VIN}/cmd/encontrar_coche_luces", "PRESS", (VIN, "encontrar_coche_luces", None)),
    (f"evbro/cars/{VIN}/cmd/actualizar_estado_completo", "PRESS", (VIN, "__poll__", None)),
    (f"evbro/cars/{VIN}/cmd/maletero", "STOP", None),                       # not a payload we act on
    (f"evbro/cars/{VIN}/cmd/lo_que_sea", "ON", None),                        # unknown entity
    (f"evbro/cars/{VIN}/json", "{}", None),                                  # our own state topic
    (f"otro/cars/{VIN}/cmd/maletero", "OPEN", None),                            # another prefix
])
def test_commands_from_home_assistant_map_to_the_catalogue(topic, payload, expected) -> None:
    assert ha_discovery.command_from("evbro", topic, payload, 22.0) == expected


async def test_the_publisher_announces_once_publishes_location_and_runs_commands_only_for_published_cars(monkeypatch) -> None:
    monkeypatch.setattr(settings, "MQTT_TOPIC_PREFIX", "evbro")
    monkeypatch.setattr(settings, "MQTT_HA_DISCOVERY", True)
    fake = FakeClient()
    runs: list[tuple] = []

    async def runner(vin, command, params, origin):
        runs.append((vin, command, params, origin))
        return {"success": True}

    async def poll(vin):
        runs.append((vin, "__poll__"))

    MqttPublisher._client, MqttPublisher._connected, MqttPublisher._announced, MqttPublisher._published = fake, True, set(), set()
    MqttPublisher._loop = asyncio.get_running_loop()
    MqttPublisher.configure(runner, poll)
    car = type("V", (), {"name": "Mi Ebro", "brand": "Ebro", "model": "S900 PHEV"})()
    telemetry = {"battery_level": 64, "latitude": 40.4, "longitude": -3.7, "speed_kmh": 0, "heading": 90, "target_temp": 23.0, "raw_data": {"x": 1}}
    MqttPublisher.publish_telemetry(VIN, telemetry, car)
    MqttPublisher.publish_telemetry(VIN, telemetry, car)
    configs = [t for t, _, retain in fake.published if t.startswith("homeassistant/") and retain]
    assert len(configs) == len(ha_discovery.discovery_messages("evbro", "homeassistant", VIN, None, None, None))   # announced once, not twice
    js = json.loads(next(p for t, p, _ in fake.published if t == f"evbro/cars/{VIN}/json"))
    assert js["battery_level"] == 64 and "raw_data" not in js and js["last_contact"]
    loc = json.loads(next(p for t, p, _ in fake.published if t == f"evbro/cars/{VIN}/location"))
    assert loc["latitude"] == 40.4 and loc["longitude"] == -3.7
    # commands: a published car runs them (the climate mode uses the car's last target temperature); an unknown car is ignored
    MqttPublisher._on_command(f"evbro/cars/{VIN}/cmd/cierre_centralizado", "LOCK")
    MqttPublisher._on_command(f"evbro/cars/{VIN}/cmd/climatizacion_modo", "heat_cool")
    MqttPublisher._on_command(f"evbro/cars/{VIN}/cmd/actualizar_estado_completo", "PRESS")
    MqttPublisher._on_command("evbro/cars/OTRO0000000000000/cmd/cierre_centralizado", "UNLOCK")
    await asyncio.sleep(0.05)
    assert runs == [(VIN, "bloquear", None, "mqtt"), (VIN, "clima_on", {"temperature": "23.0"}, "mqtt"), (VIN, "__poll__")]
    MqttPublisher._client, MqttPublisher._connected, MqttPublisher._loop = None, False, None
