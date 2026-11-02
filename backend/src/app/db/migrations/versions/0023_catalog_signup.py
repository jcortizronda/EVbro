"""Online catalogue sign-up: keys an installation obtained by itself with one click (services/catalog_remote.py). Idempotent.

`catalog_keys.auto` marks those keys: they have a lower daily proposal cap than the ones the administrator hands out.

Revision ID: 0023_catalog_signup
Revises: 0022_telemetry
Create Date: 2026-11-02
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy import inspect

revision = "0023_catalog_signup"
down_revision = "0022_telemetry"
branch_labels = None
depends_on = None


def upgrade() -> None:
    cols = {c["name"] for c in inspect(op.get_bind()).get_columns("catalog_keys")}
    if "auto" not in cols:
        op.add_column("catalog_keys", sa.Column("auto", sa.Boolean(), nullable=False, server_default=sa.false()))


def downgrade() -> None:
    op.drop_column("catalog_keys", "auto")
