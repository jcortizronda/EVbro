"""Privacy consent on registration (`app_users.privacy_accepted_at`) and the administrator's action log (`admin_audit`). Idempotent.

Revision ID: 0009_consent_audit
Revises: 0008_catalog_fitments
Create Date: 2026-10-30
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy import inspect

revision = "0009_consent_audit"
down_revision = "0008_catalog_fitments"
branch_labels = None
depends_on = None


def upgrade() -> None:
    insp = inspect(op.get_bind())
    if not any(c["name"] == "privacy_accepted_at" for c in insp.get_columns("app_users")):
        op.add_column("app_users", sa.Column("privacy_accepted_at", sa.DateTime(), nullable=True))
    if "admin_audit" not in set(insp.get_table_names()):
        op.create_table(
            "admin_audit",
            sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
            sa.Column("admin_id", sa.Integer(), nullable=True, index=True),
            sa.Column("username", sa.String(64), nullable=False),
            sa.Column("action", sa.String(60), nullable=False, index=True),
            sa.Column("target", sa.String(120), nullable=True),
            sa.Column("detail", sa.String(255), nullable=True),
            sa.Column("ip", sa.String(64), nullable=True),
            sa.Column("created_at", sa.DateTime(), nullable=False, index=True),
        )


def downgrade() -> None:
    op.drop_table("admin_audit")
    op.drop_column("app_users", "privacy_accepted_at")
