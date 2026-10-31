"""The language of each account (`app_users.language`: es | en | pt), chosen in Ajustes → Sistema. Idempotent.

Revision ID: 0018_user_language
Revises: 0017_energy_recalc
Create Date: 2026-10-31
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy import inspect

revision = "0018_user_language"
down_revision = "0017_energy_recalc"
branch_labels = None
depends_on = None


def upgrade() -> None:
    insp = inspect(op.get_bind())
    if not any(c["name"] == "language" for c in insp.get_columns("app_users")):
        op.add_column("app_users", sa.Column("language", sa.String(5), nullable=False, server_default="es"))


def downgrade() -> None:
    op.drop_column("app_users", "language")
