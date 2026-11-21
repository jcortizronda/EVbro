"""Drives: the EV odometer of each end taken from the right reading. Idempotent.

0029 took, for the end of a past trip, the FIRST reading with the trip's final total odometer from ten minutes before the end,
which could still be a reading on the move (a trip ending at 14:31 took 14:29, before the EV counter's last tick). The ends
are now the last reading at or before the start and the first one at or after the end, both with the trip's total odometer.
Trips without such a reading keep what they have.

Revision ID: 0030_drive_ev_odometer_ends
Revises: 0029_drive_ev_odometer
Create Date: 2026-11-12
"""
from __future__ import annotations

import datetime
import json
from typing import Any, Optional

import sqlalchemy as sa
from alembic import op

revision = "0030_drive_ev_odometer_ends"
down_revision = "0029_drive_ev_odometer"
branch_labels = None
depends_on = None

_MATCH_WINDOW = datetime.timedelta(hours=2)


def _ev_odometer(raw: Any) -> Optional[float]:
    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except ValueError:
            return None
    if not isinstance(raw, dict):
        return None
    try:
        value = float(raw.get("electricRange"))
    except (TypeError, ValueError):
        return None
    return value if value > 0 else None


def _as_datetime(value: Any) -> datetime.datetime:
    return value if isinstance(value, datetime.datetime) else datetime.datetime.fromisoformat(str(value))


def upgrade() -> None:
    bind = op.get_bind()
    as_param = (lambda dt: dt.strftime("%Y-%m-%d %H:%M:%S")) if bind.dialect.name == "sqlite" else (lambda dt: dt)
    drives = bind.execute(sa.text(
        "SELECT id, vin, start_time, end_time, start_odometer, end_odometer, distance_km FROM drives "
        "WHERE end_time IS NOT NULL AND start_odometer IS NOT NULL AND end_odometer IS NOT NULL")).fetchall()
    before = sa.text("SELECT raw_data FROM telemetry_snapshots WHERE vin = :vin AND odometer_km = :odo "
                     "AND timestamp <= :at AND timestamp >= :since ORDER BY timestamp DESC LIMIT 1")
    after = sa.text("SELECT raw_data FROM telemetry_snapshots WHERE vin = :vin AND odometer_km = :odo "
                    "AND timestamp >= :at AND timestamp <= :until ORDER BY timestamp ASC LIMIT 1")
    for d in drives:
        start_time, end_time = _as_datetime(d.start_time), _as_datetime(d.end_time)
        start = _ev_odometer(bind.execute(before, {"vin": d.vin, "odo": d.start_odometer, "at": as_param(start_time),
                                                   "since": as_param(start_time - _MATCH_WINDOW)}).scalar())
        end = _ev_odometer(bind.execute(after, {"vin": d.vin, "odo": d.end_odometer, "at": as_param(end_time),
                                                "until": as_param(end_time + _MATCH_WINDOW)}).scalar())
        if start is None or end is None or end < start or end - start > (d.distance_km or 0) + 1:
            continue
        bind.execute(sa.text("UPDATE drives SET start_ev_odometer = :s, end_ev_odometer = :e WHERE id = :id"),
                     {"s": start, "e": end, "id": d.id})


def downgrade() -> None:
    pass  # the previous pairs are not kept
