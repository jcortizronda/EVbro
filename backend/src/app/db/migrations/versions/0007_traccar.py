"""Traccar Server links and GPS positions; the phone-tracking tables (never used in production) go away. Idempotent.

Revision ID: 0007_traccar
Revises: 0006_catalog_make
Create Date: 2026-10-30
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy import inspect

revision = "0007_traccar"
down_revision = "0006_catalog_make"
branch_labels = None
depends_on = None


def upgrade() -> None:
    existing = set(inspect(op.get_bind()).get_table_names())
    if "traccar_links" not in existing:
        op.create_table(
            "traccar_links",
            sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
            sa.Column("vin", sa.String(32), sa.ForeignKey("vehicles.vin", ondelete="CASCADE"), nullable=False, unique=True, index=True),
            sa.Column("host", sa.String(255), nullable=False),
            sa.Column("port", sa.Integer(), nullable=True),
            sa.Column("use_ssl", sa.Boolean(), nullable=False, server_default=sa.false()),
            sa.Column("verify_ssl", sa.Boolean(), nullable=False, server_default=sa.true()),
            sa.Column("api_token", sa.Text(), nullable=False),
            sa.Column("device_id", sa.Integer(), nullable=False),
            sa.Column("device_name", sa.String(120), nullable=True),
            sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.true()),
            sa.Column("build_trips", sa.Boolean(), nullable=False, server_default=sa.false()),
            sa.Column("stop_minutes", sa.Integer(), nullable=False, server_default="5"),
            sa.Column("created_at", sa.DateTime(), nullable=True),
            sa.Column("last_position_at", sa.DateTime(), nullable=True),
            sa.Column("last_latitude", sa.Float(), nullable=True),
            sa.Column("last_longitude", sa.Float(), nullable=True),
            sa.Column("last_error", sa.String(255), nullable=True),
        )
    if "gps_positions" not in existing:
        op.create_table(
            "gps_positions",
            sa.Column("id", sa.BigInteger().with_variant(sa.Integer(), "sqlite"), primary_key=True, autoincrement=True),
            sa.Column("vin", sa.String(32), sa.ForeignKey("vehicles.vin", ondelete="CASCADE"), nullable=False, index=True),
            sa.Column("timestamp", sa.DateTime(), nullable=False, index=True),
            sa.Column("latitude", sa.Float(), nullable=False),
            sa.Column("longitude", sa.Float(), nullable=False),
            sa.Column("altitude", sa.Float(), nullable=True),
            sa.Column("speed_kmh", sa.Float(), nullable=True),
            sa.Column("course", sa.Float(), nullable=True),
            sa.Column("accuracy_m", sa.Float(), nullable=True),
            sa.Column("ignition", sa.Boolean(), nullable=True),
            sa.Column("motion", sa.Boolean(), nullable=True),
            sa.Column("battery_pct", sa.Float(), nullable=True),
        )
    for table in ("phone_positions", "phone_trackers"):      # the phone GPS idea was replaced the same day; nothing was stored
        if table in existing:
            op.drop_table(table)


def downgrade() -> None:
    op.drop_table("gps_positions")
    op.drop_table("traccar_links")
