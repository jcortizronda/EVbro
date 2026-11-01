"""The car maker's own part number («Ref. fabricante», e.g. Ebro's) next to the aftermarket reference. Idempotent.

`vehicle_parts.oem_reference` is the one of this car. In the catalogue it lives on each fitment (`catalog_fitments.oem_reference`),
because the same filter has one number at Ebro and another at a different make; `catalog_entries.oem_reference` mirrors the first
fitment, like `make`, `model` and `years`.

Revision ID: 0020_oem_reference
Revises: 0019_evbro_secrets
Create Date: 2026-11-01
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy import inspect

revision = "0020_oem_reference"
down_revision = "0019_evbro_secrets"
branch_labels = None
depends_on = None

TABLES = ("vehicle_parts", "catalog_entries", "catalog_fitments")


def upgrade() -> None:
    insp = inspect(op.get_bind())
    for table in TABLES:
        if not any(c["name"] == "oem_reference" for c in insp.get_columns(table)):
            op.add_column(table, sa.Column("oem_reference", sa.String(120), nullable=True))


def downgrade() -> None:
    for table in TABLES:
        op.drop_column(table, "oem_reference")
