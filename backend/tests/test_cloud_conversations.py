"""Talking to the Ebro cloud without tripping over ourselves (failures seen on the production server up to 22-11-2026):

* every conversation signs in, and a sign-in of an account makes the token of the previous one worthless: a read and an order at
  once gave `A00000` «token caducado» to one of them → one conversation per account at a time;
* a busy car (`A00082`) takes the order a few seconds later → it is sent again instead of handing over an error;
* a read that never left the server (no DNS right after a restart) → once more;
* paused at the charge target, a car whose live channel listens is read seldom;
* the server counts what it asks of the cloud per day.
"""
from __future__ import annotations

import asyncio
import datetime
import threading
import time

import httpx
import pytest
from freezegun import freeze_time
from sqlalchemy import select

from app.core import commands
from app.core.context import CoreCtx
from app.core.errors import CommandError
from app.db.models import AppUser, CloudUsage, CommandAudit, EbroAccount, Vehicle
from app.db.session import async_session_factory
from app.main import app
from app.security import auth, passwords
from app.services import cloud_usage, poller_commands
from app.services import poller as poller_module
from app.services.car_events import CarEventHub
from app.services.poller import VehiclePoller

VIN, OTHER = "LSJA0000000000061", "LSJA0000000000062"
BUSY = CommandError("coche ocupado", code="A00082")
OVERTAKEN = CommandError("token caducado", code="A00000", reason="reauth")
NO_SESSION = CommandError("Sesión caducada", reason="reauth")


class _Req:
    client = type("C", (), {"host": "127.0.0.1"})()
    headers = {"user-agent": "tests"}


class Cloud:
    """`commands.send` that fails with the queued errors first, then accepts."""

    def __init__(self, *errors):
        self.errors, self.calls = list(errors), []

    def __call__(self, ctx, cmd_key, emit=None, params=None):
        self.calls.append(cmd_key)
        if self.errors:
            raise self.errors.pop(0)
        return f"{cmd_key}: HTTP 200 A00079"


@pytest.fixture
async def client(db, data_dir, monkeypatch):
    monkeypatch.setattr(poller_commands, "BUSY_WAIT_SECONDS", 0.0)
    monkeypatch.setattr(VehiclePoller, "_contexts", {}, raising=False)
    cloud_usage._pending.clear()
    cloud_usage._last_flush.clear()

    async def no_poll(self, vin, force=False):
        return None
    monkeypatch.setattr(VehiclePoller, "poll_vehicle", no_poll)
    recovered = []

    async def recover(self, vin):
        recovered.append(vin)
        return recover.works
    recover.works = True
    monkeypatch.setattr(VehiclePoller, "_recover_session", recover)
    async with async_session_factory() as s:
        user = AppUser(id=1, role="manager", username="juan", password_hash=passwords.hash_password("una-contraseña-larga-1"))
        s.add_all([user, Vehicle(owner_id=1, vin=VIN, name="Ebro"), EbroAccount(phone="600000000", password="x", pin="1234", is_active=True, user_id=1)])
        await s.commit()
        token = await auth.create_session(s, user, _Req())
    c = httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://testserver")
    c.cookies.set(auth.COOKIE_NAME, token)
    c.recovered, c.recover = recovered, recover
    yield c
    await c.aclose()
    cloud_usage._pending.clear()


async def order(client, command="clima_on"):
    return await client.post(f"/api/vehicles/{VIN}/commands", json={"command": command})


async def audit() -> list:
    async with async_session_factory() as s:
        return [(r.command, r.success, r.code) for r in (await s.execute(select(CommandAudit).order_by(CommandAudit.id))).scalars()]


# ───────────────────────── orders ─────────────────────────
async def test_a_busy_car_gets_the_order_again_and_the_person_sees_one_success(client, monkeypatch) -> None:
    cloud = Cloud(BUSY, BUSY)
    monkeypatch.setattr(commands, "send", cloud)
    r = await order(client)
    assert r.status_code == 200 and r.json()["success"] is True
    assert cloud.calls == ["clima_on"] * 3 and await audit() == [("clima_on", True, None)]


