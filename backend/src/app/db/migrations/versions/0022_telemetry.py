"""Anonymous usage statistics and error reports (services/telemetry.py, docs/telemetry.md). Idempotent.

Sender side: `telemetry_local_errors` keeps this installation's errors until a report carries them. Receiver side (only the
project's server, TELEMETRY_RECEIVER): `telemetry_installs`, `telemetry_error_groups` and `telemetry_error_hits`.

Revision ID: 0022_telemetry
Revises: 0021_home_layout
Create Date: 2026-11-02
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy import inspect

revision = "0022_telemetry"
down_revision = "0021_home_layout"
branch_labels = None
depends_on = None


def upgrade() -> None:
    existing = set(inspect(op.get_bind()).get_table_names())
    if "telemetry_local_errors" not in existing:
        op.create_table(
            "telemetry_local_errors",
            sa.Column("fingerprint", sa.String(64), primary_key=True),
            sa.Column("kind", sa.String(10), nullable=False),
            sa.Column("level", sa.String(10), nullable=False),
            sa.Column("error_type", sa.String(120), nullable=True),
            sa.Column("message", sa.Text(), nullable=True),
            sa.Column("location", sa.String(240), nullable=True),
            sa.Column("frames", sa.JSON(), nullable=True),
            sa.Column("count", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("first_seen", sa.DateTime(), nullable=False),
            sa.Column("last_seen", sa.DateTime(), nullable=False),
        )
    if "telemetry_installs" not in existing:
        op.create_table(
            "telemetry_installs",
            sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
            sa.Column("install_id", sa.String(40), nullable=False),
            sa.Column("first_seen", sa.DateTime(), nullable=False),
            sa.Column("last_seen", sa.DateTime(), nullable=False),
            sa.Column("version", sa.String(40), nullable=True),
            sa.Column("commit", sa.String(40), nullable=True),
            sa.Column("reports", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("report", sa.JSON(), nullable=True),
        )
        op.create_index("ix_telemetry_installs_install_id", "telemetry_installs", ["install_id"], unique=True)
    if "telemetry_error_groups" not in existing:
        op.create_table(
            "telemetry_error_groups",
            sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
            sa.Column("fingerprint", sa.String(64), nullable=False),
            sa.Column("kind", sa.String(10), nullable=False),
            sa.Column("level", sa.String(10), nullable=False),
            sa.Column("error_type", sa.String(120), nullable=True),
            sa.Column("message", sa.Text(), nullable=True),
            sa.Column("location", sa.String(240), nullable=True),
            sa.Column("frames", sa.JSON(), nullable=True),
            sa.Column("total_count", sa.BigInteger(), nullable=False, server_default="0"),
            sa.Column("versions", sa.JSON(), nullable=True),
            sa.Column("first_seen", sa.DateTime(), nullable=False),
            sa.Column("last_seen", sa.DateTime(), nullable=False),
            sa.Column("resolved_at", sa.DateTime(), nullable=True),
        )
        op.create_index("ix_telemetry_error_groups_fingerprint", "telemetry_error_groups", ["fingerprint"], unique=True)
    if "telemetry_error_hits" not in existing:
        op.create_table(
            "telemetry_error_hits",
            sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
            sa.Column("group_id", sa.Integer(), sa.ForeignKey("telemetry_error_groups.id", ondelete="CASCADE"), nullable=False),
            sa.Column("install_id", sa.String(40), nullable=False),
            sa.Column("count", sa.BigInteger(), nullable=False, server_default="0"),
            sa.Column("version", sa.String(40), nullable=True),
            sa.Column("last_seen", sa.DateTime(), nullable=False),
            sa.UniqueConstraint("group_id", "install_id", name="uq_telemetry_hit"),
        )
        op.create_index("ix_telemetry_error_hits_group_id", "telemetry_error_hits", ["group_id"])
        op.create_index("ix_telemetry_error_hits_install_id", "telemetry_error_hits", ["install_id"])


def downgrade() -> None:
    for name in ("telemetry_error_hits", "telemetry_error_groups", "telemetry_installs", "telemetry_local_errors"):
        op.drop_table(name)
