"""Car telemetry: which fields exist and how an MQTT message is read.

This module **knows nothing about FastAPI or the DB**: it is the map of the vehicle's telemetry
and the pure functions that interpret it. It used to live inside the coordinator, where the field
table and the payload parsing were mixed with locks, timers and task creation — so the only
way to test "does a driving heartbeat count as contact with data?" was to bring up a whole
server.

Parsing returns a `CarMessage`: what kind of message it is, which state fields it carries and
whether it is worth refreshing the UI. Decisions that depend on live state (locks, burst,
timers) stay in `services/car_events.py`, which is where that state lives.
"""
from __future__ import annotations

from dataclasses import dataclass, field
import hashlib
import json
import logging
from typing import Any

from .constants import MAX_STATUS_LEN, SERVICE_TYPE_POSITION

_LOGGER = logging.getLogger(__name__)


def truncate_status(text: str, limit: int = MAX_STATUS_LEN) -> str:
    """Trim a status text to the cap, with an ellipsis."""
    return text if len(text) <= limit else text[: limit - 1] + "…"


# Map car-field → entity.
# kind: open|onoff → binary_sensor ON if != 0 ; lock → 0=Locked/1=Unlocked ; level → sensor 0-3
SENSORS = [
    {"key": "frontLeftDoor",  "name": "Puerta del. izquierda",  "comp": "binary_sensor", "dclass": "door",   "kind": "open"},
    {"key": "frontRightDoor", "name": "Puerta del. derecha",  "comp": "binary_sensor", "dclass": "door",   "kind": "open"},
    {"key": "backLeftDoor",   "name": "Puerta tras. izquierda", "comp": "binary_sensor", "dclass": "door",   "kind": "open"},
    {"key": "backRightDoor",  "name": "Puerta tras. derecha", "comp": "binary_sensor", "dclass": "door",   "kind": "open"},
    {"key": "trunkDoor",      "name": "Maletero",               "comp": "binary_sensor", "dclass": "opening","kind": "open"},
    {"key": "hood",           "name": "Capó",              "comp": "binary_sensor", "dclass": "opening","kind": "open"},
    {"key": "liftgateOperateState", "name": "Portón en movimiento", "comp": "binary_sensor", "dclass": "moving", "kind": "onoff"},
    {"key": "doorLock",       "name": "Cierre centralizado",           "comp": "sensor",        "dclass": None,     "kind": "lock"},
    {"key": "frontLeftWindowState",  "name": "Ventanilla del. izquierda",  "comp": "binary_sensor", "dclass": "window", "kind": "open"},
    {"key": "frontRightWindowState", "name": "Ventanilla del. derecha",  "comp": "binary_sensor", "dclass": "window", "kind": "open"},
    {"key": "backLeftWindowState",   "name": "Ventanilla tras. izquierda", "comp": "binary_sensor", "dclass": "window", "kind": "open"},
    {"key": "backRightWindowState",  "name": "Ventanilla tras. derecha", "comp": "binary_sensor", "dclass": "window", "kind": "open"},
    {"key": "sunroofState",   "name": "Techo solar",      "comp": "binary_sensor", "dclass": "window", "kind": "open"},
    {"key": "sunshadeState",  "name": "Cortina del techo",       "comp": "binary_sensor", "dclass": "window", "kind": "open", "diag": True},
    {"key": "frontHVACState", "name": "Climatización",               "comp": "binary_sensor", "dclass": "running","kind": "onoff"},
    {"key": "airPurification","name": "Purificación de aire",  "comp": "binary_sensor", "dclass": "running","kind": "onoff"},
    {"key": "frontWindshieldHeat", "name": "Desempañado del parabrisas", "comp": "binary_sensor", "dclass": "running", "kind": "onoff"},
    {"key": "fWinHeatingState","name": "Calefacción del parabrisas", "comp": "binary_sensor", "dclass": "running", "kind": "onoff", "diag": True},
    {"key": "rWinHeatingState","name": "Calefacción de la luneta",    "comp": "binary_sensor", "dclass": "running", "kind": "onoff"},
    {"key": "steerWheelHeating","name": "Calefacción del volante",   "comp": "binary_sensor", "dclass": "running", "kind": "onoff"},
    {"key": "dSeatHeatingState","name": "Calefacción asiento conductor",     "comp": "binary_sensor", "dclass": "running", "kind": "onoff"},
    {"key": "pSeatHeatingState","name": "Calefacción asiento pasajero","comp": "binary_sensor", "dclass": "running", "kind": "onoff"},
    {"key": "dSeatVentilateState","name": "Ventilación asiento conductor",      "comp": "binary_sensor", "dclass": "running", "kind": "onoff"},
    {"key": "pSeatVentilateState","name": "Ventilación asiento pasajero", "comp": "binary_sensor", "dclass": "running", "kind": "onoff"},
    {"key": "lSeatHeatingState2","name": "Calefacción asiento tras. izquierdo",      "comp": "sensor", "dclass": None, "kind": "level", "icon": "mdi:car-seat-heater"},
    {"key": "rSeatHeatingState2","name": "Calefacción asiento tras. derecho",      "comp": "sensor", "dclass": None, "kind": "level", "icon": "mdi:car-seat-heater"},
    # NB: rear CENTRE seat (mSeat*State2) removed: this car does not have it
    # (no heating/ventilation in the middle seat) → sensors always missing.
    {"key": "lSeatVentilateState2","name": "Ventilación asiento tras. izquierdo",       "comp": "sensor", "dclass": None, "kind": "level", "icon": "mdi:car-seat-cooler"},
    {"key": "rSeatVentilateState2","name": "Ventilación asiento tras. derecho",       "comp": "sensor", "dclass": None, "kind": "level", "icon": "mdi:car-seat-cooler"},
    # — extra telemetry (fields already sent by the car in the 5A02) —
    {"key": "chargeGunState", "name": "Cable de carga conectado",  "comp": "binary_sensor", "dclass": "plug",    "kind": "onoff"},
    {"key": "engineState",    "name": "Motor",          "comp": "binary_sensor", "dclass": "running", "kind": "onoff", "icon": "mdi:engine"},
    # sunroofMoveState = sunroof movement PHASE code (values 1/2/3/4/8, never 0):
    # not a clean on/off (no known "stopped" value) → raw diagnostic sensor.
    {"key": "sunroofMoveState", "name": "Estado del techo", "comp": "sensor", "dclass": None, "kind": "value", "icon": "mdi:car-select", "diag": True},
    # NB fields deliberately NOT mapped (verified on real events.jsonl, 2026-06-21):
    #   rangeUnit / averageFuelUnit / tirePressureUnit are ALWAYS "1" = they are unit-of-measure
    #   FLAGS, NOT the value. The car does not send the real range/consumption/tyre pressure
    #   value on this channel → mapping them would show a fixed "1". Postponed: the other
    #   channel is needed (realtime /asr/manager or nested TPMS structure).
]
META = {s["key"]: s for s in SENSORS}