async def test_a_car_that_stays_busy_is_said_so_after_the_tries(client, monkeypatch) -> None:
    cloud = Cloud(*[BUSY] * 10)
    monkeypatch.setattr(commands, "send", cloud)
    r = await order(client)
    assert r.json()["success"] is False and "ocupado" in r.json()["detail"]
    assert len(cloud.calls) == 1 + poller_commands.BUSY_RETRIES and await audit() == [("clima_on", False, "A00082")]


async def test_a_token_overtaken_by_another_sign_in_is_tried_once_more(client, monkeypatch) -> None:
    cloud = Cloud(OVERTAKEN)
    monkeypatch.setattr(commands, "send", cloud)
    assert (await order(client)).json()["success"] is True
    assert len(cloud.calls) == 2 and client.recovered == []                 # the next send signs in by itself: no password needed
    cloud = Cloud(OVERTAKEN, OVERTAKEN, OVERTAKEN)
    monkeypatch.setattr(commands, "send", cloud)
    r = await order(client)
    assert r.status_code == 424 and r.json()["reason"] == "reauth" and len(cloud.calls) == 2      # once more, not for ever


async def test_a_dead_session_is_reopened_with_the_stored_password_before_giving_up(client, monkeypatch) -> None:
    cloud = Cloud(NO_SESSION)
    monkeypatch.setattr(commands, "send", cloud)
    assert (await order(client)).json()["success"] is True
    assert client.recovered == [VIN] and len(cloud.calls) == 2
    client.recover.works = False                                             # the daily attempt is spent, or the password is refused
    cloud = Cloud(NO_SESSION, NO_SESSION)
    monkeypatch.setattr(commands, "send", cloud)
    r = await order(client)
    assert r.status_code == 424 and len(cloud.calls) == 1


async def test_the_conversations_of_one_account_never_overlap_and_other_accounts_do_not_wait(db, monkeypatch) -> None:
    poller = VehiclePoller()
    poller._contexts = {VIN: CoreCtx(vin=VIN, token_path="/tmp/cuenta-1"), OTHER: CoreCtx(vin=OTHER, token_path="/tmp/cuenta-2")}
    assert poller.cloud_lock(VIN) is poller.cloud_lock(VIN) and poller.cloud_lock(VIN) is not poller.cloud_lock(OTHER)
    inside, most, guard = {"n": 0}, {"n": 0}, threading.Lock()

    def talk(ctx, cmd_key, emit=None, params=None):
        with guard:
            inside["n"] += 1
            most["n"] = max(most["n"], inside["n"])
        time.sleep(0.03)                                                     # the talk with the cloud, in the executor thread
        with guard:
            inside["n"] -= 1
        return "ok"
    monkeypatch.setattr(commands, "send", talk)
    ctx = poller._contexts[VIN]
    results = await asyncio.gather(*(poller._send_command(VIN, ctx, "clima_on", None, "test") for _ in range(4)))
    assert all(r[0] for r in results) and most["n"] == 1


# ───────────────────────── reads ─────────────────────────
@pytest.fixture
def reader(db, monkeypatch):
    monkeypatch.setattr(poller_module, "UNSENT_RETRY_SECONDS", 0.0)
    cloud_usage._pending.clear()
    poller = VehiclePoller()
    poller._contexts = {VIN: CoreCtx(vin=VIN)}
    poller._inflight, poller._last_poll_times, poller._latest_snapshots, poller._session_paused = {}, {}, {}, {}
    yield poller
    cloud_usage._pending.clear()


def probes(*answers):
    """`probe_once` that gives the queued answers in turn: a dict of raw data, or an error text."""
    left, calls = list(answers), []

    def fake(ctx, publish, force=False, on_data=None):
        calls.append(force)
        answer = left.pop(0)
        if isinstance(answer, dict):
            on_data(answer)
            return {"ok": True}
        return {"ok": False, "reason": "exception", "error": answer}
    fake.calls = calls
    return fake


def counted() -> dict:
    return {kind: n for (_day, kind), n in cloud_usage._pending.items()}


