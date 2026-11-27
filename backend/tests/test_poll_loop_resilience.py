"""The polling loop must survive a failure of any periodic job, and `/health/ready` must say when it has stopped.

27-11-2026 (operations audit): the charge manager's tick and four more jobs ran outside the loop's `try`, so one exception (a
database outage at that moment) ended the reads of every car for good, while `/health` kept answering «healthy».
"""
from __future__ import annotations

import asyncio
import datetime

import httpx

from app.main import app
from app.services import poller as poller_mod
from app.services.charge_manager import ChargeManager
from app.services.poller import VehiclePoller
from app.timeutil import utcnow


async def test_a_failing_periodic_job_does_not_end_the_polling_loop(db, monkeypatch, caplog) -> None:
    poller = VehiclePoller()
    passes = []

    async def broken_tick(self):
        raise RuntimeError("database went away")

    async def counting_climate(self):
        passes.append(1)

    async def fake_sleep(seconds):
        if len(passes) >= 2:
            poller._running = False

    monkeypatch.setattr(ChargeManager, "tick", broken_tick)
    monkeypatch.setattr(VehiclePoller, "_run_climate_schedules", counting_climate)
    monkeypatch.setattr(poller_mod.asyncio, "sleep", fake_sleep)
    VehiclePoller.loop_heartbeat = None
    poller._running = True
    with caplog.at_level("ERROR", logger="app.services.poller"):
        await asyncio.wait_for(poller._poller_loop(), timeout=20)
    assert len(passes) == 2                                                     # the jobs after the broken one still ran, twice
    assert sum("Periodic job 'charge manager' failed" in r.message for r in caplog.records) == 2
    assert all(r.exc_info for r in caplog.records if "Periodic job" in r.message)        # with its trace
    assert VehiclePoller.loop_heartbeat is not None


async def test_readiness_says_when_the_reads_have_stopped(db, monkeypatch) -> None:
    poller = VehiclePoller.get_instance()
    monkeypatch.setattr(poller, "_running", True)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://testserver") as client:
        monkeypatch.setattr(VehiclePoller, "loop_heartbeat", utcnow())
        r = await client.get("/health/ready")
        assert r.status_code == 200 and r.json() == {"status": "ok", "checks": {"database": True, "polling": True}}
        monkeypatch.setattr(VehiclePoller, "loop_heartbeat", utcnow() - datetime.timedelta(minutes=10))
        r = await client.get("/health/ready")
        assert r.status_code == 503 and r.json()["checks"] == {"database": True, "polling": False}
        assert (await client.get("/health")).status_code == 200                  # liveness stays up: Docker must not restart it
