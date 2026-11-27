"""MQTT link: orders that open the car need their own switch (`mqtt_links.allow_open_commands`). Idempotent.

Anybody holding a manager's session could point the MQTT link at a broker of theirs and open the car with an `UNLOCK` message, the
stored PIN doing the rest (security audit of 27-11-2026). Orders that open, unlock or disarm are now refused over MQTT unless the
manager switches this on, which asks for the PIN. Existing links start with it off.

Revision ID: 0043_mqtt_open_commands
Revises: 0042_command_actor
Create Date: 2026-11-27
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy import inspect

revision = "0043_mqtt_open_commands"
down_revision = "0042_command_actor"
branch_labels = None
depends_on = None


def upgrade() -> None:
    cols = {c["name"] for c in inspect(op.get_bind()).get_columns("mqtt_links")}
    if "allow_open_commands" not in cols:
        op.add_column("mqtt_links", sa.Column("allow_open_commands", sa.Boolean(), nullable=False, server_default=sa.false()))


def downgrade() -> None:
    op.drop_column("mqtt_links", "allow_open_commands")
