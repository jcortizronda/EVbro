"""Drives: the tank points put in at a pump stop during the trip (`drives.fuel_refuelled_pct`). Idempotent.

A trip with a refuel in the middle ends with more fuel than it started with; the litres spent are the points consumed before and
after the stop, and the detail says how much went in (05-11-2026: 9 % → 21 % with 38 points put in on the road).

Revision ID: 0025_drive_refuelled
Revises: 0024_drive_fuel_litres
Create Date: 2026-11-05
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy import inspect

revision = "0025_drive_refuelled"
down_revision = "0024_drive_fuel_litres"
branch_labels = None
depends_on = None


def upgrade() -> None:
    cols = {c["name"] for c in inspect(op.get_bind()).get_columns("drives")}
    if "fuel_refuelled_pct" not in cols:
        op.add_column("drives", sa.Column("fuel_refuelled_pct", sa.Float(), nullable=True, server_default="0"))


def downgrade() -> None:
    op.drop_column("drives", "fuel_refuelled_pct")