# Meta-fields of command CONFIRMATION pushes (110x/1105/1135…): they are NOT vehicle state
# telemetry → they do not go among the "fields".
CMD_CONFIRM_META = ("result", "resultTime", "seq", "reason", "hasAsy")

# [MED] "geo" fields allowed in the position (1301 push / realtime probe). ONLY the
# geolocation is kept: battery/speed/online live in `data["realtime"]`.
GEO_KEYS = ("lat", "lon", "latitude", "longitude", "speed", "vehicleSpeed",
            "direction", "heading", "altitude", "gpsTime", "positionTime")

# Clocks present in the realtime frame. Dual use, hence a single definition:
#   * they are IGNORED when comparing two readings (otherwise two responses with identical
#     data would always look "changed");
#   * they are the candidates, IN THIS ORDER, for dating the first frame after startup.
# Order matters for the latter, so it is a tuple and the set is derived from it.
CLOCK_FIELDS = ("resultTime", "collectTime", "time", "updateTime")
_CLOCK_FIELDS = frozenset(CLOCK_FIELDS)

# The car emits a push with ONLY this field every few seconds while driving: it is a
# heartbeat, not telemetry. See `CarMessage.meaningful`.
_HEARTBEAT_FIELD = "time"


def is_unit_flag(key: str) -> bool:
    """True if the field is a UNIT-OF-MEASURE FLAG, not a value.

    Chery API convention: fields ending in `Unit` (`rangeUnit`,
    `averageFuelUnit`, `tirePressureUnit`, `avgHkPowerUnit`…) are always 1 or 2 and only say
    *which unit* the homonymous field is expressed in — they carry no data. The
    auto-discovery of unmapped fields (diagnostics monitor) flagged them as
    "to be mapped": pure noise, hiding the possible REAL fields still to be discovered.
    Filtering by suffix also covers any that appear in the future."""
    return key.endswith("Unit")


