"""Refuels from a spreadsheet: `refuels.import_batch`, the import a refuel came with, so it can be undone whole
(services/refuel_import.py). Idempotent.

Revision ID: 0040_refuel_import
Revises: 0039_account_email
Create Date: 2026-11-25
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy import inspect

revision = "0040_refuel_import"
down_revision = "0039_account_email"
branch_labels = None
depends_on = None


def upgrade() -> None:
    insp = inspect(op.get_bind())
    if "import_batch" not in {c["name"] for c in insp.get_columns("refuels")}:
        op.add_column("refuels", sa.Column("import_batch", sa.String(32), nullable=True))
        op.create_index("ix_refuels_import_batch", "refuels", ["import_batch"])


def downgrade() -> None:
    insp = inspect(op.get_bind())
    if "import_batch" in {c["name"] for c in insp.get_columns("refuels")}:
        op.drop_index("ix_refuels_import_batch", table_name="refuels")
        op.drop_column("refuels", "import_batch")
