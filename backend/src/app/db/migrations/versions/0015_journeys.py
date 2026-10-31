"""Named journeys (a holiday, a road trip) that add up everything recorded between two dates (`journeys`). Idempotent.

Revision ID: 0015_journeys
Revises: 0014_mqtt_links
Create Date: 2026-10-31
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy import inspect

revision = "0015_journeys"
down_revision = "0014_mqtt_links"
branch_labels = None
depends_on = None


def upgrade() -> None:
    insp = inspect(op.get_bind())
    if "journeys" not in set(insp.get_table_names()):
        op.create_table(
            "journeys",
            sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
            sa.Column("vin", sa.String(32), sa.ForeignKey("vehicles.vin", ondelete="CASCADE"), nullable=False, index=True),
            sa.Column("name", sa.String(120), nullable=False),
            sa.Column("start_time", sa.DateTime(), nullable=False, index=True),
            sa.Column("end_time", sa.DateTime(), nullable=False),
            sa.Column("notes", sa.Text(), nullable=True),
            sa.Column("created_at", sa.DateTime(), nullable=True),
        )


def downgrade() -> None:
    op.drop_table("journeys")
