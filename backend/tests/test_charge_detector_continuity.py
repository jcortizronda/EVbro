"""One plug-in is one charge: a server restart or a short pause at the charger must not split it (seen on 01-11-2026)."""
from __future__ import annotations

import datetime

import pytest
from freezegun import freeze_time
from sqlalchemy import select

from app.db.models import ChargePlan, ChargePoint, ChargingSession, TelemetrySnapshot, Vehicle
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


async def incomplete_reading(at, plug=True):
    """A read where only the location or trips endpoint answered (06-11-2026): no battery, no power, `is_charging` by default."""
    with freeze_time(at):
        async with async_session_factory() as s:
            v = (await s.execute(select(Vehicle).where(Vehicle.vin == VIN))).scalar_one()
            res = await ChargeDetector.process_snapshot(s, v, {"is_charging": False, "cable_connected": plug, "battery_level": None,
                                                              "charge_power_kw": None, "latitude": None, "longitude": None})
            await s.commit()
            return res


@pytest.mark.parametrize("plug", [True, False])
async def test_a_read_without_charge_data_does_not_end_the_charge(car, plug) -> None:
    await reading(D(2026, 11, 6, 1, 30), 41, True)
    assert await incomplete_reading(D(2026, 11, 6, 1, 44), plug=plug) is None
    await reading(D(2026, 11, 6, 1, 46), 43, True)
    await reading(D(2026, 11, 6, 2, 0), 45, False, kw=0.0)
    (charge,) = await sessions()
    assert charge.start_battery_pct == 41 and charge.end_battery_pct == 45 and charge.kwh_added == pytest.approx(0.8)


async def test_a_read_without_charge_data_after_a_restart_keeps_the_session_open(car) -> None:
    await reading(D(2026, 11, 6, 1, 30), 41, True)
    restart()
    await incomplete_reading(D(2026, 11, 6, 1, 32))
    await reading(D(2026, 11, 6, 1, 34), 42, True)
    (charge,) = await sessions()
    assert charge.end_time is None and cd._ACTIVE_CHARGES[VIN] == charge.id


async def test_the_poller_drops_a_read_without_charge_data_during_a_charge(car, monkeypatch) -> None:
    from app.services import poller as poller_mod
    sent = []

    def record(name):
        async def notify(cls, *a, **k):
            sent.append(name)
        return classmethod(notify)

    for name in ("notify_charging_started", "notify_charging_completed", "notify_charge_interrupted"):
        monkeypatch.setattr(poller_mod.NotificationService, name, record(name))
    answers = [{"chargeGunState": "1", "chargeState": "1", "dumpEnergy": "41", "totalVoltage": "330", "totalCurrent": "-8",
                "lat": "37.0", "lon": "-6.0"},
               {"chargeGunState": "1", "appointmentTravelSetVOS": []}]

    def probe(ctx, publish, force=False, on_data=None):
        on_data(answers.pop(0))
        return {"ok": True}

    poller = poller_mod.VehiclePoller()
    monkeypatch.setattr(poller_mod, "probe_once", probe)
    monkeypatch.setattr(poller, "_get_or_create_ctx", lambda vin: object())
    monkeypatch.setattr(poller_mod.VehiclePoller, "_latest_snapshots", {})
    monkeypatch.setattr(poller_mod.VehiclePoller, "_last_charging_by_vin", {VIN: False}, raising=False)
    first = await poller._poll_vehicle(VIN)
    second = await poller._poll_vehicle(VIN)
    assert first["state"] == "charging" and second is first and poller.get_latest(VIN)["state"] == "charging"
    (charge,) = await sessions()
    assert charge.end_time is None and sent == ["notify_charging_started"]


async def plugged_reading(at, soc, odometer=1000.0):
    async with async_session_factory() as s:
        s.add(TelemetrySnapshot(vin=VIN, timestamp=at, battery_level=soc, electric_range_km=30.0, odometer_km=odometer,
                                is_charging=False, cable_connected=True))
        await s.commit()


