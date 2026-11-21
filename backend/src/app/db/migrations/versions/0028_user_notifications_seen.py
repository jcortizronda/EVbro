"""Accounts: when each one last opened its notification centre (`app_users.notifications_seen_at`). Idempotent.

The bell of the header lights up when there are notices newer than that. Existing accounts start with everything already seen,
so the old history does not show up as new the day this arrives.

Revision ID: 0028_user_notifications_seen
Revises: 0027_vehicle_rejected_commands
Create Date: 2026-11-09
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy import inspect

revision = "0028_user_notifications_seen"
down_revision = "0027_vehicle_rejected_commands"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    cols = {c["name"] for c in inspect(bind).get_columns("app_users")}
    if "notifications_seen_at" not in cols:
        op.add_column("app_users", sa.Column("notifications_seen_at", sa.DateTime(), nullable=True))
    # Naive UTC, like every other timestamp of the database
    now = "(now() AT TIME ZONE 'utc')" if bind.dialect.name == "postgresql" else "CURRENT_TIMESTAMP"
    op.execute(f"UPDATE app_users SET notifications_seen_at = {now} WHERE notifications_seen_at IS NULL")


def downgrade() -> None:
    op.drop_column("app_users", "notifications_seen_at")
