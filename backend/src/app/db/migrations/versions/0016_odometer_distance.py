"""Drive distance is the odometer alone: drop the GPS decimals older versions added on top of it (`drives.distance_km`). Idempotent.

Up to 0015 a drive's distance was the odometer (whole km) plus the decimal part of the GPS track when both agreed. Those decimals
were counted again by the odometer in the next drive, so sums of drives drifted above the car's own total. Drives detected from
the car (the ones with start and end odometer) are truncated to the whole kilometre; drives built from GPS only (Traccar) keep
their value because they have no odometer to fall back on.

Revision ID: 0016_odometer_distance
Revises: 0015_journeys
Create Date: 2026-10-31
"""
from __future__ import annotations

from alembic import op

revision = "0016_odometer_distance"
down_revision = "0015_journeys"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # CAST(... AS INTEGER) truncates towards zero in both PostgreSQL and SQLite; distances are never negative.
    op.execute(
        "UPDATE drives SET distance_km = CAST(distance_km AS INTEGER) "
        "WHERE start_odometer IS NOT NULL AND end_odometer IS NOT NULL AND distance_km IS NOT NULL "
        "AND distance_km <> CAST(distance_km AS INTEGER)"
    )


def downgrade() -> None:
    pass  # the dropped decimals cannot be recovered
