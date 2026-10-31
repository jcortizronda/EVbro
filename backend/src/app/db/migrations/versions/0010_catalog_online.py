"""Online catalogue: keys for other installations (`catalog_keys`), where this server takes its catalogue from (`catalog_remote`) and
the origin of each entry (`catalog_entries.origin/remote_id/remote_note/forwarded_at`). Idempotent.

Revision ID: 0010_catalog_online
Revises: 0009_consent_audit
Create Date: 2026-10-30
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy import inspect

revision = "0010_catalog_online"
down_revision = "0009_consent_audit"
branch_labels = None
depends_on = None


def upgrade() -> None:
    insp = inspect(op.get_bind())
    cols = {c["name"] for c in insp.get_columns("catalog_entries")}
    if "origin" not in cols:
        op.add_column("catalog_entries", sa.Column("origin", sa.String(10), nullable=False, server_default="local"))
        op.create_index("ix_catalog_entries_origin", "catalog_entries", ["origin"])
    if "remote_id" not in cols:
        op.add_column("catalog_entries", sa.Column("remote_id", sa.Integer(), nullable=True))
    if "remote_note" not in cols:
        op.add_column("catalog_entries", sa.Column("remote_note", sa.String(120), nullable=True))
    if "forwarded_at" not in cols:
        op.add_column("catalog_entries", sa.Column("forwarded_at", sa.DateTime(), nullable=True))
    tables = set(insp.get_table_names())
    if "catalog_keys" not in tables:
        op.create_table(
            "catalog_keys",
            sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
            sa.Column("key_hash", sa.String(64), nullable=False, unique=True, index=True),
            sa.Column("note", sa.String(120), nullable=True),
            sa.Column("created_by", sa.Integer(), nullable=True),
            sa.Column("created_at", sa.DateTime(), nullable=False),
            sa.Column("expires_at", sa.DateTime(), nullable=True),
            sa.Column("revoked", sa.Boolean(), nullable=False, server_default="false"),
            sa.Column("last_used_at", sa.DateTime(), nullable=True),
            sa.Column("downloads", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("proposals", sa.Integer(), nullable=False, server_default="0"),
        )
    if "catalog_remote" not in tables:
        op.create_table(
            "catalog_remote",
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column("source", sa.String(10), nullable=False, server_default="own"),
            sa.Column("link", sa.Text(), nullable=True),
            sa.Column("last_sync_at", sa.DateTime(), nullable=True),
            sa.Column("last_error", sa.String(255), nullable=True),
            sa.Column("entries", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("updated_at", sa.DateTime(), nullable=True),
        )


def downgrade() -> None:
    op.drop_table("catalog_remote")
    op.drop_table("catalog_keys")
    op.drop_index("ix_catalog_entries_origin", table_name="catalog_entries")
    for col in ("forwarded_at", "remote_note", "remote_id", "origin"):
        op.drop_column("catalog_entries", col)
