"""Command audit: what the car answered (`command_audit.car_result`, `car_result_at`). Idempotent.

The audit only kept the cloud accepting the order (A00079). The car's own confirmation, which arrives later over its MQTT
channel, went to the web and was lost: on 17-11-2026 nobody could tell what a car had answered to two «Cerrar ventanillas» that
left a window reading ajar.

Revision ID: 0032_command_car_result
Revises: 0031_alert_states
Create Date: 2026-11-18
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy import inspect

revision = "0032_command_car_result"
down_revision = "0031_alert_states"
branch_labels = None
depends_on = None


def upgrade() -> None:
    cols = {c["name"] for c in inspect(op.get_bind()).get_columns("command_audit")}
    if "car_result" not in cols:
        op.add_column("command_audit", sa.Column("car_result", sa.Text(), nullable=True))
    if "car_result_at" not in cols:
        op.add_column("command_audit", sa.Column("car_result_at", sa.DateTime(), nullable=True))


def downgrade() -> None:
    op.drop_column("command_audit", "car_result_at")
    op.drop_column("command_audit", "car_result")
