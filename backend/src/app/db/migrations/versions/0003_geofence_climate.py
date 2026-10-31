"""History of geofence entries/exits and timetabled pre-conditioning.

Idempotent like 0002: the baseline builds the schema from the current models, so on an empty database both tables already exist.

Revision ID: 0003_geofence_climate
Revises: 0002_refuel_full_tank
Create Date: 2026-10-30
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy import inspect

revision = "0003_geofence_climate"
down_revision = "0002_refuel_full_tank"
branch_labels = None
depends_on = None


def upgrade() -> None:
    existing = set(inspect(op.get_bind()).get_table_names())
    if "geofence_events" not in existing:
        op.create_table(
            "geofence_events",
            sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
            sa.Column("vin", sa.String(32), sa.ForeignKey("vehicles.vin", ondelete="CASCADE"), nullable=False, index=True),
            sa.Column("geofence_id", sa.Integer(), sa.ForeignKey("geofences.id", ondelete="SET NULL"), nullable=True, index=True),
            sa.Column("geofence_name", sa.String(100), nullable=False),
            sa.Column("kind", sa.String(8), nullable=False),
            sa.Column("timestamp", sa.DateTime(), nullable=False, index=True),
            sa.Column("latitude", sa.Float(), nullable=True),
            sa.Column("longitude", sa.Float(), nullable=True),
            sa.Column("battery_pct", sa.Float(), nullable=True),
            sa.Column("odometer_km", sa.Float(), nullable=True),
        )
    if "climate_schedules" not in existing:
        op.create_table(
            "climate_schedules",
            sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
            sa.Column("vin", sa.String(32), sa.ForeignKey("vehicles.vin", ondelete="CASCADE"), nullable=False, index=True),
            sa.Column("time_local", sa.String(5), nullable=False),
            sa.Column("days", sa.String(20), nullable=False, server_default="0,1,2,3,4"),
            sa.Column("temperature", sa.Float(), nullable=False, server_default="22"),
            sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.true()),
            sa.Column("label", sa.String(60), nullable=True),
            sa.Column("last_run_on", sa.Date(), nullable=True),
            sa.Column("last_result", sa.String(255), nullable=True),
            sa.Column("created_at", sa.DateTime(), nullable=True),
        )


def downgrade() -> None:
    op.drop_table("climate_schedules")
    op.drop_table("geofence_events")
