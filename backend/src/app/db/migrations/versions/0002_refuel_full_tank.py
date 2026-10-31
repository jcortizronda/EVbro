"""Refuels: whether the tank was filled up (`full_tank`), needed to measure real consumption between full tanks.

NULL means "not stated": a detected refuel then counts as full when the gauge ended at 95 % or more (see services/refuel_stats.py).

Idempotent on purpose: the baseline builds the schema from the CURRENT models (`create_all`), so on an empty database this column
already exists when this revision runs; it is only added to databases that were at 0001 before the column existed.

Revision ID: 0002_refuel_full_tank
Revises: 0001_baseline
Create Date: 2026-10-30
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy import inspect

revision = "0002_refuel_full_tank"
down_revision = "0001_baseline"
branch_labels = None
depends_on = None


def _has_column(table: str, column: str) -> bool:
    return any(c["name"] == column for c in inspect(op.get_bind()).get_columns(table))


def upgrade() -> None:
    if not _has_column("refuels", "full_tank"):
        op.add_column("refuels", sa.Column("full_tank", sa.Boolean(), nullable=True))


def downgrade() -> None:
    op.drop_column("refuels", "full_tank")
