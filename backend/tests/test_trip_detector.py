"""Trip detector: start recovered when detected late, and discarding of ghost trips.

Reproduces the real case of 28/10/2026: the return trip was detected with the car already at 54 km/h (reading triggered by MQTT),
with a mid-route start, odometer and position, and was closed an hour later with start = end (0 s, 0 km).
"""
from __future__ import annotations

import datetime

import pytest
from freezegun import freeze_time
from sqlalchemy import select

from app.db.models import Drive, DrivePosition, Refuel, TelemetrySnapshot, Vehicle
from app.db.session import async_session_factory
from app.services import trip_detector as td
from app.services.trip_detector import TripDetector

VIN = "LSJA0000000000001"
HOME = (40.0, -3.0)          # where it was parked
DESTINATION = (40.005, -2.996)
T0 = datetime.datetime(2026, 10, 28, 14, 36, 21)


@pytest.fixture(autouse=True)
def _clean_state(monkeypatch):
    for d in (td._ACTIVE_DRIVES, td._LAST_MOTION_TIME, td._LAST_IDLE, td._HV_OFF_AT, td._LAST_CLOSED):
        d.clear()

    async def no_geofence(session, lat, lon, **kwargs):
        return None

    async def address(lat, lon):
        return f"Calle {lat:.3f}"

    monkeypatch.setattr(td, "find_geofence", no_geofence)
    monkeypatch.setattr(td, "reverse_geocode", address)
    yield
    for d in (td._ACTIVE_DRIVES, td._LAST_MOTION_TIME, td._LAST_IDLE, td._HV_OFF_AT, td._LAST_CLOSED):
        d.clear()


def reading(speed, odo, pos, soc=72.0, fuel=27.0, **extra):
    return {"speed_kmh": speed, "odometer_km": odo, "latitude": pos[0], "longitude": pos[1], "battery_level": soc,
            "fuel_level": fuel, "state": "driving" if speed > 2 else "parked", **extra}


async def process(vehicle, telemetry, at):
    with freeze_time(at):
        async with async_session_factory() as s:
            v = (await s.execute(select(Vehicle).where(Vehicle.vin == VIN))).scalar_one()
            res = await TripDetector.process_snapshot(s, v, telemetry)
            await s.commit()
            return res


async def drives():
    async with async_session_factory() as s:
        return list((await s.execute(select(Drive).order_by(Drive.id))).scalars())


@pytest.fixture
async def car(db):
    async with async_session_factory() as s:
        s.add(Vehicle(owner_id=1, vin=VIN, name="Ebro", odometer=9417.0))
        await s.commit()
    return VIN


async def test_a_trip_detected_mid_route_recovers_the_start_from_the_parked_reading(car) -> None:
    await process(car, reading(0, 9417, HOME), T0)                                   # 14:36:21 parked
    await process(car, reading(54, 9419, (40.006, -2.997)), T0 + datetime.timedelta(minutes=3, seconds=39))  # 14:40:00
    (d,) = await drives()
    assert d.start_estimated is True
    assert (d.start_lat, d.start_lon) == HOME and d.start_odometer == 9417 and d.start_battery_pct == 72.0
    # 2 km at 35 km/h ≈ 3.4 min before the reading, and never before the parked reading
    assert T0 <= d.start_time < T0 + datetime.timedelta(minutes=3, seconds=39)
    assert d.start_time == T0 + datetime.timedelta(minutes=3, seconds=39) - datetime.timedelta(minutes=2 / 35 * 60)
    async with async_session_factory() as s:
        pos = list((await s.execute(select(DrivePosition).where(DrivePosition.drive_id == d.id).order_by(DrivePosition.timestamp))).scalars())
    assert (pos[0].latitude, pos[0].longitude) == HOME and pos[0].timestamp == d.start_time     # the track starts at the origin


async def test_the_full_trip_closes_with_the_recovered_data(car) -> None:
    await process(car, reading(0, 9417, HOME), T0)
    t = T0 + datetime.timedelta(minutes=3, seconds=39)
    await process(car, reading(54, 9419, (40.006, -2.997)), t)
    await process(car, reading(0, 9419, DESTINATION), t + datetime.timedelta(seconds=70))             # parks
    await process(car, reading(0, 9419, DESTINATION), t + datetime.timedelta(minutes=6))              # closes (≥5 min)
    (d,) = await drives()
    assert d.end_time == t and d.distance_km == 2.0 and d.start_estimated
    assert d.duration_seconds == int((t - d.start_time).total_seconds()) and d.duration_seconds > 60


