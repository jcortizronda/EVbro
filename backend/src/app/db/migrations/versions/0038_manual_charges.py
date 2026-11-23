"""Charges typed by the user or imported from a file: `charging_sessions.manual`, `date_unknown`, `battery_gain_pct` and
`import_batch` (services/manual_charges.py). Idempotent.

Revision ID: 0038_manual_charges
Revises: 0037_cloud_usage
Create Date: 2026-11-23
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy import inspect

revision = "0038_manual_charges"
down_revision = "0037_cloud_usage"
branch_labels = None
depends_on = None


def upgrade() -> None:
    insp = inspect(op.get_bind())
    have = {c["name"] for c in insp.get_columns("charging_sessions")}
    if "manual" not in have:
        op.add_column("charging_sessions", sa.Column("manual", sa.Boolean(), nullable=False, server_default=sa.false()))
    if "date_unknown" not in have:
        op.add_column("charging_sessions", sa.Column("date_unknown", sa.Boolean(), nullable=False, server_default=sa.false()))
    if "battery_gain_pct" not in have:
        op.add_column("charging_sessions", sa.Column("battery_gain_pct", sa.Float(), nullable=True))
    if "import_batch" not in have:
        op.add_column("charging_sessions", sa.Column("import_batch", sa.String(32), nullable=True))
    if "ix_charging_sessions_import_batch" not in {i["name"] for i in insp.get_indexes("charging_sessions")}:
        op.create_index("ix_charging_sessions_import_batch", "charging_sessions", ["import_batch"])


def downgrade() -> None:
    op.drop_index("ix_charging_sessions_import_batch", table_name="charging_sessions")
    for column in ("import_batch", "battery_gain_pct", "date_unknown", "manual"):
        op.drop_column("charging_sessions", column)
