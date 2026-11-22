"""Reads asked of the Ebro cloud per day: `cloud_usage` (services/cloud_usage.py). Idempotent.

Revision ID: 0037_cloud_usage
Revises: 0036_account_exit_surveys
Create Date: 2026-11-22
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy import inspect

revision = "0037_cloud_usage"
down_revision = "0036_account_exit_surveys"
branch_labels = None
depends_on = None


def upgrade() -> None:
    if "cloud_usage" in inspect(op.get_bind()).get_table_names():
        return                                    # a database built from the models already has it, and there is nothing to fill in
    op.create_table(
        "cloud_usage",
        sa.Column("day", sa.Date(), primary_key=True),
        sa.Column("kind", sa.String(20), primary_key=True),
        sa.Column("count", sa.Integer(), nullable=False, server_default="0"),
    )


def downgrade() -> None:
    op.drop_table("cloud_usage")