async def test_without_a_previous_reading_the_start_is_not_marked_as_estimated(car) -> None:
    await process(car, reading(40, 9417, HOME), T0)
    (d,) = await drives()
    assert d.start_estimated is False and d.start_time == T0 and (d.start_lat, d.start_lon) == HOME


async def test_if_the_odometer_has_not_moved_the_trip_starts_now(car) -> None:
    await process(car, reading(0, 9417, HOME), T0)
    now = T0 + datetime.timedelta(minutes=2)
    await process(car, reading(10, 9417, HOME), now)
    (d,) = await drives()
    assert d.start_estimated is False and d.start_time == now


async def test_a_very_old_parked_reading_is_not_used_as_the_starting_point(car) -> None:
    await process(car, reading(0, 9417, HOME), T0)
    await process(car, reading(54, 9440, DESTINATION), T0 + datetime.timedelta(hours=7))
    (d,) = await drives()
    assert d.start_estimated is False and (d.start_lat, d.start_lon) == DESTINATION


async def test_after_a_restart_the_start_is_recovered_from_the_database(car) -> None:
    async with async_session_factory() as s:        # the parked reading was already stored; the detector's memory is empty
        s.add(TelemetrySnapshot(vin=VIN, timestamp=T0, state="parked", speed_kmh=0.0, odometer_km=9417.0, battery_level=72.0,
                                fuel_level=27.0, latitude=HOME[0], longitude=HOME[1]))
        await s.commit()
    await process(car, reading(54, 9419, (40.006, -2.997)), T0 + datetime.timedelta(minutes=4))
    (d,) = await drives()
    assert d.start_estimated and d.start_odometer == 9417 and (d.start_lat, d.start_lon) == HOME


async def test_a_trip_under_a_minute_with_no_km_is_noise_and_is_discarded(car) -> None:
    # a single moving reading (no previous reading to recover) and then parked: the usual ghost trip
    await process(car, reading(54, 9419, DESTINATION), T0)
    assert len(await drives()) == 1
    assert await process(car, reading(0, 9419, DESTINATION), T0 + datetime.timedelta(minutes=6)) is None
    assert await drives() == []
    async with async_session_factory() as s:
        assert (await s.execute(select(DrivePosition))).scalars().all() == []                       # and leaves no orphan points


async def test_a_short_trip_with_kilometres_is_kept(car) -> None:
    await process(car, reading(30, 9417, HOME), T0)
    await process(car, reading(30, 9418, DESTINATION), T0 + datetime.timedelta(seconds=40))
    await process(car, reading(0, 9418, DESTINATION), T0 + datetime.timedelta(minutes=7))
    (d,) = await drives()
    assert d.distance_km == 1.0 and d.end_time is not None


async def test_the_memory_state_is_only_updated_when_parked(car) -> None:
    await process(car, reading(0, 9417, HOME), T0)
    await process(car, reading(50, 9418, DESTINATION), T0 + datetime.timedelta(minutes=2))
    assert td._LAST_IDLE[car]["odometer"] == 9417            # the moving reading does not overwrite the starting point


# ───────────────────────── Distance, end by high voltage and restart ─────────────────────────
async def test_the_distance_is_the_odometer_step(car) -> None:
    # HOME → DESTINATION is about 0.55 km by GPS; the odometer moved 1 km: the odometer wins, whole kilometres
    await process(car, reading(30, 9417, HOME), T0)
    await process(car, reading(30, 9418, DESTINATION), T0 + datetime.timedelta(seconds=40))
    await process(car, reading(0, 9418, DESTINATION), T0 + datetime.timedelta(minutes=7))
    (d,) = await drives()
    assert d.distance_km == 1.0


async def test_the_gps_track_never_adds_decimals_to_the_distance(car) -> None:
    # A track with a fractional GPS length (≈3.4 km) and an odometer that moved 3 km: the drive is 3 km, not 3.4.
    # Those decimals would be counted again by the odometer in the next drive (see docs/trips.md, «Distancia»).
    points = [HOME, (40.005, -2.99), (40.015, -2.98), (40.025, -2.97)]
    for i, p in enumerate(points):
        await process(car, reading(40, 9417 + (3 if i == len(points) - 1 else 0), p), T0 + datetime.timedelta(seconds=30 * i))
    await process(car, reading(0, 9420, points[-1]), T0 + datetime.timedelta(minutes=8))
    (d,) = await drives()
    assert d.distance_km == 3.0


