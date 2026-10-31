"""Shared catalogue of consumables with the administrator's approval queue. Idempotent like the previous revisions.

Revision ID: 0005_catalog_entries
Revises: 0004_phone_trackers
Create Date: 2026-10-30
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy import inspect

revision = "0005_catalog_entries"
down_revision = "0004_phone_trackers"
branch_labels = None
depends_on = None


def upgrade() -> None:
    if "catalog_entries" in set(inspect(op.get_bind()).get_table_names()):
        return
    op.create_table(
        "catalog_entries",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("model", sa.String(80), nullable=False, index=True),
        sa.Column("years", sa.String(20), nullable=True),
        sa.Column("category", sa.String(30), nullable=False, index=True),
        sa.Column("position", sa.String(10), nullable=True),
        sa.Column("name", sa.String(150), nullable=False),
        sa.Column("brand", sa.String(80), nullable=True),
        sa.Column("reference", sa.String(120), nullable=True),
        sa.Column("spec", sa.String(120), nullable=True),
        sa.Column("quantity", sa.Float(), nullable=True),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column("source", sa.String(30), nullable=False, server_default="manual"),
        sa.Column("status", sa.String(10), nullable=False, server_default="pending", index=True),
        sa.Column("reject_reason", sa.String(255), nullable=True),
        sa.Column("proposed_by", sa.Integer(), sa.ForeignKey("app_users.id", ondelete="SET NULL"), nullable=True, index=True),
        sa.Column("proposed_at", sa.DateTime(), nullable=True),
        sa.Column("reviewed_by", sa.Integer(), sa.ForeignKey("app_users.id", ondelete="SET NULL"), nullable=True),
        sa.Column("reviewed_at", sa.DateTime(), nullable=True),
        sa.Column("confirmations", sa.Integer(), nullable=False, server_default="0"),
    )


def downgrade() -> None:
    op.drop_table("catalog_entries")
