"""Drive fuel in litres: the points of the tank gauge were stored as litres (`drives.fuel_used_l`). Idempotent.

The car reports the tank as a % (`oilSurplus`). Up to 0023 the trip detector stored `start_fuel - end_fuel` straight into
`fuel_used_l`, so a 400 km trip that took the tank from 100 % to 43 % showed «57 L» (04-11-2026). Every drive whose litres still
equal its gauge points is converted with the tank size of its vehicle sheet (50 L when there is none); a value corrected by hand
no longer matches the points and is left alone.

Revision ID: 0024_drive_fuel_litres
Revises: 0023_catalog_signup
Create Date: 2026-11-04
"""
from __future__ import annotations

from alembic import op
from sqlalchemy import text

revision = "0024_drive_fuel_litres"
down_revision = "0023_catalog_signup"
branch_labels = None
depends_on = None


def upgrade() -> None:
    conn = op.get_bind()
    tanks = {vin: tank for vin, tank in conn.execute(text("SELECT vin, fuel_tank_liters FROM vehicles"))}
    rows = conn.execute(text(
        "SELECT id, vin, start_fuel, end_fuel, fuel_used_l FROM drives "
        "WHERE start_fuel IS NOT NULL AND end_fuel IS NOT NULL AND fuel_used_l IS NOT NULL AND fuel_used_l > 0"
    )).all()
    for drive_id, vin, start, end, litres in rows:
        points = max(0.0, start - end)
        if abs(litres - points) > 0.011:                      # not the detector's figure: edited by hand, keep it
            continue
        litres_now = round(points / 100.0 * (tanks.get(vin) or 50.0), 2)
        conn.execute(text("UPDATE drives SET fuel_used_l = :l WHERE id = :id"), {"l": litres_now, "id": drive_id})


def downgrade() -> None:
    pass  # the gauge points are still in start_fuel/end_fuel; older code recomputes them on the next edit