async def first_charge_reading(at, soc, odometer=1000.0):
    with freeze_time(at):
        async with async_session_factory() as s:
            v = (await s.execute(select(Vehicle).where(Vehicle.vin == VIN))).scalar_one()
            await ChargeDetector.process_snapshot(s, v, {"is_charging": True, "cable_connected": True, "battery_level": soc, "odometer_km": odometer,
                                                         "electric_range_km": 56.0, "charge_power_kw": 2.8, "latitude": 37.0, "longitude": -6.0})
            await s.commit()


async def test_a_charge_seen_late_starts_where_the_schedule_opened_it(car) -> None:
    async with async_session_factory() as s:
        s.add(ChargePlan(vin=VIN, target_pct=100, mode="scheduled", sched_enabled=True, sched_start_min=0, sched_duration_min=480))
        await s.commit()
    await plugged_reading(D(2026, 11, 5, 20, 34), 20)
    await first_charge_reading(D(2026, 11, 6, 1, 31), 41)
    (charge,) = await sessions()
    assert charge.start_time == D(2026, 11, 5, 23, 0) and charge.start_battery_pct == 20 and charge.start_range_km == 30.0


async def test_a_charge_seen_late_without_a_schedule_keeps_the_detection_time(car) -> None:
    await plugged_reading(D(2026, 11, 5, 20, 34), 20)
    await first_charge_reading(D(2026, 11, 6, 1, 31), 41)
    (charge,) = await sessions()
    assert charge.start_time == D(2026, 11, 6, 1, 31) and charge.start_battery_pct == 20


@pytest.mark.parametrize("before, odometer", [(40.5, 1000.0), (20.0, 1012.0)])
async def test_a_charge_only_looks_back_if_the_car_stayed_put_and_the_battery_rose(car, before, odometer) -> None:
    await plugged_reading(D(2026, 11, 5, 20, 34), before, odometer=odometer)
    await first_charge_reading(D(2026, 11, 6, 1, 31), 41)
    (charge,) = await sessions()
    assert charge.start_time == D(2026, 11, 6, 1, 31) and charge.start_battery_pct == 41


async def test_a_new_charge_does_not_claim_the_rise_of_the_previous_session(car) -> None:
    await plugged_reading(D(2026, 11, 5, 20, 0), 20)
    async with async_session_factory() as s:
        s.add(ChargingSession(vin=VIN, start_time=D(2026, 11, 5, 21, 0), end_time=D(2026, 11, 5, 23, 0), start_battery_pct=20, end_battery_pct=40))
        await s.commit()
    await first_charge_reading(D(2026, 11, 6, 1, 31), 41)
    assert (await sessions())[-1].start_battery_pct == 41


async def alerts_for_a_stop(monkeypatch, holds: bool) -> list:
    from app.services import poller as poller_mod
    sent = []

    def record(name):
        async def notify(cls, *a, **k):
            sent.append(name)
        return classmethod(notify)

    for name in ("notify_charging_completed", "notify_charge_interrupted"):
        monkeypatch.setattr(poller_mod.NotificationService, name, record(name))
    monkeypatch.setattr(poller_mod.ChargeManager.get_instance(), "_holds", {VIN: "target" if holds else None})
    monkeypatch.setattr(poller_mod.VehiclePoller, "_last_charging_by_vin", {VIN: True})
    poller = poller_mod.VehiclePoller()
    async with async_session_factory() as s:
        v = (await s.execute(select(Vehicle).where(Vehicle.vin == VIN))).scalar_one()
        await poller._check_and_dispatch_alerts(s, v, {"battery_level": 80.0, "is_charging": False, "cable_connected": True})
    return sent


async def test_reaching_the_target_is_a_completed_charge(car, monkeypatch) -> None:
    assert await alerts_for_a_stop(monkeypatch, holds=True) == ["notify_charging_completed"]


async def test_a_stop_evbro_did_not_ask_for_is_an_interruption(car, monkeypatch) -> None:
    assert await alerts_for_a_stop(monkeypatch, holds=False) == ["notify_charge_interrupted"]
