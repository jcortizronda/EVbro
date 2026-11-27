"""A new tank size re-estimates the litres of past drives and detected refuels (calculations audit, 27-11-2026).

They were worked out once with the sheet's tank (50 L when it had none); the model later filled in 60/70 L and nothing was redone.
"""
from __future__ import annotations

import datetime

from sqlalchemy import select

from app.db.models import Drive, Refuel, Vehicle
from app.db.session import async_session_factory
from app.services.energy_recalc import recalculate_for_tank

VIN = "LSJA0000000000092"
T = datetime.datetime(2026, 11, 20, 9, 0)


async def test_drive_and_refuel_litres_follow_the_tank_but_typed_ones_stay(db) -> None:
    async with async_session_factory() as s:
        car = Vehicle(vin=VIN, owner_id=1, name="X", fuel_tank_liters=70.0)
        s.add(car)
        s.add(Drive(vin=VIN, start_time=T, end_time=T + datetime.timedelta(hours=1), start_fuel=100.0, end_fuel=43.0, fuel_used_l=28.5))
        s.add(Refuel(vin=VIN, timestamp=T, fuel_before_pct=20.0, fuel_after_pct=95.0, liters=37.5, price_per_liter=1.5, total_cost=56.25))
        s.add(Refuel(vin=VIN, timestamp=T + datetime.timedelta(days=1), fuel_before_pct=20.0, fuel_after_pct=95.0, liters=37.5,
                     price_per_liter=1.5, total_cost=60.0))                                      # a total typed by hand
        s.add(Refuel(vin=VIN, timestamp=T + datetime.timedelta(days=2), fuel_before_pct=10.0, fuel_after_pct=90.0, liters=41.0,
                     liters_entered=True))                                                         # litres typed by hand
        await s.commit()
        await recalculate_for_tank(s, car)
        await s.commit()
    async with async_session_factory() as s:
        assert (await s.execute(select(Drive.fuel_used_l))).scalar_one() == 39.9
        refuels = (await s.execute(select(Refuel).order_by(Refuel.timestamp))).scalars().all()
    assert (refuels[0].liters, refuels[0].total_cost) == (52.5, 78.75)
    assert (refuels[1].liters, refuels[1].total_cost) == (52.5, 60.0)
    assert refuels[2].liters == 41.0