# ───────────────────────── End of the trip ─────────────────────────
async def test_the_trip_ends_when_the_car_was_switched_off_and_closes_after_5_minutes(car) -> None:
    await process(car, reading(0, 9417, HOME, high_voltage_on=False), T0)
    await process(car, reading(50, 9419, DESTINATION, high_voltage_on=True), T0 + datetime.timedelta(minutes=4))
    t_stop = T0 + datetime.timedelta(minutes=6)
    await process(car, reading(0, 9419, DESTINATION, high_voltage_on=True), t_stop)                 # stopped with the car on
    t_off = t_stop + datetime.timedelta(minutes=2)
    await process(car, reading(0, 9419, DESTINATION, high_voltage_on=False), t_off)                 # switched off
    ds = await drives()
    assert ds[0].end_time is None                                                                  # not closed yet
    await process(car, reading(0, 9419, DESTINATION, high_voltage_on=False), T0 + datetime.timedelta(minutes=4 + 5, seconds=1))
    (d,) = await drives()
    assert d.end_time == t_off                                                                     # end = when it was switched off, not the last movement


async def test_if_it_is_switched_on_again_within_5_minutes_the_trip_continues(car) -> None:
    await process(car, reading(0, 9417, HOME, high_voltage_on=False), T0)
    await process(car, reading(50, 9418, DESTINATION, high_voltage_on=True), T0 + datetime.timedelta(minutes=3))
    await process(car, reading(0, 9418, DESTINATION, high_voltage_on=False), T0 + datetime.timedelta(minutes=4))      # stop with the car off
    await process(car, reading(0, 9418, DESTINATION, high_voltage_on=True), T0 + datetime.timedelta(minutes=5))       # switched on again
    await process(car, reading(45, 9419, HOME, high_voltage_on=True), T0 + datetime.timedelta(minutes=6))         # and carries on
    ds = await drives()
    assert len(ds) == 1 and ds[0].end_time is None
    assert td._HV_OFF_AT.get(VIN) is None


async def test_without_high_voltage_data_the_end_is_the_last_movement(car) -> None:
    await process(car, reading(0, 9417, HOME), T0)
    t = T0 + datetime.timedelta(minutes=4)
    await process(car, reading(50, 9419, DESTINATION), t)
    await process(car, reading(0, 9419, DESTINATION), t + datetime.timedelta(minutes=6))
    (d,) = await drives()
    assert d.end_time == t


# ───────────────────────── Open trip after a restart ─────────────────────────
async def test_an_open_trip_is_resumed_after_a_restart_and_closed(car) -> None:
    await process(car, reading(0, 9417, HOME), T0)
    t = T0 + datetime.timedelta(minutes=4)
    await process(car, reading(50, 9419, DESTINATION), t)
    for d in (td._ACTIVE_DRIVES, td._LAST_MOTION_TIME, td._LAST_IDLE, td._HV_OFF_AT):           # restart: memory is lost
        d.clear()
    await process(car, reading(0, 9419, DESTINATION), t + datetime.timedelta(minutes=6))
    ds = await drives()
    assert len(ds) == 1 and ds[0].end_time is not None and ds[0].distance_km == 2.0


async def test_a_very_old_open_trip_is_not_resumed(car) -> None:
    await process(car, reading(0, 9417, HOME), T0)
    await process(car, reading(50, 9419, DESTINATION), T0 + datetime.timedelta(minutes=4))
    for d in (td._ACTIVE_DRIVES, td._LAST_MOTION_TIME, td._LAST_IDLE, td._HV_OFF_AT, td._LAST_CLOSED):
        d.clear()
    await process(car, reading(50, 9440, DESTINATION), T0 + datetime.timedelta(hours=5))
    ds = await drives()
    assert len(ds) == 2 and ds[0].end_time is None                                              # the old one stays as it was; a new one starts


async def test_while_a_trip_is_open_the_poller_knows(car) -> None:
    assert TripDetector.has_open_trip(car) is False
    await process(car, reading(50, 9419, DESTINATION), T0)
    assert TripDetector.has_open_trip(car) is True
    await process(car, reading(0, 9425, DESTINATION), T0 + datetime.timedelta(minutes=6))
    assert TripDetector.has_open_trip(car) is False


