"""Costes and Estadísticas divide the costs by the km the odometer moved while they were spent (calculations audit, 27-11-2026).

Costes used to divide a month of costs by the km since the purchase (several times too low); Estadísticas by the km of the trips EVbro
saw, so costs imported from before those trips inflated it.
"""
from __future__ import annotations

import datetime

from app.api.routes_costs import get_costs
from app.db.models import ChargingSession, Drive, Refuel, Vehicle
from app.db.session import async_session_factory
from app.services import stats
from app.timeutil import utcnow

VIN = "LSJA0000000000091"


async def test_cost_per_100km_uses_the_km_driven_while_the_costs_were_spent(db) -> None:
    now = utcnow()
    month_ago = now - datetime.timedelta(days=30)
    async with async_session_factory() as s:
        s.add(Vehicle(vin=VIN, owner_id=1, name="X", odometer=11000.0, purchase_odometer=9000.0,
                      delivery_date=(now - datetime.timedelta(days=400)).date()))
        s.add(Drive(vin=VIN, start_time=month_ago, end_time=month_ago + datetime.timedelta(hours=1), distance_km=40,
                    start_odometer=10000.0, end_odometer=10040.0))
        s.add(ChargingSession(vin=VIN, start_time=month_ago - datetime.timedelta(minutes=30), end_time=month_ago - datetime.timedelta(minutes=5),
                              charge_cost=60.0))
        s.add(Refuel(vin=VIN, timestamp=now - datetime.timedelta(days=10), liters=30, total_cost=40.0, odometer=10600.0))
        await s.commit()
    async with async_session_factory() as s:
        costs = await get_costs(VIN, "all", None, None, s)
    # 100 € over the ~1000 km driven since the first cost (just before the 10 000 km trip; the reading before it is the delivery, a
    # year earlier, so the odometer at that moment is interpolated), not over the 2 000 km since the purchase
    assert costs["km"] == 1000 and costs["per_100km"] == 10.0
    async with async_session_factory() as s:
        summary = await stats.compute(s, VIN, "all")
    assert summary["totals"]["cost_per_km"] == 0.1                   # the same figure in Estadísticas (€/km)
