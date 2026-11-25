"""The account's optional e-mail (`app_users.email`) and the password-reset links sent to it (`password_resets`). Idempotent.

Revision ID: 0039_account_email
Revises: 0038_manual_charges
Create Date: 2026-11-25
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy import inspect

revision = "0039_account_email"
down_revision = "0038_manual_charges"
branch_labels = None
depends_on = None


def upgrade() -> None:
    insp = inspect(op.get_bind())
    if "email" not in {c["name"] for c in insp.get_columns("app_users")}:
        op.add_column("app_users", sa.Column("email", sa.String(254), nullable=True))
        op.create_index("ix_app_users_email", "app_users", ["email"])
    if not insp.has_table("password_resets"):
        op.create_table(
            "password_resets",
            sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
            sa.Column("user_id", sa.Integer(), sa.ForeignKey("app_users.id", ondelete="CASCADE"), nullable=False),
            sa.Column("token_hash", sa.String(64), nullable=False),
            sa.Column("created_at", sa.DateTime(), nullable=False),
            sa.Column("expires_at", sa.DateTime(), nullable=False),
            sa.Column("used_at", sa.DateTime(), nullable=True),
            sa.Column("ip", sa.String(64), nullable=True),
        )
        op.create_index("ix_password_resets_user_id", "password_resets", ["user_id"])
        op.create_index("ix_password_resets_token_hash", "password_resets", ["token_hash"], unique=True)


def downgrade() -> None:
    insp = inspect(op.get_bind())
    if insp.has_table("password_resets"):
        op.drop_table("password_resets")
    if "email" in {c["name"] for c in insp.get_columns("app_users")}:
        op.drop_index("ix_app_users_email", table_name="app_users")
        op.drop_column("app_users", "email")
