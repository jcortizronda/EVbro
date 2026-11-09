"""Vehicles: the raw keys the cloud has ever sent for each car (`vehicles.reported_keys`). Idempotent.

Not every model reports the same things (08-11-2026, five cars of three models on one server: the S900 never sends the cabin
temperature or the rear window heater state, the S800 never sends a position, only the S700 of 2026 sends a charge cut-off SOC).
The interface and the Home Assistant discovery read this list to show each car only what it can report.

Revision ID: 0026_vehicle_reported_keys
Revises: 0025_drive_refuelled
Create Date: 2026-11-08
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy import inspect

revision = "0026_vehicle_reported_keys"
down_revision = "0025_drive_refuelled"
branch_labels = None
depends_on = None


def upgrade() -> None:
    cols = {c["name"] for c in inspect(op.get_bind()).get_columns("vehicles")}
    if "reported_keys" not in cols:
        op.add_column("vehicles", sa.Column("reported_keys", sa.JSON(), nullable=True))


def downgrade() -> None:
    op.drop_column("vehicles", "reported_keys")
