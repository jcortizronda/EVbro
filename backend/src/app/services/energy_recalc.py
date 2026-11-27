"""Re-estimate the energy of past drives and charges when the car sheet's battery capacity changes.

EVbro never measures energy: a drive's kWh are its battery points spent × the sheet's capacity, and a charge's kWh added are
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

from app.db.models import ChargingSession, Drive, Refuel, Vehicle

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


async def recalculate_for_tank(session: AsyncSession, vehicle: Vehicle) -> Dict[str, int]:
    """The fuel counterpart: the litres of the drives and of the detected refuels are tank points × the sheet's tank, worked out once,
    when each closed. A sheet without a tank used 50 L until the model filled in the real one (60/70 L), so those litres stayed a
    fifth or a third short (calculations audit, 27-11-2026). Litres typed by the user are not touched; a refuel cost worked out from
    its price per litre follows the litres, a total typed by hand does not."""
    from app.services.trip_detector import fuel_litres

    tank = vehicle.fuel_tank_liters
    if not tank:
        return {"drives": 0, "refuels": 0}
    drives = (await session.execute(select(Drive).where(Drive.vin == vehicle.vin, Drive.start_fuel.isnot(None), Drive.end_fuel.isnot(None)))).scalars().all()
    for d in drives:
        d.fuel_used_l = fuel_litres(d.start_fuel + (d.fuel_refuelled_pct or 0.0), d.end_fuel, tank)
    refuels = (await session.execute(select(Refuel).where(Refuel.vin == vehicle.vin, Refuel.detected.is_(True), Refuel.liters_entered.is_(False),
                                                          Refuel.fuel_before_pct.isnot(None), Refuel.fuel_after_pct.isnot(None)))).scalars().all()
    for r in refuels:
        old = r.liters
        r.liters = round(max(0.0, r.fuel_after_pct - r.fuel_before_pct) / 100.0 * tank, 2)
        if r.price_per_liter is not None and r.total_cost is not None and old is not None and abs(r.total_cost - r.price_per_liter * old) < 0.02:
            r.total_cost = round(r.price_per_liter * r.liters, 2)          # it came from the price: it follows the litres
    await session.flush()
    _LOGGER.info("Fuel re-estimated with a %.0f L tank for %s: %d drives and %d refuels", tank, vehicle.vin, len(drives), len(refuels))
    return {"drives": len(drives), "refuels": len(refuels)}
