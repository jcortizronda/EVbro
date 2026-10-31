"""Baseline: the schema exactly as `app.db.models` describes it on 2026-10-29, when Alembic took over.

Databases created before this date reach the same state through the legacy path in `db/session.py` (`create_all` plus the list of
`ALTER TABLE ... IF NOT EXISTS`) and are then stamped here. From this revision on, every schema change is a new file in this folder;
nothing is added to the legacy list any more.

Revision ID: 0001_baseline
Revises:
Create Date: 2026-10-29
"""
from __future__ import annotations

from alembic import op

from app.db.models import Base

revision = "0001_baseline"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Only runs on an EMPTY database (a tracked one is already at this point): the models are the source of truth of the baseline.
    Base.metadata.create_all(bind=op.get_bind())


def downgrade() -> None:
    Base.metadata.drop_all(bind=op.get_bind())
