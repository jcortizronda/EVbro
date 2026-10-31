"""Phone GPS trackers (OwnTracks / Traccar Client) and their positions. Idempotent like 0002 and 0003.

Revision ID: 0004_phone_trackers
Revises: 0003_geofence_climate
Create Date: 2026-10-30
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy import inspect

revision = "0004_phone_trackers"
down_revision = "0003_geofence_climate"
branch_labels = None
depends_on = None


def upgrade() -> None:
    existing = set(inspect(op.get_bind()).get_table_names())
    if "phone_trackers" not in existing:
        op.create_table(
            "phone_trackers",
            sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
            sa.Column("vin", sa.String(32), sa.ForeignKey("vehicles.vin", ondelete="CASCADE"), nullable=False, index=True),
            sa.Column("user_id", sa.Integer(), sa.ForeignKey("app_users.id", ondelete="CASCADE"), nullable=True, index=True),
            sa.Column("name", sa.String(60), nullable=False),
            sa.Column("token_hash", sa.String(64), nullable=False, unique=True, index=True),
            sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.true()),
            sa.Column("created_at", sa.DateTime(), nullable=True),
            sa.Column("last_seen_at", sa.DateTime(), nullable=True),
            sa.Column("last_latitude", sa.Float(), nullable=True),
            sa.Column("last_longitude", sa.Float(), nullable=True),
            sa.Column("last_speed_kmh", sa.Float(), nullable=True),
            sa.Column("last_accuracy_m", sa.Float(), nullable=True),
            sa.Column("last_battery_pct", sa.Float(), nullable=True),
            sa.Column("positions_count", sa.Integer(), nullable=False, server_default="0"),
        )
    if "phone_positions" not in existing:
        op.create_table(
            "phone_positions",
            sa.Column("id", sa.BigInteger().with_variant(sa.Integer(), "sqlite"), primary_key=True, autoincrement=True),
            sa.Column("tracker_id", sa.Integer(), sa.ForeignKey("phone_trackers.id", ondelete="CASCADE"), nullable=False, index=True),
            sa.Column("vin", sa.String(32), sa.ForeignKey("vehicles.vin", ondelete="CASCADE"), nullable=False, index=True),
            sa.Column("timestamp", sa.DateTime(), nullable=False, index=True),
            sa.Column("latitude", sa.Float(), nullable=False),
            sa.Column("longitude", sa.Float(), nullable=False),
            sa.Column("altitude", sa.Float(), nullable=True),
            sa.Column("speed_kmh", sa.Float(), nullable=True),
            sa.Column("heading", sa.Float(), nullable=True),
            sa.Column("accuracy_m", sa.Float(), nullable=True),
            sa.Column("battery_pct", sa.Float(), nullable=True),
        )


def downgrade() -> None:
    op.drop_table("phone_positions")
    op.drop_table("phone_trackers")
