"""Vehicles: the commands the cloud refused for each car with A00084 (`vehicles.rejected_commands`). Idempotent.

Not every car accepts every command of the catalogue: the S900 answers «No vehicle control command permission» to the rear
window heater (08-11-2026, five tries by its owner). The interface marks those controls and the Home Assistant discovery
leaves them out; an accepted send takes the command off the list.

Revision ID: 0027_vehicle_rejected_commands
Revises: 0026_vehicle_reported_keys
Create Date: 2026-11-09
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy import inspect

revision = "0027_vehicle_rejected_commands"
down_revision = "0026_vehicle_reported_keys"
branch_labels = None
depends_on = None


def upgrade() -> None:
    cols = {c["name"] for c in inspect(op.get_bind()).get_columns("vehicles")}
    if "rejected_commands" not in cols:
        op.add_column("vehicles", sa.Column("rejected_commands", sa.JSON(), nullable=True))


def downgrade() -> None:
    op.drop_column("vehicles", "rejected_commands")
