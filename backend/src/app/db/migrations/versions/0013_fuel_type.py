"""Fuel type per car (`vehicles.fuel_type`: gasoline | diesel | phev | ev; existing cars stay phev). Idempotent.

Revision ID: 0013_fuel_type
Revises: 0012_charge_type
Create Date: 2026-10-30
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy import inspect

revision = "0013_fuel_type"
down_revision = "0012_charge_type"
branch_labels = None
depends_on = None


def upgrade() -> None:
    insp = inspect(op.get_bind())
    if not any(c["name"] == "fuel_type" for c in insp.get_columns("vehicles")):
        op.add_column("vehicles", sa.Column("fuel_type", sa.String(10), nullable=False, server_default="phev"))


def downgrade() -> None:
    op.drop_column("vehicles", "fuel_type")
