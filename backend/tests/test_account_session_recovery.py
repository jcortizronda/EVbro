"""An Ebro session the cloud no longer accepts is reopened with the stored password; cars without a connected account are left alone.

On 07-11-2026 a car went dark for 36 hours: the refresh token was rejected and nobody told the manager. Another car kept being
polled every hour with the token of an account its manager had disconnected.
"""
from __future__ import annotations

import datetime
import json
import os

import pytest
from sqlalchemy import select

from app.config import settings
from app.core.context import CoreCtx
from app.db.models import EbroAccount, Vehicle
from app.db.session import async_session_factory
from app.services import poller as poller_module
from app.services.ebro_sessions import account_session_path
from app.services.notification_service import NotificationService
from app.services.poller import VehiclePoller, pollable

VIN = "LSJA0000000000031"
OTHER = "LSJA0000000000032"
TOKEN = {"access_token": "fresh-access", "refresh_token": "fresh-refresh", "expires_in": 7200}


@pytest.fixture
async def world(db, data_dir, monkeypatch):
    async with async_session_factory() as s:
        s.add(Vehicle(owner_id=1, vin=VIN, name="Ebro"))
        s.add(Vehicle(owner_id=1, vin=OTHER, name="Segundo"))
        s.add(EbroAccount(id=7, user_id=1, phone="600000001", password="secreta", area_code="34", is_active=True,
                          last_login=datetime.datetime(2026, 11, 1, 10, 0)))
        await s.commit()
    poller = VehiclePoller()
    poller._contexts = {VIN: CoreCtx(vin=VIN), OTHER: CoreCtx(vin=OTHER)}
    poller._inflight, poller._last_poll_times, poller._latest_snapshots = {}, {}, {}
    poller._relogin_attempt_at, poller._session_lost = {}, {}
    monkeypatch.setattr(poller, "_get_or_create_ctx", lambda vin: poller._contexts[vin])
    monkeypatch.setattr(poller_module.VehiclePoller, "_last_charging_by_vin", {}, raising=False)
    alerts = []

    async def alert(cls, user_id, names):
        alerts.append((user_id, sorted(names)))

    monkeypatch.setattr(NotificationService, "notify_account_session_lost", classmethod(alert))

    async def no_restart(self, owner_id, poller_):
        return None

    monkeypatch.setattr(poller_module.CarEventHub, "restart_owner", no_restart)
    return poller, alerts


def probe_until_relogin(logins):
    """The cloud refuses the stored session until a new login has happened; then it answers with data."""
    def probe(ctx, publish, force=False, on_data=None):
        if not logins:
            return {"ok": False, "reason": "no_usertoken"}
        on_data({"dumpEnergy": "61", "chargeGunState": "0"})
        return {"ok": True}
    return probe


async def account():
    async with async_session_factory() as s:
        return (await s.execute(select(EbroAccount).where(EbroAccount.id == 7))).scalar_one()


async def test_a_dead_session_is_reopened_with_the_stored_password_and_the_read_goes_on(world, monkeypatch) -> None:
    poller, alerts = world
    logins = []

    def login(phone, password, area):
        logins.append((phone, password, area))
        return dict(TOKEN)

    monkeypatch.setattr(poller_module.ebro_login, "login_with_password", login)
    monkeypatch.setattr(poller_module, "probe_once", probe_until_relogin(logins))
    result = await poller._poll_vehicle(VIN)
    assert logins == [("600000001", "secreta", "34")] and result["battery_level"] == 61.0
    acc = await account()
    assert acc.token_data["access_token"] == "fresh-access" and acc.last_login > datetime.datetime(2026, 11, 1, 10, 0)
    with open(account_session_path(7), encoding="utf-8") as fh:            # one session for the account, shared by its cars
        assert json.load(fh)["access_token"] == "fresh-access"
    assert {poller._contexts[v].token_path for v in (VIN, OTHER)} == {account_session_path(7)}
    assert poller._contexts[VIN].state.token_lock is poller._contexts[OTHER].state.token_lock
    assert alerts == [] and not poller.session_lost(7, acc.last_login)


