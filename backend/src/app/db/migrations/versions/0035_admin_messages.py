"""Messages from the administrator to the manager accounts: `admin_messages` and the switch that silences them. Idempotent.

`notification_configs.notify_admin_messages` off means SILENCED: the message is still kept in the account's notice centre, but it
is not sent through its channels.

Revision ID: 0035_admin_messages
Revises: 0034_refuel_liters_entered
Create Date: 2026-11-22
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy import inspect

revision = "0035_admin_messages"
down_revision = "0034_refuel_liters_entered"
branch_labels = None
depends_on = None


def upgrade() -> None:
    insp = inspect(op.get_bind())
    if "notify_admin_messages" not in {c["name"] for c in insp.get_columns("notification_configs")}:
        op.add_column("notification_configs", sa.Column("notify_admin_messages", sa.Boolean(), nullable=True, server_default=sa.true()))
    if "admin_messages" not in insp.get_table_names():
        op.create_table(
            "admin_messages",
            sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
            sa.Column("created_at", sa.DateTime(), nullable=False),
            sa.Column("admin_id", sa.Integer(), nullable=True),
            sa.Column("username", sa.String(64), nullable=False),
            sa.Column("title", sa.String(120), nullable=False),
            sa.Column("body", sa.Text(), nullable=False),
            sa.Column("audience", sa.String(20), nullable=False),
            sa.Column("targets", sa.Text(), nullable=True),
            sa.Column("include_members", sa.Boolean(), nullable=False, server_default=sa.false()),
            sa.Column("recipients", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("pushed", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("silenced", sa.Integer(), nullable=False, server_default="0"),
        )
        op.create_index("ix_admin_messages_created_at", "admin_messages", ["created_at"])


def downgrade() -> None:
    op.drop_index("ix_admin_messages_created_at", table_name="admin_messages")
    op.drop_table("admin_messages")
    op.drop_column("notification_configs", "notify_admin_messages")
