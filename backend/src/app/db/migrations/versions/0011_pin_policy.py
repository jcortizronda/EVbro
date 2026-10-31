"""When orders ask for the PIN, per manager (`app_users.pin_policy`: open | always | never). Idempotent.

Revision ID: 0011_pin_policy
Revises: 0010_catalog_online
Create Date: 2026-10-30
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy import inspect

revision = "0011_pin_policy"
down_revision = "0010_catalog_online"
branch_labels = None
depends_on = None


def upgrade() -> None:
    insp = inspect(op.get_bind())
    if not any(c["name"] == "pin_policy" for c in insp.get_columns("app_users")):
        op.add_column("app_users", sa.Column("pin_policy", sa.String(10), nullable=False, server_default="open"))


def downgrade() -> None:
    op.drop_column("app_users", "pin_policy")
