"""One plug-in is one charge: a server restart or a short pause at the charger must not split it (seen on 01-11-2026)."""
from __future__ import annotations

import datetime

import pytest
from freezegun import freeze_time
from sqlalchemy import select

from app.db.models import ChargePoint, ChargingSession, Vehicle
from app.db.session import async_session_factory
from app.services import charge_detector as cd
from app.services.charge_detector import ChargeDetector

VIN = "LSJA0000000000002"
D = datetime.datetime


@pytest.fixture
async def car(db, monkeypatch):
    async def no_geofence(*a, **k):
        return None

    async def address(lat, lon):
        return "Centro comercial" if lat == 37.0 else f"Calle {lat:.3f}"

    monkeypatch.setattr(cd, "find_geofence", no_geofence)
    monkeypatch.setattr(cd, "reverse_geocode", address)
    for state in (cd._ACTIVE_CHARGES, cd._RECOVERED, cd._PAUSED):
        state.clear()
    async with async_session_factory() as s:
        s.add(Vehicle(owner_id=1, vin=VIN, name="Ebro", battery_capacity_kwh=20.0))
        await s.commit()
    yield
    for state in (cd._ACTIVE_CHARGES, cd._RECOVERED, cd._PAUSED):
        state.clear()


async def reading(at, soc, charging, plug=True, kw=3.0, pos=(37.0, -6.0)):
    with freeze_time(at):
        async with async_session_factory() as s:
            v = (await s.execute(select(Vehicle).where(Vehicle.vin == VIN))).scalar_one()
            res = await ChargeDetector.process_snapshot(s, v, {"is_charging": charging, "cable_connected": plug, "battery_level": soc,
                                                              "charge_power_kw": kw, "latitude": pos[0], "longitude": pos[1]})
            await s.commit()
            return res


def restart():
    """What a container restart does to the detector: its memory is gone, the database stays."""
    for state in (cd._ACTIVE_CHARGES, cd._RECOVERED, cd._PAUSED):
        state.clear()


async def sessions():
    async with async_session_factory() as s:
        return (await s.execute(select(ChargingSession).where(ChargingSession.vin == VIN).order_by(ChargingSession.id))).scalars().all()


async def test_a_restart_mid_charge_carries_on_with_the_same_session(car) -> None:
    await reading(D(2026, 11, 1, 17, 44), 45, True)
    await reading(D(2026, 11, 1, 18, 41), 53, True)
    restart()
    await reading(D(2026, 11, 1, 18, 45), 53, True)
    end = await reading(D(2026, 11, 1, 20, 28), 67, False, plug=False, kw=0.0)
    rows = await sessions()
    assert len(rows) == 1
    assert (end.start_battery_pct, end.end_battery_pct, end.kwh_added) == (45, 67, 4.4)
    assert end.start_time == D(2026, 11, 1, 17, 44) and end.end_time == D(2026, 11, 1, 20, 28)
    async with async_session_factory() as s:
        points = (await s.execute(select(ChargePoint).where(ChargePoint.charging_session_id == end.id))).scalars().all()
    assert len(points) == 4


async def test_a_session_left_open_is_closed_at_its_last_reading_when_the_car_no_longer_charges(car) -> None:
    await reading(D(2026, 11, 1, 9, 24), 23, True)
    await reading(D(2026, 11, 1, 10, 30), 60, True)
    restart()
    assert await reading(D(2026, 11, 1, 12, 0), 58, False, plug=False, kw=0.0) is None
    (only,) = await sessions()
    assert only.end_time == D(2026, 11, 1, 10, 30) and only.end_battery_pct == 60 and only.kwh_added == 7.4


async def test_an_old_open_session_is_not_resumed_by_a_new_charge(car) -> None:
    await reading(D(2026, 11, 1, 9, 0), 20, True)
    restart()
    await reading(D(2026, 11, 2, 9, 0), 30, True)                  # a day later: another plug-in
    first, second = await sessions()
    assert first.end_time == D(2026, 11, 1, 9, 0) and second.end_time is None and second.start_battery_pct == 30


async def test_a_short_pause_without_unplugging_is_still_one_charge(car) -> None:
    await reading(D(2026, 11, 1, 20, 0), 63, True)
    paused = await reading(D(2026, 11, 1, 20, 27), 67, False, kw=0.0)       # stopped at the post, cable still in
    assert paused is not None
    await reading(D(2026, 11, 1, 20, 27, 40), 67, True, kw=2.65)            # and charging again half a minute later
    end = await reading(D(2026, 11, 1, 20, 28, 20), 67, False, plug=False, kw=0.0)
    rows = await sessions()
    assert len(rows) == 1 and end.id == paused.id
    assert end.start_time == D(2026, 11, 1, 20, 0) and end.end_time == D(2026, 11, 1, 20, 28, 20) and end.kwh_added == 0.8


async def test_unplugging_or_a_long_pause_starts_a_new_charge(car) -> None:
    await reading(D(2026, 11, 1, 20, 0), 60, True)
    await reading(D(2026, 11, 1, 20, 30), 70, False, kw=0.0)
    await reading(D(2026, 11, 1, 20, 31), 70, False, plug=False, kw=0.0)    # unplugged
    await reading(D(2026, 11, 1, 20, 35), 70, True)
    await reading(D(2026, 11, 1, 21, 0), 80, False, kw=0.0)
    await reading(D(2026, 11, 2, 1, 0), 80, True)                            # still plugged, but hours later (scheduled charge)
    assert len(await sessions()) == 3


async def test_the_engine_charging_the_battery_on_the_road_is_not_a_charge(car) -> None:
    """04-11-2026: at 69 km/h the car reported `chargeState = 1` (the engine was charging the battery, 22 kW) without any cable."""
    await reading(D(2026, 11, 4, 13, 28, 6), 56, True, plug=False, kw=22.18)
    await reading(D(2026, 11, 4, 13, 28, 46), 56, True, plug=False, kw=4.44)
    assert await sessions() == []
    await reading(D(2026, 11, 4, 14, 0, 47), 50, True, plug=True, kw=2.69)        # back home, cable in: that one is a charge
    (charge,) = await sessions()
    assert charge.start_battery_pct == 50 and charge.end_time is None


async def test_a_charge_moves_where_the_cloud_places_the_plugged_in_car(car) -> None:
    """04-11-2026: a charge at home was born with the stale position of work; an hour later the cloud placed the car at home."""
    t = D(2026, 11, 4, 14, 0)
    await reading(t, 50, True)                                                                  # stale position
    await reading(t + datetime.timedelta(minutes=2), 51, True, pos=(37.00005, -6.00005))        # GPS drift: stays
    await reading(t + datetime.timedelta(hours=1), 59, True, pos=(37.5, -6.5))                  # the real place
    (c,) = await sessions()
    assert (c.latitude, c.longitude) == (37.5, -6.5) and c.address == "Calle 37.500"
