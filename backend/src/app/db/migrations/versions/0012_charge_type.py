"""AC or DC per charging session (`charging_sessions.charge_type`). Idempotent.

Revision ID: 0012_charge_type
Revises: 0011_pin_policy
Create Date: 2026-10-30
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy import inspect

revision = "0012_charge_type"
down_revision = "0011_pin_policy"
branch_labels = None
depends_on = None


def upgrade() -> None:
    insp = inspect(op.get_bind())
    if not any(c["name"] == "charge_type" for c in insp.get_columns("charging_sessions")):
        op.add_column("charging_sessions", sa.Column("charge_type", sa.String(4), nullable=True))


def downgrade() -> None:
    op.drop_column("charging_sessions", "charge_type")
