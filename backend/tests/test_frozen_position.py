"""The cloud sometimes repeats one position for hours while the car drives; EVbro notices it and, when asked to, requests a fix.

An S900, 31-10-2026 20:00-21:58 and 03-11 22:30 to 04-11 15:06: the same point for up to 112 readings and 27 km per trip while the
realtime data (speed, odometer) was fresh. A manual «Localizar coche (GPS)» gave a fresh point every time it was sent.
"""
from __future__ import annotations

import datetime

import pytest

from app.config import settings
from app.services import poller as poller_mod

VIN = "LSJA0000000000071"
T0 = datetime.datetime(2026, 11, 4, 5, 41)


def reading(lat, lon, odo, state="driving", speed=None):
    return {"latitude": lat, "longitude": lon, "odometer_km": odo, "state": state, "speed_kmh": speed}


@pytest.fixture
def watcher(monkeypatch):
    poller = poller_mod.VehiclePoller()
    poller._position_watch = {}
    sent = []

    async def locate(vin, key, params, origin):
        sent.append((vin, key, origin))
        return {"success": True, "message": "ok", "reason": None}

    monkeypatch.setattr(poller, "execute_internal", locate)
    monkeypatch.setattr(settings, "AUTO_LOCATE_ENABLED", True)
    return poller, sent


async def test_the_same_point_with_the_odometer_advancing_is_frozen_and_asks_for_one_fix_per_gap(watcher, caplog) -> None:
    poller, sent = watcher
    with caplog.at_level("INFO", logger="app.services.poller"):
        await poller._watch_frozen_position(VIN, reading(37.0, -6.0, 9578), now=T0 - datetime.timedelta(hours=10))   # parked overnight
        await poller._watch_frozen_position(VIN, reading(37.0, -6.0, 9579), now=T0)
        await poller._watch_frozen_position(VIN, reading(37.0, -6.0, 9579), now=T0 + datetime.timedelta(minutes=2))
        assert sent == []                                                        # too early to tell: the hours parked do not count
        await poller._watch_frozen_position(VIN, reading(37.0, -6.0, 9580), now=T0 + datetime.timedelta(minutes=3))
        assert sent == [(VIN, "localizar_coche_gps", "auto_locate")]
        await poller._watch_frozen_position(VIN, reading(37.0, -6.0, 9581), now=T0 + datetime.timedelta(minutes=6))
        assert len(sent) == 1                                                    # one every five minutes at most
        await poller._watch_frozen_position(VIN, reading(37.0, -6.0, 9583), now=T0 + datetime.timedelta(minutes=9))
        assert len(sent) == 2
        await poller._watch_frozen_position(VIN, reading(37.01, -6.01, 9584), now=T0 + datetime.timedelta(minutes=10))
    assert [r.levelname for r in caplog.records if "Frozen position" in r.message] == ["WARNING"]
    moved = [r.message for r in caplog.records if "moves again" in r.message]
    assert len(moved) == 1 and "after 10 min and 6.0 km frozen, 60 s after the automatic locate" in moved[0]
    await poller._watch_frozen_position(VIN, reading(37.01, -6.01, 9584), now=T0 + datetime.timedelta(minutes=11))
    assert len(sent) == 2                                                        # a new point: the watch starts over


async def test_a_parked_car_keeps_its_point_without_being_frozen(watcher) -> None:
    poller, sent = watcher
    for minutes in (0, 10, 60, 600):
        await poller._watch_frozen_position(VIN, reading(37.0, -6.0, 9578), now=T0 + datetime.timedelta(minutes=minutes))
    assert sent == [] and poller._position_watch[VIN]["frozen"] is False
    await poller._watch_frozen_position(VIN, reading(None, None, 9578), now=T0)   # no position at all: nothing to watch


async def test_one_odometer_tick_after_a_night_parked_is_not_a_freeze(watcher, caplog) -> None:
    """An S700, 14-11-2026: asleep with 1364 km, the first reading of the morning said 1365 with the old point, and the next one, a
    minute later, had moved. It was logged as «1097 min frozen, 1.0 km»: the clock ran from the point, and 1 km was one tick."""
    poller, sent = watcher
    with caplog.at_level("WARNING", logger="app.services.poller"):
        await poller._watch_frozen_position(VIN, reading(37.0, -6.0, 1364), now=T0)
        await poller._watch_frozen_position(VIN, reading(37.0, -6.0, 1365), now=T0 + datetime.timedelta(minutes=1097))
        await poller._watch_frozen_position(VIN, reading(37.0, -6.0, 1365), now=T0 + datetime.timedelta(minutes=1101))
        await poller._watch_frozen_position(VIN, reading(37.01, -6.0, 1365), now=T0 + datetime.timedelta(minutes=1102))
    assert sent == [] and not any("Frozen position" in r.message for r in caplog.records)