# ───────────────────────── Levels at departure ─────────────────────────
async def test_the_point_lost_while_switching_on_counts_for_the_trip_not_for_the_parked_time(car) -> None:
    # Real case of 03/11/2026: charged to 80 % overnight; «ready» at 80 %, first reading in motion a minute later at 79 %
    await process(car, reading(0, 9569, HOME, soc=80.0), T0)                                           # parked, unplugged
    await process(car, reading(0, 9569, HOME, soc=80.0, state="ready"), T0 + datetime.timedelta(seconds=55))
    await process(car, reading(22, 9569, HOME, soc=79.0), T0 + datetime.timedelta(minutes=2))         # moving, odometer still
    await process(car, reading(20, 9572, DESTINATION, soc=78.0), T0 + datetime.timedelta(minutes=12))
    await process(car, reading(0, 9572, DESTINATION, soc=78.0), T0 + datetime.timedelta(minutes=13))
    await process(car, reading(0, 9572, DESTINATION, soc=78.0), T0 + datetime.timedelta(minutes=19))  # closes
    (d,) = await drives()
    assert d.start_estimated is False and d.start_time == T0 + datetime.timedelta(minutes=2)          # the time is not moved
    assert d.start_battery_pct == 80.0 and d.end_battery_pct == 78.0 and d.battery_used_pct == 2.0


async def test_an_old_stopped_reading_does_not_give_the_starting_levels(car) -> None:
    # Parked for hours: a point lost meanwhile is the parked drain, not the trip's
    await process(car, reading(0, 9569, HOME, soc=80.0), T0)
    await process(car, reading(22, 9569, HOME, soc=79.0), T0 + datetime.timedelta(hours=3))
    (d,) = await drives()
    assert d.start_battery_pct == 79.0


async def test_the_engine_charging_the_battery_does_not_cut_the_trip_but_plugging_in_ends_it(car) -> None:
    """04-11-2026: one trip became five because the car reported «charging» (engine → battery) twice on the road."""
    m = datetime.timedelta(minutes=1)
    await process(car, reading(0, 9605, HOME, soc=62), T0)
    await process(car, reading(27, 9605, HOME, soc=62), T0 + 2 * m)
    await process(car, reading(69, 9614, HOME, soc=56, is_charging=True, cable_connected=False), T0 + 8 * m)
    await process(car, reading(48, 9615, HOME, soc=56), T0 + 9 * m)
    assert TripDetector.has_open_trip(car) and len(await drives()) == 1
    await process(car, reading(0, 9631, DESTINATION, soc=50, is_charging=False, cable_connected=True), T0 + 35 * m)
    (d,) = await drives()
    assert d.end_time is not None and d.start_battery_pct == 62 and d.end_battery_pct == 50 and d.distance_km == 26


async def test_the_fuel_spent_is_litres_out_of_the_gauge_points_and_the_tank_size(car) -> None:
    """04-11-2026: a 400 km trip took the tank from 100 % to 43 % and the list said «57 L»; the car reports the tank in %."""
    m = datetime.timedelta(minutes=1)
    await process(car, reading(0, 9656, HOME, soc=60, fuel=100), T0)
    await process(car, reading(30, 9657, HOME, soc=60, fuel=100), T0 + 1 * m)
    await process(car, reading(0, 10056, DESTINATION, soc=56, fuel=43, high_voltage_on=False), T0 + 220 * m)
    (d,) = await drives()
    assert d.end_time is not None and (d.start_fuel, d.end_fuel) == (100, 43)
    assert d.fuel_used_l == 28.5                                 # 57 points of a 50 L tank (the sheet's default)


