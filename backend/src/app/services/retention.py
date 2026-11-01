"""How long the history is kept. Three independent periods (0 = forever):

  * **drives** (and the points of their route),
  * **charges** (and their power curve),
  * **raw telemetry** (the car's full readings: the one that grows most and is least useful over the years).

Applied once a day (poller) and on demand from Settings. Only drives and charges already CLOSED are deleted.
"""
from __future__ import annotations

import datetime
from typing import Any, Dict, Optional

from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import (
    AppSetting,
    ChargePoint,
    ChargingSession,
    Drive,
    DrivePosition,
    GpsPosition,
    TelemetrySnapshot,
)

KEYS = {"trips_days": "retention.trips_days", "charges_days": "retention.charges_days", "snapshots_days": "retention.snapshots_days"}
DEFAULTS = {"trips_days": 0, "charges_days": 0, "snapshots_days": 365}
MAX_DAYS = 3650


async def get_config(session: AsyncSession) -> Dict[str, int]:
    rows = {r.key: r.value for r in (await session.execute(select(AppSetting).where(AppSetting.key.in_(KEYS.values())))).scalars()}
    out = {}
    for name, key in KEYS.items():
        try:
            out[name] = int(rows[key]) if key in rows else DEFAULTS[name]
        except (TypeError, ValueError):
            out[name] = DEFAULTS[name]
    return out


async def set_config(session: AsyncSession, values: Dict[str, Optional[int]]) -> Dict[str, int]:
    for name, days in values.items():
        if name not in KEYS or days is None:
            continue
        row = await session.get(AppSetting, KEYS[name])
        if row is None:
            session.add(AppSetting(key=KEYS[name], value=str(int(days))))
        else:
            row.value = str(int(days))
    await session.commit()
    return await get_config(session)


async def counts(session: AsyncSession) -> Dict[str, Any]:
    """How much is stored now (shown next to the periods)."""
    async def one(model, ts_col):
        n, oldest = (await session.execute(select(func.count(), func.min(ts_col)).select_from(model))).one()
        return {"count": n, "oldest": oldest.isoformat() if oldest else None}
    return {
        "trips": await one(Drive, Drive.start_time),
        "charges": await one(ChargingSession, ChargingSession.start_time),
        "snapshots": await one(TelemetrySnapshot, TelemetrySnapshot.timestamp),
    }


async def apply(session: AsyncSession, now: Optional[datetime.datetime] = None) -> Dict[str, int]:
    """Deletes whatever is older than each kind's period. Returns how many rows of each kind were deleted."""
    now = now or datetime.datetime.utcnow()
    cfg = await get_config(session)
    deleted = {"trips": 0, "charges": 0, "snapshots": 0}

    if cfg["trips_days"] > 0:
        old = select(Drive.id).where(Drive.end_time.is_not(None), Drive.start_time < now - datetime.timedelta(days=cfg["trips_days"]))
        await session.execute(delete(DrivePosition).where(DrivePosition.drive_id.in_(old)))
        deleted["trips"] = (await session.execute(delete(Drive).where(Drive.id.in_(old)))).rowcount or 0

    if cfg["charges_days"] > 0:
        old = select(ChargingSession.id).where(
            ChargingSession.end_time.is_not(None), ChargingSession.start_time < now - datetime.timedelta(days=cfg["charges_days"]))
        await session.execute(delete(ChargePoint).where(ChargePoint.charging_session_id.in_(old)))
        deleted["charges"] = (await session.execute(delete(ChargingSession).where(ChargingSession.id.in_(old)))).rowcount or 0

    if cfg["snapshots_days"] > 0:
        deleted["snapshots"] = (await session.execute(
            delete(TelemetrySnapshot).where(TelemetrySnapshot.timestamp < now - datetime.timedelta(days=cfg["snapshots_days"]))
        )).rowcount or 0
        # GPS fixes from Traccar are raw telemetry too (the drive keeps its own copy of the track)
        deleted["snapshots"] += (await session.execute(
            delete(GpsPosition).where(GpsPosition.timestamp < now - datetime.timedelta(days=cfg["snapshots_days"]))
        )).rowcount or 0

    await session.commit()
    return deleted
