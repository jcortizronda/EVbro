"""Scheduled departure («Reserva tu viaje» in the official app): the car keeps its battery warm before the time you leave.

The app's own text: «El aislamiento de la batería comienza 1 hora y 30 minutos antes de su salida». The plan lives in the car;
EVBro stores nothing. It is READ from what the cloud already returns on every reading (`travelQuery`, merged into the raw
data: `mainSwitcher` and `appointmentTravelSetVOS`) and CHANGED with the `appointmentTravel` order (core/catalog.py).
docs/ebro-protocol.md, «Salida programada».

What is known and what is not (2026-11-21):
  * `travelTime` is minutes of the day in UTC, like the scheduled charge: the official app passes both through the same
    time-zone helper (`CommonUtils::dateTimeZone`). Checked on 2026-11-22: the app showed 08:00 (UTC+1) for a stored 420.
  * A car can hold several departures (an S800 had two). EVBro changes the time and the switch of the ones the car has and
    does not add or remove any.
  * The numbering of the weekdays (`travelWeekday` / `travelDate`, 1..7) has not been established, and a departure near
    midnight may move to another day in UTC. So the days are kept exactly as the car has them and are not editable here.
"""
from __future__ import annotations

import datetime
import re
from typing import Any, Dict, List, Optional, Tuple
from zoneinfo import ZoneInfo

from app.vehicle.charging import local_minutes_to_utc, utc_minutes_to_local

ALL_DAYS = [1, 2, 3, 4, 5, 6, 7]
DEFAULT_TIME_UTC_MIN = 420            # what every car has before anybody touches it
MAX_DEPARTURES = 4                    # more than any car has shown; a body with a hundred entries is not a plan
_HHMM = re.compile(r"^([01]\d|2[0-3]):([0-5]\d)$")


class DepartureError(Exception):
    """The plan asked for is not valid. `reason` is stable for the interface."""

    def __init__(self, reason: str, message: str) -> None:
        super().__init__(message)
        self.reason, self.message = reason, message


def _int(value: Any, default: int = 0) -> int:
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return default


def _days(value: Any) -> List[int]:
    days = sorted({_int(d) for d in value if 1 <= _int(d) <= 7}) if isinstance(value, list) else []
    return days or list(ALL_DAYS)


def _entries(raw: Optional[Dict[str, Any]]) -> Optional[List[Dict[str, Any]]]:
    entries = (raw or {}).get("appointmentTravelSetVOS")
    return [e for e in entries if isinstance(e, dict)] if isinstance(entries, list) else None


def read_plan(raw: Optional[Dict[str, Any]], tz: ZoneInfo, now: Optional[datetime.datetime] = None) -> Dict[str, Any]:
    """The plan as the car has it, in local time. `known` is False when the cloud has not returned it for this car."""
    entries = _entries(raw)
    if entries is None:
        return {"known": False, "enabled": None, "departures": [], "timezone": str(tz)}
    master = _int((raw or {}).get("mainSwitcher")) == 1
    departures = []
    for e in entries[:MAX_DEPARTURES]:
        local = utc_minutes_to_local(_int(e.get("travelTime"), DEFAULT_TIME_UTC_MIN), tz, now)
        departures.append({"time": f"{local // 60:02d}:{local % 60:02d}", "days": _days(e.get("travelDate")),
                           # a departure only counts when its own switch and the master one are both on
                           "enabled": master and _int(e.get("switcher")) == 1})
    return {"known": True, "enabled": any(d["enabled"] for d in departures), "departures": departures, "timezone": str(tz)}


def build_order(wanted: List[Dict[str, Any]], raw: Optional[Dict[str, Any]], tz: ZoneInfo,
                now: Optional[datetime.datetime] = None) -> Tuple[str, Dict[str, Any]]:
    """The order (catalogue key, params) that leaves the car with `wanted`: one {time "HH:MM", enabled} per departure the car
    has, in its order (one, if the car has none yet). The days of each departure stay as the car has them."""
    current = _entries(raw) or []
    expected = max(1, min(len(current), MAX_DEPARTURES))
    if not isinstance(wanted, list) or len(wanted) != expected:
        raise DepartureError("plan_invalid", f"Este coche tiene {expected} salida(s) programada(s): envía una hora para cada una.")
    entries = []
    for i, item in enumerate(wanted):
        match = _HHMM.match(str(item.get("time") or "").strip())
        if not match:
            raise DepartureError("time_invalid", "La hora de salida no es válida (usa HH:MM).")
        local = int(match.group(1)) * 60 + int(match.group(2))
        days = _days(current[i].get("travelDate")) if i < len(current) else list(ALL_DAYS)
        entries.append({"switcher": 1 if item.get("enabled") else 0, "travelTime": local_minutes_to_utc(local, tz, now),
                        "travelWeekday": days})
    master = 1 if any(e["switcher"] for e in entries) else 0
    return ("salida_prog_on" if master else "salida_prog_off"), {"mainSwitcher": master, "appointmentTravelAOS": entries}
