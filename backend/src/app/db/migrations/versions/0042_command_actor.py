"""Command audit: who sent the order (`command_audit.actor`). Idempotent.

The audit kept the IP only. On a car shared with member accounts its owner could not tell whether an order had been sent by them
or by a member (security review of 26-11-2026).

Revision ID: 0042_command_actor
Revises: 0041_maintenance_advice
Create Date: 2026-11-26
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy import inspect

revision = "0042_command_actor"
down_revision = "0041_maintenance_advice"
branch_labels = None
depends_on = None


def upgrade() -> None:
    cols = {c["name"] for c in inspect(op.get_bind()).get_columns("command_audit")}
    if "actor" not in cols:
        op.add_column("command_audit", sa.Column("actor", sa.String(length=150), nullable=True))


def downgrade() -> None:
    op.drop_column("command_audit", "actor")
