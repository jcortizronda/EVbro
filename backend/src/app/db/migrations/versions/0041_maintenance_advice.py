"""EVbro's maintenance recommendations: `maintenance_advice`, what the owner answered to each one for a car
(services/maintenance_advice.py). Idempotent.

Revision ID: 0041_maintenance_advice
Revises: 0040_refuel_import
Create Date: 2026-11-25
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy import inspect

revision = "0041_maintenance_advice"
down_revision = "0040_refuel_import"
branch_labels = None
depends_on = None


def upgrade() -> None:
    if "maintenance_advice" in inspect(op.get_bind()).get_table_names():
        return                                    # a database built from the models already has it, and there is nothing to fill in
    op.create_table(
        "maintenance_advice",
        sa.Column("vin", sa.String(32), sa.ForeignKey("vehicles.vin", ondelete="CASCADE"), primary_key=True),
        sa.Column("key", sa.String(30), primary_key=True),
        sa.Column("checked_at", sa.DateTime(), nullable=True),
        sa.Column("checked_odometer", sa.Float(), nullable=True),
        sa.Column("snooze_until", sa.DateTime(), nullable=True),
        sa.Column("snooze_odometer", sa.Float(), nullable=True),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.true()),
    )


def downgrade() -> None:
    op.drop_table("maintenance_advice")