def geo_only(data: dict) -> dict:
    """The geolocation fields of a frame, and only those.

    Used both for the position push (1301) and for the realtime probe response.
    Filtering by `GEO_KEYS` instead of storing `**data` is what keeps battery/speed
    out of the position (they live in `data["realtime"]`)."""
    return {k: data[k] for k in GEO_KEYS if k in data}


def _without_clocks(obj: Any) -> Any:
    """Copy of the telemetry without the clock fields, at any depth."""
    if isinstance(obj, dict):
        return {k: _without_clocks(v) for k, v in obj.items() if k not in _CLOCK_FIELDS}
    if isinstance(obj, list):
        return [_without_clocks(v) for v in obj]
    return obj


def content_fingerprint(data: dict) -> str:
    """Fingerprint of the telemetry CONTENT: changes only if a real value changes.

    It is what allows dating "car data updated" by the content and not by the
    `resultTime` the backend sends — measured on 22/07 that this clock stays frozen
    while the values do change, i.e. exactly the opposite of what the sensor promises."""
    return hashlib.sha256(
        json.dumps(_without_clocks(data), sort_keys=True, default=str).encode("utf-8")
    ).hexdigest()


def format_command_result(data: dict) -> str:
    """Turn the REAL result of a command confirmation push into a sentence for the
    user.

    Different from the backend's "accepted" (A00079, shown when sending): this is what the
    CAR reports after trying to execute. CONSERVATIVE interpretation, based on real data
    (events.jsonl 2026-06-21) and on the meaning of the bean:
      - `reason` (list) is only filled on failures → if present = NOT achieved;
      - `result`: 5 = asynchronous operation still running (always with hasAsy=1);
        1/2 = executed/applied (vehicle state updated);
      - other codes → reported raw, without inventing their meaning."""
    # Local import: `const` is already imported above, but these two names are only used here
    # and read better next to the table they document.
    from .constants import CMD_RESULT_APPLIED, CMD_RESULT_ASYNC_RUNNING

    result = str(data.get("result", "")).strip()
    reason = data.get("reason")
    if reason:  # list of failure reasons reported by the car
        return truncate_status(f"El coche ha señalado un problema ❌ ({reason})")
    if result == CMD_RESULT_ASYNC_RUNNING:
        return "Comando en ejecución en el coche… ⏳"
    if result in CMD_RESULT_APPLIED:
        return "Comando ejecutado y confirmado por el coche ✅"
    return truncate_status(f"Confirmación recibida del coche (código {result or '?'})")


