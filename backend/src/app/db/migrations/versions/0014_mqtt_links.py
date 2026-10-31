"""A manager's own MQTT broker for Home Assistant (`mqtt_links`). Idempotent.

Revision ID: 0014_mqtt_links
Revises: 0013_fuel_type
Create Date: 2026-10-30
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy import inspect

revision = "0014_mqtt_links"
down_revision = "0013_fuel_type"
branch_labels = None
depends_on = None


def upgrade() -> None:
    insp = inspect(op.get_bind())
    if "mqtt_links" not in set(insp.get_table_names()):
        op.create_table(
            "mqtt_links",
            sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
            sa.Column("user_id", sa.Integer(), sa.ForeignKey("app_users.id", ondelete="CASCADE"), nullable=False, unique=True, index=True),
            sa.Column("host", sa.String(255), nullable=False),
            sa.Column("port", sa.Integer(), nullable=True),
            sa.Column("username", sa.String(120), nullable=True),
            sa.Column("password", sa.Text(), nullable=True),
            sa.Column("use_tls", sa.Boolean(), nullable=False, server_default="false"),
            sa.Column("topic_prefix", sa.String(60), nullable=False, server_default="ebromate"),
            sa.Column("ha_discovery", sa.Boolean(), nullable=False, server_default="true"),
            sa.Column("ha_prefix", sa.String(60), nullable=False, server_default="homeassistant"),
            sa.Column("enabled", sa.Boolean(), nullable=False, server_default="true"),
            sa.Column("created_at", sa.DateTime(), nullable=True),
            sa.Column("updated_at", sa.DateTime(), nullable=True),
        )


def downgrade() -> None:
    op.drop_table("mqtt_links")
