"""Re-estimate the energy of drives and charges with the capacity now in each car sheet. Idempotent.

Rows written while the sheet had no capacity used the default 19.27 kWh. Same rules as services/energy_recalc.py: drives with
battery points spent, charges with start and end battery; the cost of a charge priced per kWh follows its kWh unless a meter
reading (`kwh_used`) exists. Cars without a capacity in the sheet are left alone. `ROUND` needs a NUMERIC on PostgreSQL
(`round(double precision, integer)` does not exist; SQLite does not care), hence the CASTs.

Revision ID: 0017_energy_recalc
Revises: 0016_odometer_distance
Create Date: 2026-10-31
"""
from __future__ import annotations

from alembic import op

revision = "0017_energy_recalc"
down_revision = "0016_odometer_distance"
branch_labels = None
depends_on = None

_CAP = "(SELECT v.battery_capacity_kwh FROM vehicles v WHERE v.vin = {t}.vin)"
_HAS_CAP = "EXISTS (SELECT 1 FROM vehicles v WHERE v.vin = {t}.vin AND v.battery_capacity_kwh IS NOT NULL AND v.battery_capacity_kwh > 0)"


def upgrade() -> None:
    op.execute(f"UPDATE drives SET energy_consumed_kwh = ROUND(CAST(battery_used_pct / 100.0 * {_CAP.format(t='drives')} AS NUMERIC), 2) "
               f"WHERE battery_used_pct > 0 AND {_HAS_CAP.format(t='drives')}")
    op.execute("UPDATE charging_sessions SET kwh_added = ROUND(CAST((CASE WHEN end_battery_pct > start_battery_pct THEN end_battery_pct - start_battery_pct "
               f"ELSE 0 END) / 100.0 * {_CAP.format(t='charging_sessions')} AS NUMERIC), 2) "
               f"WHERE start_battery_pct IS NOT NULL AND end_battery_pct IS NOT NULL AND {_HAS_CAP.format(t='charging_sessions')}")
    op.execute("UPDATE charging_sessions SET charge_cost = ROUND(CAST(effective_cost_per_kwh * kwh_added AS NUMERIC), 2) "
               "WHERE (kwh_used IS NULL OR kwh_used <= 0) AND effective_cost_per_kwh IS NOT NULL "
               f"AND start_battery_pct IS NOT NULL AND end_battery_pct IS NOT NULL AND {_HAS_CAP.format(t='charging_sessions')}")


def downgrade() -> None:
    pass  # the previous estimates are not kept