async def test_with_the_experiment_off_the_freeze_is_only_logged(watcher, monkeypatch, caplog) -> None:
    poller, sent = watcher
    monkeypatch.setattr(settings, "AUTO_LOCATE_ENABLED", False)
    with caplog.at_level("WARNING", logger="app.services.poller"):
        await poller._watch_frozen_position(VIN, reading(37.0, -6.0, 9578), now=T0)
        await poller._watch_frozen_position(VIN, reading(37.0, -6.0, 9579), now=T0 + datetime.timedelta(minutes=1))
        await poller._watch_frozen_position(VIN, reading(37.0, -6.0, 9590), now=T0 + datetime.timedelta(minutes=5))
    assert sent == [] and any("Frozen position" in r.message for r in caplog.records)


async def test_without_a_saved_pin_the_locate_is_tried_once_and_noted(watcher, monkeypatch, caplog) -> None:
    poller, sent = watcher

    async def no_pin(vin, key, params, origin):
        sent.append(key)
        return {"success": False, "message": "Falta el PIN", "reason": "pin"}

    monkeypatch.setattr(poller, "execute_internal", no_pin)
    with caplog.at_level("INFO", logger="app.services.poller"):
        await poller._watch_frozen_position(VIN, reading(37.0, -6.0, 9578), now=T0)
        await poller._watch_frozen_position(VIN, reading(37.0, -6.0, 9580), now=T0 + datetime.timedelta(minutes=1))
        await poller._watch_frozen_position(VIN, reading(37.0, -6.0, 9590), now=T0 + datetime.timedelta(minutes=5))
        await poller._watch_frozen_position(VIN, reading(37.0, -6.0, 9600), now=T0 + datetime.timedelta(minutes=15))
    assert sent == ["localizar_coche_gps"]
    assert [r.levelname for r in caplog.records if "no PIN saved" in r.message] == ["INFO"]


async def test_a_refused_locate_is_logged_and_retried_after_the_gap(watcher, monkeypatch, caplog) -> None:
    poller, sent = watcher

    async def refused(vin, key, params, origin):
        sent.append(key)
        return {"success": False, "message": "Rechazado", "reason": "rejected"}

    monkeypatch.setattr(poller, "execute_internal", refused)
    with caplog.at_level("WARNING", logger="app.services.poller"):
        await poller._watch_frozen_position(VIN, reading(37.0, -6.0, 9578), now=T0)
        await poller._watch_frozen_position(VIN, reading(37.0, -6.0, 9579), now=T0 + datetime.timedelta(minutes=1))
        await poller._watch_frozen_position(VIN, reading(37.0, -6.0, 9590), now=T0 + datetime.timedelta(minutes=5))
        await poller._watch_frozen_position(VIN, reading(37.0, -6.0, 9591), now=T0 + datetime.timedelta(minutes=6))
        await poller._watch_frozen_position(VIN, reading(37.0, -6.0, 9592), now=T0 + datetime.timedelta(minutes=11))
    assert sent == ["localizar_coche_gps", "localizar_coche_gps"]
    assert any("not accepted: Rechazado" in r.message for r in caplog.records)


async def test_an_odometer_that_advanced_with_the_car_off_is_not_a_frozen_position(watcher, caplog) -> None:
    """16-11-2026: a car parked after a 98 km trip, asleep at its real position; an hour later its odometer read 4 km more (a short
    round trip between two hourly readings). It was taken for a frozen position and the car got three «Localizar» in three
    hours, each one waking it up."""
    poller, sent = watcher
    hour = datetime.timedelta(hours=1)
    with caplog.at_level("INFO", logger="app.services.poller"):
        await poller._watch_frozen_position(VIN, reading(37.0, -6.0, 22866, "parked"), now=T0)
        await poller._watch_frozen_position(VIN, reading(37.0, -6.0, 22866, "asleep"), now=T0 + datetime.timedelta(minutes=5))
        for n in range(1, 5):
            await poller._watch_frozen_position(VIN, reading(37.0, -6.0, 22870, "asleep"), now=T0 + n * hour)
    assert sent == [] and not [r for r in caplog.records if "Frozen position" in r.message]
    # the next morning it drives off with the position really stuck: the 4 km of the night do not count, the ones driven do
    await poller._watch_frozen_position(VIN, reading(37.0, -6.0, 22871), now=T0 + 10 * hour)
    await poller._watch_frozen_position(VIN, reading(37.0, -6.0, 22871), now=T0 + 10 * hour + datetime.timedelta(minutes=4))
    assert sent == []                                                            # 1 km driven: not enough yet
    await poller._watch_frozen_position(VIN, reading(37.0, -6.0, 22873), now=T0 + 10 * hour + datetime.timedelta(minutes=6))
    assert len(sent) == 1