async def test_a_refuel_on_the_way_is_added_to_the_fuel_spent_and_shown_on_the_trip(car) -> None:
    """05-11-2026: 9 % at the start, 38 points put in at a pump stop, 21 % at the end; the list said no fuel was spent."""
    m = datetime.timedelta(minutes=1)
    await process(car, reading(0, 10317, HOME, soc=25, fuel=9), T0)
    await process(car, reading(138, 10321, HOME, soc=20, fuel=8), T0 + 3 * m)
    await process(car, reading(0, 10331, HOME, soc=23, fuel=5), T0 + 7 * m)                 # at the pump (under 5 min: the trip goes on)
    async with async_session_factory() as s:                                                # what the refuel detector records
        s.add(Refuel(vin=VIN, timestamp=T0 + 7 * m, odometer=10331, fuel_before_pct=5, fuel_after_pct=43, liters=26.6, detected=True))
        # the day's earlier refuel, edited by hand to noon of the same day, is at another odometer: not this trip's
        s.add(Refuel(vin=VIN, timestamp=T0 + 20 * m, odometer=9554, fuel_before_pct=27, fuel_after_pct=100, liters=51, detected=True))
        await s.commit()
    await process(car, reading(139, 10334, HOME, soc=23, fuel=43), T0 + 12 * m)
    await process(car, reading(0, 10494, DESTINATION, soc=21, fuel=21, high_voltage_on=False), T0 + 120 * m)
    (d,) = await drives()
    assert d.end_time is not None and (d.start_fuel, d.end_fuel) == (9, 21)
    assert d.fuel_refuelled_pct == 38 and d.fuel_used_l == 13.0                              # 9 + 38 − 21 = 26 points of 50 L


async def test_the_end_of_a_trip_moves_where_the_cloud_places_the_parked_car(car) -> None:
    """04-11-2026: the cloud kept the morning's position (work) for an hour after the car was parked at home."""
    m = datetime.timedelta(minutes=1)
    work = (37.5, -5.5)
    await process(car, reading(0, 9605, work), T0)
    await process(car, reading(40, 9610, work), T0 + 2 * m)
    await process(car, reading(0, 9631, work), T0 + 30 * m)                                   # stopped, position still stale
    await process(car, reading(0, 9631, work, high_voltage_on=False), T0 + 36 * m)            # closes the trip «at work»
    (d,) = await drives()
    assert d.end_time is not None and (d.end_lat, d.end_lon) == work and d.end_address == "Calle 37.500"
    await process(car, reading(0, 9631, HOME, high_voltage_on=False), T0 + 100 * m)           # the cloud wakes up: it is at home
    (d,) = await drives()
    assert (d.end_lat, d.end_lon) == HOME and d.end_address == "Calle 40.000"
    await process(car, reading(0, 9640, DESTINATION, high_voltage_on=False), T0 + 110 * m)    # another odometer: not this trip
    (d,) = await drives()
    assert (d.end_lat, d.end_lon) == HOME


# ───────────────────────── cars the cloud never places (two S800, 08-11-2026) ─────────────────────────
def blind(speed, odo, soc=72.0, fuel=27.0, **extra):
    """A reading without a position, as the S800 get them."""
    return {"speed_kmh": speed, "odometer_km": odo, "latitude": None, "longitude": None, "battery_level": soc, "fuel_level": fuel,
            "state": "driving" if speed > 2 else "parked", **extra}


async def test_a_car_without_a_position_still_gets_its_trips_measured_by_the_odometer(car) -> None:
    await process(car, blind(0, 9417.0, soc=62.0), T0 - datetime.timedelta(minutes=3))
    await process(car, blind(45, 9417.0, soc=62.0), T0)
    assert TripDetector.has_open_trip(VIN)
    await process(car, blind(60, 9430.0, soc=55.0), T0 + datetime.timedelta(minutes=12))
    await process(car, blind(0, 9441.0, soc=50.0, high_voltage_on=False), T0 + datetime.timedelta(minutes=14))
    (d,) = await drives()
    assert d.end_time is None
    await process(car, blind(0, 9441.0, soc=50.0, high_voltage_on=False), T0 + datetime.timedelta(minutes=20))
    (d,) = await drives()
    assert d.end_time == T0 + datetime.timedelta(minutes=14) and d.distance_km == 24.0 and d.battery_used_pct == 12.0
    assert d.start_lat is None and d.end_lat is None and d.start_address is None and d.end_address is None
    async with async_session_factory() as s:
        assert (await s.execute(select(DrivePosition).where(DrivePosition.drive_id == d.id))).scalars().all() == []


async def test_a_late_detected_trip_without_a_position_still_recovers_its_start_from_the_parked_reading(car) -> None:
    await process(car, blind(0, 9417.0, soc=62.0), T0 - datetime.timedelta(minutes=40))
    await process(car, blind(50, 9431.0, soc=58.0), T0)
    (d,) = await drives()
    assert d.start_estimated and d.start_odometer == 9417.0 and d.start_battery_pct == 62.0 and d.start_lat is None
    assert T0 - datetime.timedelta(minutes=40) <= d.start_time < T0