@dataclass(frozen=True)
class CarMessage:
    """An MQTT message from the car, already interpreted.

    Separating WHAT the message says from WHAT we do with it is what allows testing the
    interpretation rules (is this a confirmation?, does it carry data or is it a heartbeat?)
    without bringing up the server.
    """

    service_type: str
    #: raw payload as it arrived (consumed by the monitor's auto-discovery).
    data: dict[str, Any] = field(default_factory=dict)
    #: vehicle STATE fields, already as strings and without the confirmation meta-fields.
    state_fields: dict[str, str] = field(default_factory=dict)
    #: geolocation, only if this message is a position report.
    geo: dict[str, Any] = field(default_factory=dict)

    @property
    def is_confirmation(self) -> bool:
        """True if it is the car's response to a command (110x/1105/1135…).

        Recognised by the presence of `result`/`seq`: "pure" 5A02 telemetry does not carry them.
        """
        return "result" in self.data or "seq" in self.data

    @property
    def meaningful(self) -> bool:
        """True if it carries real data; False if it is the driving heartbeat (only `time`).

        While driving, the car emits a `time`-only push every few seconds. That heartbeat
        must not move «Último contacto» nor write to the recorder during the whole
        trip — but it does count for detecting driving and the "awake" state, which are measured
        on the message timestamps, not on their content."""
        return any(k != _HEARTBEAT_FIELD for k in self.data)


def parse_car_message(payload: bytes | str) -> CarMessage | None:
    """Decode the car's MQTT payload. `None` if it cannot be interpreted.

    The envelope varies: some messages carry the data under `content.data` and others are
    already the content itself. It is normalised here so the rest of the component always sees
    the same shape."""
    try:
        obj = json.loads(payload.decode("utf-8") if isinstance(payload, bytes) else payload)
    except Exception as err:
        _LOGGER.debug("[auto] payload MQTT no decodificable: %s", err)
        return None

    content = obj.get("content", obj) if isinstance(obj, dict) else {}
    data = content.get("data", {}) if isinstance(content, dict) else {}
    if not isinstance(data, dict):
        data = {}
    service_type = str(content.get("serviceType")) if isinstance(content, dict) else ""

    # The STATE fields: without the heartbeat and without the confirmation meta-fields, which
    # describe the command and not the vehicle.
    state_fields = {
        k: str(v)
        for k, v in data.items()
        if k != _HEARTBEAT_FIELD and k not in CMD_CONFIRM_META
    }

    # Position is told apart by the message TYPE, not by the mere presence of lat/lon:
    # a 5A02 could carry them and it would not be a position report.
    geo = (
        geo_only(data)
        if service_type == SERVICE_TYPE_POSITION and "lat" in data and "lon" in data
        else {}
    )
    return CarMessage(service_type=service_type, data=data, state_fields=state_fields, geo=geo)


def unknown_fields(message: CarMessage) -> list[tuple[str, Any]]:
    """5A02 fields the car sends but `META` does not map.

    It is the channel for discovering the telemetry still missing (range/TPMS). It is limited to
    5A02 on purpose: comparing the fields of a POSITION push against `META` flagged `lat`/`lon`
    as "unmapped" — false positives, and worse, a coordinate ended up recorded as a
    sample that way (leak seen in the field on 2026-07-20). `GEO_KEYS` is excluded anyway, in case
    the car put the position inside a 5A02."""
    from .constants import SERVICE_TYPE_TELEMETRY

    if message.service_type != SERVICE_TYPE_TELEMETRY:
        return []
    return [
        (k, v)
        for k, v in message.data.items()
        if k != _HEARTBEAT_FIELD
        and k not in CMD_CONFIRM_META
        and k not in META
        and k not in GEO_KEYS
        and not is_unit_flag(k)
    ]


# Re-exported so whoever imports the module does not have to go to `const` for the length
# cap when composing status messages.
__all__ = [
    "CLOCK_FIELDS",
    "CMD_CONFIRM_META",
    "GEO_KEYS",
    "MAX_STATUS_LEN",
    "META",
    "SENSORS",
    "CarMessage",
    "content_fingerprint",
    "format_command_result",
    "geo_only",
    "is_unit_flag",
    "parse_car_message",
    "unknown_fields",
]
