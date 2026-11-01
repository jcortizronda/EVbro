"""Re-estimate the energy of past drives and charges when the car sheet's battery capacity changes.

EVBro never measures energy: a drive's kWh are its battery points spent × the sheet's capacity, and a charge's kWh added are
the points gained × the same capacity (charge_detector.py, trip_detector.py). A car whose sheet had no capacity when those rows
were written got the default (19.27 kWh), so an S900 with 34.5 kWh ended up with roughly half of its real figures. When the
owner sets or corrects the capacity, everything estimated from it is recomputed here, including the cost of charges priced per
kWh (manual price or geofence tariff: the effective €/kWh stays, the kWh change). Charges with a meter reading (`kwh_used`)
keep their cost: what was paid is the grid energy, not the estimate. Drives edited by hand are recomputed too: there is no flag
that tells them apart, and the sheet's capacity is the single source for this estimate (docs/costs.md, «Energía estimada»).
"""
from __future__ import annotations

import logging
from typing import Dict

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import ChargingSession, Drive, Vehicle

_LOGGER = logging.getLogger(__name__)


async def recalculate_for_capacity(session: AsyncSession, vehicle: Vehicle) -> Dict[str, int]:
    """Rewrites `energy_consumed_kwh` of the drives and `kwh_added` (+ per-kWh cost) of the charges of `vehicle` with its current
    capacity. Returns how many rows of each kind changed. Does nothing without a capacity."""
    capacity = vehicle.battery_capacity_kwh
    if not capacity:
        return {"drives": 0, "charges": 0}
    drives = (await session.execute(select(Drive).where(Drive.vin == vehicle.vin, Drive.battery_used_pct > 0))).scalars().all()
    for d in drives:
        d.energy_consumed_kwh = round(d.battery_used_pct / 100.0 * capacity, 2)
    charges = (await session.execute(select(ChargingSession).where(ChargingSession.vin == vehicle.vin, ChargingSession.start_battery_pct.isnot(None),
                                                                    ChargingSession.end_battery_pct.isnot(None)))).scalars().all()
    for c in charges:
        gained = max(0.0, (c.end_battery_pct or 0.0) - (c.start_battery_pct or 0.0))
        c.kwh_added = round(gained / 100.0 * capacity, 2)
        if not c.kwh_used and c.effective_cost_per_kwh is not None:
            c.charge_cost = round(c.effective_cost_per_kwh * c.kwh_added, 2)
    await session.flush()
    _LOGGER.info("Energy re-estimated with %.2f kWh capacity for %s: %d drives and %d charges", capacity, vehicle.vin, len(drives), len(charges))
    return {"drives": len(drives), "charges": len(charges)}
