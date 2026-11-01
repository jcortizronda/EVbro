"""Each account's own Inicio: which widgets it shows and in what order. Idempotent.

`app_users.home_layout` is `{"widgets": ["range", "map", ...]}`; NULL means the default layout. The keys are defined by the
interface (features/home-widgets.js), so the server only checks their shape.

Revision ID: 0021_home_layout
Revises: 0020_oem_reference
Create Date: 2026-11-01
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy import inspect

revision = "0021_home_layout"
down_revision = "0020_oem_reference"
branch_labels = None
depends_on = None


def upgrade() -> None:
    if not any(c["name"] == "home_layout" for c in inspect(op.get_bind()).get_columns("app_users")):
        op.add_column("app_users", sa.Column("home_layout", sa.JSON(), nullable=True))


def downgrade() -> None:
    op.drop_column("app_users", "home_layout")
