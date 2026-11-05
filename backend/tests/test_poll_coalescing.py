"""One read of the cloud per car at a time: requests that overlap or follow each other closely share one answer.

On 04-11-2026 a push from the car arrived 2 s after the scheduled read; both polled the cloud, got the same snapshot and ran
the detectors twice: the trip and the charge were duplicated. And a car whose reads failed was retried every 10 s.
"""
from __future__ import annotations

import asyncio
import datetime
import time

import pytest
from freezegun import freeze_time

from app.core.context import CoreCtx
from app.services import poller as poller_module
from app.services.poller import VehiclePoller

VIN = "LSJA0000000000009"


@pytest.fixture
def fresh_poller():
    poller = VehiclePoller()
    poller._contexts = {VIN: CoreCtx(vin=VIN)}
    poller._inflight = {}
    poller._last_poll_times = {}
    poller._latest_snapshots = {}
    return poller


def probe_that(calls, soc="55", ok=True, delay=0.0):
    def fake_probe(ctx, publish, force=False, on_data=None):
        calls.append(force)
        time.sleep(delay)                                   # it runs in the executor thread, like the real one
        if ok:
            on_data({"dumpEnergy": soc})
            return {"ok": True}
        return {"ok": False, "reason": "no_usertoken"}
    return fake_probe


async def test_two_reads_at_once_ask_the_cloud_once_and_share_the_answer(db, fresh_poller, monkeypatch) -> None:
    calls = []
    monkeypatch.setattr(poller_module, "probe_once", probe_that(calls, delay=0.05))
    first, second = await asyncio.gather(fresh_poller.poll_vehicle(VIN, force=True), fresh_poller.poll_vehicle(VIN, force=True))
    assert calls == [True]
    assert first is second and first["battery_level"] == 55.0


async def test_a_read_right_after_another_reuses_its_answer_until_the_window_passes(db, fresh_poller, monkeypatch) -> None:
    calls = []
    monkeypatch.setattr(poller_module, "probe_once", probe_that(calls))
    first = await fresh_poller.poll_vehicle(VIN, force=True)
    again = await fresh_poller.poll_vehicle(VIN, force=True)
    assert calls == [True] and again is first
    with freeze_time(datetime.datetime.utcnow() + datetime.timedelta(seconds=poller_module.POLL_COALESCE_SECONDS + 1)):
        monkeypatch.setattr(poller_module, "probe_once", probe_that(calls, soc="54"))
        later = await fresh_poller.poll_vehicle(VIN, force=True)
    assert calls == [True, True] and later["battery_level"] == 54.0


async def test_a_read_that_brings_nothing_still_counts_as_an_attempt(db, fresh_poller, monkeypatch) -> None:
    """The loop retries at the car's adaptive interval, not at every turn (a car with an expired session was asked every 10 s)."""
    calls = []
    monkeypatch.setattr(poller_module, "probe_once", probe_that(calls, ok=False))
    assert await fresh_poller.poll_vehicle(VIN, force=True) is None
    assert VIN in fresh_poller._last_poll_times and fresh_poller._inflight == {}
