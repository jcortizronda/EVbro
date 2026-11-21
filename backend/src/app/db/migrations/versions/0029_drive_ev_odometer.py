"""Drives: the car's electric-only odometer at the start and the end (`drives.start_ev_odometer`, `drives.end_ev_odometer`). Idempotent.

The car counts apart the kilometres driven with the petrol engine off (`electricRange` in its readings, «Cuentakilómetros EV»).
The difference between both ends says how many kilometres of each trip were electric. Past trips are filled from the stored
readings (`telemetry_snapshots.raw_data`, kept one year by default): the reading at the start and at the end whose total odometer
matches the trip's. A trip without a matching reading, or with an implausible result, stays unknown (NULL).

Revision ID: 0029_drive_ev_odometer
Revises: 0028_user_notifications_seen
Create Date: 2026-11-10
"""
from __future__ import annotations

import datetime
import json
from typing import Any, Optional

import sqlalchemy as sa
from alembic import op
from sqlalchemy import inspect

revision = "0029_drive_ev_odometer"
down_revision = "0028_user_notifications_seen"
branch_labels = None
depends_on = None

_MATCH_WINDOW = datetime.timedelta(hours=2)    # the reading must be this close to the trip's start or end


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
    """SQLite hands timestamps back as text."""
    return value if isinstance(value, datetime.datetime) else datetime.datetime.fromisoformat(str(value))


def upgrade() -> None:
    bind = op.get_bind()
    # SQLite compares timestamps as text: the parameters must be written the way the rows are; PostgreSQL wants datetimes
    as_param = (lambda dt: dt.strftime("%Y-%m-%d %H:%M:%S")) if bind.dialect.name == "sqlite" else (lambda dt: dt)
    cols = {c["name"] for c in inspect(bind).get_columns("drives")}
    for name in ("start_ev_odometer", "end_ev_odometer"):
        if name not in cols:
            op.add_column("drives", sa.Column(name, sa.Float(), nullable=True))

    drives = bind.execute(sa.text(
        "SELECT id, vin, start_time, end_time, start_odometer, end_odometer, distance_km FROM drives "
        "WHERE end_time IS NOT NULL AND start_odometer IS NOT NULL AND end_odometer IS NOT NULL "
        "AND start_ev_odometer IS NULL AND end_ev_odometer IS NULL")).fetchall()
    before = sa.text("SELECT raw_data FROM telemetry_snapshots WHERE vin = :vin AND odometer_km = :odo "
                     "AND timestamp <= :at AND timestamp >= :since ORDER BY timestamp DESC LIMIT 1")
    after = sa.text("SELECT raw_data FROM telemetry_snapshots WHERE vin = :vin AND odometer_km = :odo "
                    "AND timestamp >= :at AND timestamp <= :until ORDER BY timestamp ASC LIMIT 1")
    for d in drives:
        start_time, end_time = _as_datetime(d.start_time), _as_datetime(d.end_time)
        start = bind.execute(before, {"vin": d.vin, "odo": d.start_odometer, "at": as_param(start_time + datetime.timedelta(minutes=10)),
                                      "since": as_param(start_time - _MATCH_WINDOW)}).scalar()
        end = bind.execute(after, {"vin": d.vin, "odo": d.end_odometer, "at": as_param(end_time - datetime.timedelta(minutes=10)),
                                   "until": as_param(end_time + _MATCH_WINDOW)}).scalar()
        ev_start, ev_end = _ev_odometer(start), _ev_odometer(end)
        if ev_start is None or ev_end is None:
            continue
        ev_km = ev_end - ev_start
        if ev_km < 0 or ev_km > (d.distance_km or 0) + 1:
            continue                                # the readings do not belong together
        bind.execute(sa.text("UPDATE drives SET start_ev_odometer = :s, end_ev_odometer = :e WHERE id = :id"),
                     {"s": ev_start, "e": ev_end, "id": d.id})


def downgrade() -> None:
    op.drop_column("drives", "end_ev_odometer")
    op.drop_column("drives", "start_ev_odometer")
