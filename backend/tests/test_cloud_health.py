"""services/cloud_health.py: the Ebro cloud is taken to be down when the reads already being made fail for a while with none
getting through, and everybody connected is told once."""
from __future__ import annotations

import datetime

import pytest

from app.services import cloud_health
from app.services.poller import VehiclePoller

T0 = datetime.datetime(2026, 11, 26, 18, 0)


def at(minutes: float) -> datetime.datetime:
    return T0 + datetime.timedelta(minutes=minutes)


@pytest.fixture(autouse=True)
def _clean():
    cloud_health.reset()
    yield
    cloud_health.reset()


def test_one_failed_read_or_one_car_is_not_an_outage() -> None:
    assert cloud_health.unanswered("A", 10, at(0)) is None
    assert cloud_health.unanswered("A", 10, at(15)) is None            # long enough, but a single car out of ten
    assert cloud_health.unanswered("B", 10, at(16)) is None
    assert cloud_health.status() == {"down": False, "since": None}


def test_several_cars_failing_for_a_while_with_no_answer_in_between_is() -> None:
    assert cloud_health.unanswered("A", 10, at(0)) is None
    assert cloud_health.unanswered("B", 10, at(2)) is None
    assert cloud_health.unanswered("C", 10, at(4)) is None             # three cars already, but only four minutes
    assert cloud_health.unanswered("A", 10, at(11)) == "down"
    assert cloud_health.status() == {"down": True, "since": "2026-11-26T18:00:00Z"}      # since the first failure
    assert cloud_health.unanswered("D", 10, at(12)) is None            # said once
    assert cloud_health.answered(at(20)) == "up"
    assert cloud_health.status()["down"] is False and cloud_health.answered(at(21)) is None


def test_a_read_that_gets_through_starts_the_count_again() -> None:
    for i, vin in enumerate("ABC"):
        cloud_health.unanswered(vin, 10, at(i))
    assert cloud_health.answered(at(9)) is None                        # one answer: the cloud is there
    assert cloud_health.unanswered("A", 10, at(11)) is None
    assert cloud_health.unanswered("B", 10, at(12)) is None and cloud_health.unanswered("C", 10, at(13)) is None
    assert cloud_health.unanswered("C", 10, at(20)) is None            # nine minutes since the new first failure
    assert cloud_health.unanswered("C", 10, at(21.5)) == "down"


def test_a_server_with_one_car_needs_only_that_car() -> None:
    assert cloud_health.unanswered("A", 1, at(0)) is None
    assert cloud_health.unanswered("A", 1, at(30)) == "down"


def test_every_connected_page_is_told_when_it_goes_down_and_when_it_comes_back() -> None:
    poller, seen = VehiclePoller(), []
    poller.register_ws_subscriber(seen.append)
    try:
        poller._cloud_changed(None)
        assert seen == []
        cloud_health.unanswered("A", 1, at(0))
        poller._cloud_changed(cloud_health.unanswered("A", 1, at(30)))
        assert seen == [{"type": "cloud_status", "down": True, "since": "2026-11-26T18:00:00Z"}]
        poller._cloud_changed(cloud_health.answered())
        assert seen[-1] == {"type": "cloud_status", "down": False, "since": None}
    finally:
        poller.unregister_ws_subscriber(seen.append)
