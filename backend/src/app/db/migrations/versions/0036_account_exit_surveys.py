"""Why accounts are deleted: `account_exit_surveys`, the optional answers a manager leaves when deleting its account. Idempotent.

Revision ID: 0036_account_exit_surveys
Revises: 0035_admin_messages
Create Date: 2026-11-22
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy import inspect

revision = "0036_account_exit_surveys"
down_revision = "0035_admin_messages"
branch_labels = None
depends_on = None


def upgrade() -> None:
    if "account_exit_surveys" in inspect(op.get_bind()).get_table_names():
        return                                    # a database built from the models already has it, and there is nothing to fill in
    op.create_table(
        "account_exit_surveys",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("username", sa.String(64), nullable=True),
        sa.Column("reasons", sa.JSON(), nullable=False),
        sa.Column("comment", sa.Text(), nullable=True),
        sa.Column("account_age_days", sa.Integer(), nullable=True),
        sa.Column("vehicles", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("members", sa.Integer(), nullable=False, server_default="0"),
    )
    op.create_index("ix_account_exit_surveys_created_at", "account_exit_surveys", ["created_at"])


def downgrade() -> None:
    op.drop_index("ix_account_exit_surveys_created_at", table_name="account_exit_surveys")
    op.drop_table("account_exit_surveys")