async def test_a_rejected_password_stops_the_retries_and_tells_the_manager_once(world, monkeypatch) -> None:
    poller, alerts = world
    logins = []

    def login(phone, password, area):
        logins.append(phone)
        raise RuntimeError("HTTP 400: El nombre de usuario no existe o la contraseña es incorrecta")

    monkeypatch.setattr(poller_module.ebro_login, "login_with_password", login)
    monkeypatch.setattr(poller_module, "probe_once", probe_until_relogin([]))
    assert await poller._poll_vehicle(VIN) is None
    poller._relogin_attempt_at.clear()                      # even a day later...
    assert await poller._poll_vehicle(OTHER) is None
    assert logins == ["600000001"] and alerts == [(1, ["Ebro", "Segundo"])]
    acc = await account()
    assert poller.session_lost(7, acc.last_login)
    # The manager connects the account again (its `last_login` changes): the stop is lifted
    async with async_session_factory() as s:
        a = await s.get(EbroAccount, 7)
        a.last_login = datetime.datetime.utcnow()
        await s.commit()
    assert not poller.session_lost(7, (await account()).last_login)


async def test_a_network_failure_is_retried_after_a_quarter_of_an_hour_not_a_day(world, monkeypatch) -> None:
    """09-11-2026: the first poll after a restart ran before the container had a route; the account then waited 24 h for nothing."""
    poller, alerts = world
    logins = []

    def login(phone, password, area):
        logins.append(phone)
        raise ConnectionError("no route")

    monkeypatch.setattr(poller_module.ebro_login, "login_with_password", login)
    monkeypatch.setattr(poller_module, "probe_once", probe_until_relogin([]))
    assert await poller._poll_vehicle(VIN) is None
    assert await poller._poll_vehicle(VIN) is None
    assert logins == ["600000001"] and alerts == []                           # not hammered within the retry window
    wait = poller._relogin_attempt_at[7] + poller_module.SESSION_RELOGIN_INTERVAL - datetime.datetime.utcnow()
    assert datetime.timedelta(minutes=14) < wait <= poller_module.SESSION_RELOGIN_NETWORK_RETRY
    poller._relogin_attempt_at[7] -= poller_module.SESSION_RELOGIN_NETWORK_RETRY
    assert await poller._poll_vehicle(VIN) is None
    assert logins == ["600000001", "600000001"]


async def test_a_car_whose_manager_disconnected_the_account_is_not_polled(world) -> None:
    async with async_session_factory() as s:
        connected = await poller_module.connected_owner_ids(s)
        car = (await s.execute(select(Vehicle).where(Vehicle.vin == VIN))).scalar_one()
        assert connected == {1} and pollable(car, connected)
        (await s.get(EbroAccount, 7)).is_active = False
        await s.commit()
        connected = await poller_module.connected_owner_ids(s)
    assert connected == set() and not pollable(car, connected)
    # ...unless the server itself runs on the account of its `.env` (installs without any account in the database)
    settings.EBRO_PHONE, settings.EBRO_PASSWORD = "600000009", "x"
    try:
        assert pollable(car, connected)
        assert not pollable(car, {2})                         # another manager connected: this one's cars still wait for their own account
    finally:
        settings.EBRO_PHONE, settings.EBRO_PASSWORD = "", ""
    manual = Vehicle(owner_id=1, vin="MANX", is_manual=True)
    assert not pollable(manual, {1})


async def test_a_car_whose_account_the_cloud_rejected_waits_for_the_account_to_be_reconnected(world) -> None:
    """09-11-2026: a rejected account was still read every hour, and every hour the log said the same thing."""
    poller, _alerts = world
    async with async_session_factory() as s:
        car = (await s.execute(select(Vehicle).where(Vehicle.vin == VIN))).scalar_one()
        assert await poller.lost_owner_ids(s) == set()
        poller._session_lost[7] = datetime.datetime(2026, 11, 1, 10, 0)       # the stored password was rejected
        lost = await poller.lost_owner_ids(s)
        assert lost == {1} and not pollable(car, {1}, lost) and pollable(car, {1})
        (await s.get(EbroAccount, 7)).last_login = datetime.datetime(2026, 11, 9, 8, 0)   # the manager reconnected the account
        await s.commit()
        assert await poller.lost_owner_ids(s) == set()


