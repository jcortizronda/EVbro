"""Alerts already sent about a situation still going on (`alert_states`): each one is sent once per episode. Idempotent.

Up to now the poller remembered in memory when it last sent each alert and repeated it on a timer (open windows and unlocked
car every 30 minutes, low fuel every 2 hours, faults every hour, tyres every 12, services every day). One row per car and
situation replaces those timers and survives a restart.

Revision ID: 0031_alert_states
Revises: 0030_drive_ev_odometer_ends
Create Date: 2026-11-18
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy import inspect

revision = "0031_alert_states"
down_revision = "0030_drive_ev_odometer_ends"
branch_labels = None
depends_on = None


def upgrade() -> None:
    if "alert_states" in set(inspect(op.get_bind()).get_table_names()):
        return
    op.create_table(
        "alert_states",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("vin", sa.String(32), sa.ForeignKey("vehicles.vin", ondelete="CASCADE"), nullable=False),
        sa.Column("key", sa.String(160), nullable=False),
        sa.Column("sent_at", sa.DateTime(), nullable=False),
        sa.UniqueConstraint("vin", "key", name="uq_alert_states_vin_key"),
    )
    op.create_index("ix_alert_states_vin", "alert_states", ["vin"])


def downgrade() -> None:
    op.drop_index("ix_alert_states_vin", table_name="alert_states")
    op.drop_table("alert_states")