async def test_a_read_that_never_left_the_server_is_tried_once_more(reader, monkeypatch) -> None:
    dns = "HTTPSConnectionPool(host='x', port=443): Max retries exceeded (Caused by NameResolutionError('Failed to resolve'))"
    fake = probes(dns, {"dumpEnergy": "61"})
    monkeypatch.setattr(poller_module, "probe_once", fake)
    assert (await reader.poll_vehicle(VIN, force=True))["battery_level"] == 61.0
    assert len(fake.calls) == 2 and counted() == {"read_failed": 1, "read_ok": 1}
    fake = probes(dns, dns, {"dumpEnergy": "61"})                            # still no network: once more, not for ever
    monkeypatch.setattr(poller_module, "probe_once", fake)
    reader._last_poll_times.clear()
    assert await reader.poll_vehicle(VIN, force=True) is None and len(fake.calls) == 2


async def test_a_read_the_cloud_may_have_received_is_not_repeated(reader, monkeypatch) -> None:
    fake = probes("HTTPSConnectionPool(host='x', port=443): Read timed out. (read timeout=20)", {"dumpEnergy": "61"})
    monkeypatch.setattr(poller_module, "probe_once", fake)
    assert await reader.poll_vehicle(VIN, force=True) is None
    assert len(fake.calls) == 1 and counted() == {"read_failed": 1}


async def test_paused_at_the_target_a_car_heard_on_its_live_channel_is_read_seldom(reader, monkeypatch) -> None:
    hub = CarEventHub.get_instance()
    service = type("S", (), {"vin": VIN, "state": "subscribed", "messages": 0})()
    monkeypatch.setattr(hub, "_services", {1: service})
    assert reader.hold_poll_seconds(VIN) == poller_module.HOLD_POLL_SECONDS          # subscribed but never heard: proves nothing
    service.messages = 3
    assert reader.hold_poll_seconds(VIN) == poller_module.HOLD_POLL_LIVE_SECONDS
    service.state = "stopped"                                                        # another app took the channel
    assert reader.hold_poll_seconds(VIN) == poller_module.HOLD_POLL_SECONDS
    assert reader.hold_poll_seconds(OTHER) == poller_module.HOLD_POLL_SECONDS         # a car nobody listens to


# ───────────────────────── the count for the console ─────────────────────────
async def test_the_usage_is_added_per_day_with_the_orders_of_the_audit_log(db) -> None:
    cloud_usage._pending.clear()
    cloud_usage._last_flush.clear()
    with freeze_time("2026-11-21 23:50:00"):
        for kind in ("read_ok", "read_ok", "read_empty", "read_failed", "query", "no-such-kind"):
            cloud_usage.count(kind)
        await cloud_usage.flush(force=True)
    with freeze_time("2026-11-22 10:00:00"):
        cloud_usage.count("read_ok")
        await cloud_usage.flush(force=True)
        cloud_usage.count("read_ok")                                        # not written yet: it still counts
        async with async_session_factory() as s:
            s.add(Vehicle(owner_id=None, vin=VIN, name="Ebro"))
            await s.flush()
            s.add_all([CommandAudit(vin=VIN, command="clima_on", success=True, timestamp=datetime.datetime(2026, 11, 22, 9, 0)),
                       CommandAudit(vin=VIN, command="clima_on", success=False, code="A00082", timestamp=datetime.datetime(2026, 11, 22, 9, 1)),
                       CommandAudit(vin=VIN, command="bloquear", success=True, timestamp=datetime.datetime(2026, 10, 1, 9, 0))])
            await s.commit()
            rows = await cloud_usage.daily(s, days=14)
            stored = {(str(r.day), r.kind): r.count for r in (await s.execute(select(CloudUsage))).scalars()}
    assert stored == {("2026-11-21", "read_ok"): 2, ("2026-11-21", "read_empty"): 1, ("2026-11-21", "read_failed"): 1,
                      ("2026-11-21", "query"): 1, ("2026-11-22", "read_ok"): 1}
    assert [r["day"] for r in rows] == ["2026-11-22", "2026-11-21"]                 # newest first; the order of October is out of range
    today, yesterday = rows
    assert (today["reads"], today["read_ok"], today["commands_ok"], today["commands_failed"]) == (2, 2, 1, 1)
    assert (yesterday["reads"], yesterday["read_failed"], yesterday["query"], yesterday["requests"]) == (4, 1, 1, 4 * 4 + 2)
    cloud_usage._pending.clear()