async def test_reads_without_a_session_are_logged_once_until_one_works(world, monkeypatch, caplog) -> None:
    """16-11-2026: 276 identical «brought no data: no_usertoken» lines in five hours for one car."""
    poller, _alerts = world
    logins = []

    def login(phone, password, area):
        raise RuntimeError("HTTP 400: El nombre de usuario no existe o la contraseña es incorrecta")

    monkeypatch.setattr(poller_module.ebro_login, "login_with_password", login)
    monkeypatch.setattr(poller_module, "probe_once", probe_until_relogin(logins))
    monkeypatch.setattr(poller_module.VehiclePoller, "_session_paused", {}, raising=False)

    def warnings():
        return [r for r in caplog.records if "brought no data" in r.message and r.levelname == "WARNING"]

    with caplog.at_level("DEBUG", logger="app.services.poller"):
        for _ in range(4):
            await poller._poll_vehicle(VIN)
        assert len(warnings()) == 1 and len([r for r in caplog.records if "brought no data" in r.message]) == 4
        assert "reads paused until there is a new session" in warnings()[0].message
        logins.append("reconnected")                                           # the manager reconnects: reads work again
        await poller._poll_vehicle(VIN)
        assert VIN not in poller._session_paused and any("Reads of" in r.message and "resumed" in r.message for r in caplog.records)
        logins.clear()                                                         # and some day the session dies again: said again
        await poller._poll_vehicle(VIN)
        assert len(warnings()) == 2


async def test_a_car_without_a_session_is_not_read_again_until_there_is_a_new_one(world, data_dir, monkeypatch) -> None:
    """16-11-2026: a dead session was asked for again on every read, 276 times in five hours for one car, always to hear «no»."""
    poller, _alerts = world
    monkeypatch.setattr(poller_module.VehiclePoller, "_session_paused", {}, raising=False)
    session_file = data_dir / "session.json"
    session_file.write_text("{}")
    poller._contexts[VIN].token_path = str(session_file)
    reads = []

    def dead(ctx, publish, force=False, on_data=None):
        reads.append(ctx.vin)
        return {"ok": False, "reason": "no_usertoken"}

    monkeypatch.setattr(poller_module, "probe_once", dead)
    now = datetime.datetime.utcnow()
    poller._relogin_attempt_at[7] = now - datetime.timedelta(hours=1)          # today's attempt with the password is spent
    assert not poller.reads_paused(VIN, 7)
    await poller._poll_vehicle(VIN)
    assert reads == [VIN] and poller.reads_paused(VIN, 7)
    assert poller.reads_paused(VIN, 7, now=now + datetime.timedelta(hours=22))  # hours later, still nothing new: left alone
    assert not poller.reads_paused(OTHER, 7)                                    # only the car that found it dead
    # the daily attempt with the stored password is due: one read goes through, and that read is what starts it
    assert not poller.reads_paused(VIN, 7, now=now + datetime.timedelta(hours=23, minutes=1))
    # a new session written to the file (Sincronizar, the account reconnected…): read again straight away
    stamp = session_file.stat().st_mtime + 5
    os.utime(session_file, (stamp, stamp))
    assert not poller.reads_paused(VIN, 7)
    # a car of nobody (the server's own account) waits a day from the moment it found the session dead
    await poller._poll_vehicle(VIN)
    assert poller.reads_paused(VIN, None, now=now + datetime.timedelta(hours=23))
    assert not poller.reads_paused(VIN, None, now=now + datetime.timedelta(hours=24, minutes=1))


async def test_the_polling_loop_skips_a_car_whose_session_is_dead(world, data_dir, monkeypatch) -> None:
    poller, _alerts = world
    monkeypatch.setattr(poller_module.VehiclePoller, "_session_paused", {}, raising=False)
    monkeypatch.setattr(poller_module.VehiclePoller, "_unpolled_logged", set(), raising=False)
    for vin in (VIN, OTHER):
        poller._contexts[vin].token_path = str(data_dir / "session.json")
    read = []

    async def poll(vin):
        read.append(vin)

    async def nothing(*args, **kwargs):
        return None

    async def one_pass(seconds):
        poller._running = False

    async def bind():
        return None

    monkeypatch.setattr(poller, "_poll_limited", poll)
    monkeypatch.setattr(poller, "bind_sessions", bind)
    for name in ("_run_climate_schedules", "_maybe_apply_retention", "_maybe_sync_catalog"):
        monkeypatch.setattr(poller, name, nothing)
    monkeypatch.setattr(poller_module.ChargeManager.get_instance(), "tick", nothing)
    monkeypatch.setattr(poller_module.asyncio, "sleep", one_pass)
    poller._relogin_attempt_at[7] = datetime.datetime.utcnow()                # today's attempt with the password is spent
    poller._pause_reads(VIN)
    poller._running = True
    await poller._poller_loop()
    assert read == [OTHER]
