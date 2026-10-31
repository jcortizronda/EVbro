"""Timetabled pre-conditioning: which schedules are due now, and the bookkeeping that keeps each to one send per day.

The poller calls `run_due` every loop (10 s). A schedule is due when, in the server's time zone, today's weekday is in `days`,
the current time is at or past `time_local` by less than `GRACE_MINUTES` (a restart at 07:59:55 still fires the 08:00 one),
and it has not already run today. Sending wakes the car: that is the point, and why it is opt-in per schedule.
"""
from __future__ import annotations

import datetime
import logging
from typing import Awaitable, Callable, Dict, List, Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_timezone
from app.db.models import ClimateSchedule, Vehicle

_LOGGER = logging.getLogger(__name__)
GRACE_MINUTES = 10
TEMPERATURE_RANGE = (16.0, 30.0)


def parse_days(text: str) -> List[int]:
    out = []
    for part in (text or "").split(","):
        part = part.strip()
        if part.isdigit() and 0 <= int(part) <= 6 and int(part) not in out:
            out.append(int(part))
    return sorted(out)


def is_due(schedule: ClimateSchedule, now_local: datetime.datetime) -> bool:
    if not schedule.enabled or now_local.weekday() not in parse_days(schedule.days):
        return False
    if schedule.last_run_on == now_local.date():
        return False
    try:
        hh, mm = (int(x) for x in schedule.time_local.split(":"))
    except ValueError:
        return False
    start = now_local.replace(hour=hh, minute=mm, second=0, microsecond=0)
    return start <= now_local < start + datetime.timedelta(minutes=GRACE_MINUTES)


def serialize(s: ClimateSchedule) -> Dict:
    return {"id": s.id, "vin": s.vin, "time_local": s.time_local, "days": parse_days(s.days), "temperature": s.temperature,
            "enabled": s.enabled, "label": s.label, "last_run_on": s.last_run_on.isoformat() if s.last_run_on else None,
            "last_result": s.last_result}


async def run_due(session: AsyncSession, send: Callable[[str, float], Awaitable[Dict]],
                  now_local: Optional[datetime.datetime] = None) -> int:
    """Send `clima_on` for every due schedule of a connected car. `send(vin, temperature)` returns the command result."""
    now_local = now_local or datetime.datetime.now(get_timezone())
    rows = (await session.execute(
        select(ClimateSchedule).join(Vehicle, Vehicle.vin == ClimateSchedule.vin)
        .where(ClimateSchedule.enabled.is_(True), Vehicle.is_manual.is_(False)))).scalars().all()
    sent = 0
    for s in rows:
        if not is_due(s, now_local):
            continue
        s.last_run_on = now_local.date()                 # mark BEFORE sending: a failure is not retried every 10 s
        await session.commit()
        try:
            result = await send(s.vin, s.temperature)
            s.last_result = (("OK · " if result.get("success") else "Fallo · ") + str(result.get("message") or ""))[:255]
        except Exception as ex:                           # noqa: BLE001 — recorded on the schedule, never stops the loop
            _LOGGER.warning("Climate schedule %s failed: %s", s.id, ex)
            s.last_result = f"Fallo · {ex}"[:255]
        await session.commit()
        sent += 1
    return sent
