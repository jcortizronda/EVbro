"""Drives: the trips EVbro did not see (`drives.unseen`). Idempotent.

A car asleep is read once an hour; with its live channel stopped, a short trip between two readings left only an odometer a
few km higher, and those km were in no trip. They are now recorded as a trip of their own, built from the reading before and
the reading after, and this flag tells them apart: estimated times, no route, no speed.

Revision ID: 0033_drive_unseen
Revises: 0032_command_car_result
Create Date: 2026-11-18
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy import inspect

revision = "0033_drive_unseen"
down_revision = "0032_command_car_result"
branch_labels = None
depends_on = None


def upgrade() -> None:
    cols = {c["name"] for c in inspect(op.get_bind()).get_columns("drives")}
    if "unseen" not in cols:
        op.add_column("drives", sa.Column("unseen", sa.Boolean(), nullable=False, server_default=sa.false()))


def downgrade() -> None:
    op.drop_column("drives", "unseen")
