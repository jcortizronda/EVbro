"""Catalogue entries fit several makes and models: `catalog_fitments`, seeded from each entry's own make/model/years. Idempotent.

Revision ID: 0008_catalog_fitments
Revises: 0007_traccar
Create Date: 2026-10-30
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy import inspect

revision = "0008_catalog_fitments"
down_revision = "0007_traccar"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    if "catalog_fitments" not in set(inspect(bind).get_table_names()):
        op.create_table(
            "catalog_fitments",
            sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
            sa.Column("entry_id", sa.Integer(), sa.ForeignKey("catalog_entries.id", ondelete="CASCADE"), nullable=False, index=True),
            sa.Column("make", sa.String(50), nullable=True),
            sa.Column("model", sa.String(80), nullable=False, index=True),
            sa.Column("years", sa.String(20), nullable=True),
        )
    bind.execute(sa.text(
        "INSERT INTO catalog_fitments (entry_id, make, model, years) "
        "SELECT e.id, e.make, e.model, e.years FROM catalog_entries e "
        "WHERE NOT EXISTS (SELECT 1 FROM catalog_fitments f WHERE f.entry_id = e.id)"))


def downgrade() -> None:
    op.drop_table("catalog_fitments")
