"""Shared catalogue: the car's brand next to the model. Idempotent like the previous revisions.

Revision ID: 0006_catalog_make
Revises: 0005_catalog_entries
Create Date: 2026-10-30
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy import inspect

revision = "0006_catalog_make"
down_revision = "0005_catalog_entries"
branch_labels = None
depends_on = None


def upgrade() -> None:
    if not any(c["name"] == "make" for c in inspect(op.get_bind()).get_columns("catalog_entries")):
        op.add_column("catalog_entries", sa.Column("make", sa.String(50), nullable=True))


def downgrade() -> None:
    op.drop_column("catalog_entries", "make")
