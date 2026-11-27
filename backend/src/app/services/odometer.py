"""Kilometres driven over a span of time, from the odometer readings EVbro has.

The «€ per 100 km» of Costes divided what EVbro had recorded (about a month of costs) by the km since the car was bought (years of
driving): 1.29 €/100 km on the demo car, five times too low on a real one. Estadísticas divided the same costs by the km of the trips
EVbro saw, and costs imported from before those trips made it 239.9 €/100 km (calculations audit, 27-11-2026). Both now divide by
the km the odometer moved between the first and the last day the figures cover.
"""
from __future__ import annotations

import datetime
from typing import List, Optional, Tuple

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Drive, Refuel, Vehicle
from app.timeutil import utcnow

Reading = Tuple[datetime.datetime, float]


async def readings(session: AsyncSession, vehicle: Vehicle) -> List[Reading]:
    """The dated odometer readings the car itself gave (trips, detected refuels), the delivery and now, oldest first. What is typed
    by hand (a refuel or a garage visit entered later) usually carries today's odometer with a past date: it is left out."""
    out: List[Reading] = []
    for start, s_odo, end, e_odo in (await session.execute(
            select(Drive.start_time, Drive.start_odometer, Drive.end_time, Drive.end_odometer).where(Drive.vin == vehicle.vin))).all():
        if start is not None and s_odo:
            out.append((start, float(s_odo)))
        if end is not None and e_odo:
            out.append((end, float(e_odo)))
    for when, odo in (await session.execute(select(Refuel.timestamp, Refuel.odometer).where(Refuel.vin == vehicle.vin,
                                                                                            Refuel.detected.is_(True)))).all():
        if when is not None and odo:
            out.append((when, float(odo)))
    delivered = vehicle.delivery_date or vehicle.registration_date
    if delivered is not None:
        out.append((datetime.datetime.combine(delivered, datetime.time()) if isinstance(delivered, datetime.date)
                    and not isinstance(delivered, datetime.datetime) else delivered, float(vehicle.purchase_odometer or 0.0)))
    if vehicle.odometer:
        out.append((utcnow(), float(vehicle.odometer)))
    out.sort(key=lambda r: r[0])
    return out


def at(points: List[Reading], when: datetime.datetime) -> Optional[float]:
    """The odometer at `when`, interpolated in time between the readings around it (the last reading before can be the delivery,
    a year earlier: taking it as is would count a year of driving). Only the readings before or only the ones after: the nearest."""
    before = [(t, odo) for t, odo in points if t <= when]
    after = [(t, odo) for t, odo in points if t > when]
    if not before:
        return min(odo for _, odo in after) if after else None
    t0, o0 = max(before, key=lambda r: (r[0], r[1]))
    o0 = max(odo for _, odo in before)                 # a reading typed with a slip must not make the odometer go backwards
    if not after:
        return o0
    t1, o1 = min(after, key=lambda r: (r[0], -r[1]))
    if o1 <= o0 or t1 <= t0:
        return o0
    return o0 + (o1 - o0) * (when - t0).total_seconds() / (t1 - t0).total_seconds()


async def km_between(session: AsyncSession, vehicle: Vehicle, start: Optional[datetime.datetime],
                     end: Optional[datetime.datetime]) -> Optional[float]:
    """Km driven from `start` to `end` (naive UTC; None = from the first reading / until now). None when it cannot be told."""
    points = await readings(session, vehicle)
    if len(points) < 2:
        return None
    first = at(points, start) if start is not None else points[0][1]
    last = at(points, end) if end is not None else max(odo for _, odo in points)
    if first is None or last is None:
        return None
    return max(0.0, last - first)