async def test_a_frozen_position_gets_two_fixes_at_most(watcher) -> None:
    poller, sent = watcher
    await poller._watch_frozen_position(VIN, reading(37.0, -6.0, 100), now=T0)
    await poller._watch_frozen_position(VIN, reading(37.0, -6.0, 101), now=T0 + datetime.timedelta(minutes=1))
    for n in range(1, 8):                                                        # an hour of driving, and then parked where it stopped
        state = "driving" if n < 5 else "asleep"
        await poller._watch_frozen_position(VIN, reading(37.0, -6.0, 101 + 2 * min(n, 4), state), now=T0 + datetime.timedelta(minutes=10 * n))
    assert len(sent) == 2


async def test_a_car_that_stops_two_km_after_its_last_point_asks_once_where_it_stopped(watcher, caplog) -> None:
    """21-11-2026: the cloud only moved the point after each locate. The last one came mid-trip, the car drove 2 km more and
    parked at home, and the map kept showing it 2 km away."""
    poller, sent = watcher
    minute = datetime.timedelta(minutes=1)
    with caplog.at_level("INFO", logger="app.services.poller"):
        await poller._watch_frozen_position(VIN, reading(37.0, -6.0, 11085), now=T0)              # the answer to a locate
        await poller._watch_frozen_position(VIN, reading(37.0, -6.0, 11086), now=T0 + 2 * minute)
        await poller._watch_frozen_position(VIN, reading(37.0, -6.0, 11087), now=T0 + 4 * minute)
        assert sent == []                                                                      # 2 minutes: not frozen yet
        await poller._watch_frozen_position(VIN, reading(37.0, -6.0, 11087, "parked"), now=T0 + 5 * minute)
        assert sent == [(VIN, "localizar_coche_gps", "auto_locate")]
        for n, state in ((8, "parked"), (11, "parked"), (16, "asleep"), (60, "asleep")):
            await poller._watch_frozen_position(VIN, reading(37.0, -6.0, 11087, state), now=T0 + n * minute)
    assert len(sent) == 1                                                                      # once, while it is still awake
    assert any("stopped 2.0 km after its last point" in r.message for r in caplog.records)


async def test_a_car_that_stops_close_to_its_last_point_is_left_alone(watcher) -> None:
    poller, sent = watcher
    minute = datetime.timedelta(minutes=1)
    await poller._watch_frozen_position(VIN, reading(37.0, -6.0, 500), now=T0)
    await poller._watch_frozen_position(VIN, reading(37.0, -6.0, 501), now=T0 + minute)        # one tick: maybe a few metres
    await poller._watch_frozen_position(VIN, reading(37.0, -6.0, 501, "parked"), now=T0 + 2 * minute)
    assert sent == []


async def test_a_car_that_drives_on_after_its_last_fix_asks_where_it_stopped_even_with_one_odometer_tick(watcher, caplog) -> None:
    """27-11-2026: the last fix came on the motorway at 59 km/h; the car drove five more minutes, the odometer ticked once (under
    the 2 km of the rule above) and it parked: the trip ended «on the motorway»."""
    poller, sent = watcher
    at = lambda m, s=0: T0 + datetime.timedelta(minutes=m, seconds=s)                 # noqa: E731
    with caplog.at_level("INFO", logger="app.services.poller"):
        for when, odo, state, speed in ((at(1, 7), 11385, "driving", 59), (at(1, 43), 11385, "driving", 30), (at(2, 50), 11386, "driving", 21),
                                        (at(3, 54), 11386, "driving", 26), (at(4, 4), 11386, "driving", 9), (at(4, 56), 11386, "driving", 2)):
            await poller._watch_frozen_position(VIN, reading(36.2, -5.4, odo, state, speed), now=when)
        assert sent == []                                                              # still driving: the freeze rule decides
        await poller._watch_frozen_position(VIN, reading(36.2, -5.4, 11386, "parked", 0), now=at(6, 7))
        assert sent == [(VIN, "localizar_coche_gps", "auto_locate")]
        for when in (at(8, 46), at(11, 13), at(57, 44)):
            await poller._watch_frozen_position(VIN, reading(36.2, -5.4, 11386, "parked", 0), now=when)
    assert len(sent) == 1                                                              # once
    assert any("stopped about 1." in r.message for r in caplog.records)


async def test_a_car_whose_point_moves_on_every_reading_is_not_asked_when_it_stops(watcher) -> None:
    poller, sent = watcher
    at = lambda s: T0 + datetime.timedelta(seconds=s)                                  # noqa: E731
    await poller._watch_frozen_position(VIN, reading(36.2000, -5.4000, 500, "driving", 60), now=at(0))
    await poller._watch_frozen_position(VIN, reading(36.2030, -5.4030, 500, "driving", 30), now=at(30))    # a new point: starts over
    await poller._watch_frozen_position(VIN, reading(36.2030, -5.4030, 501, "driving", 10), now=at(50))    # 110 m on it, one tick
    await poller._watch_frozen_position(VIN, reading(36.2030, -5.4030, 501, "parked", 0), now=at(80))
    assert sent == []
