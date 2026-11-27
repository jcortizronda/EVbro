"""One Ebro session per account, shared by all its cars (services/ebro_sessions.py).

Chery rotates the refresh token every time it is used. With one copy per car, two cars of one account invalidated each other's
session on every renewal, the account's copy aged until «Buscar vehículos» had to log in with the password, and two cars read in
parallel could renew at the same instant (2026-11-09).
"""
from __future__ import annotations

import datetime
import json
import os
import stat
import threading
import time

import pytest
from sqlalchemy import select

from app.core import ebro_login, wake
from app.core.context import CoreCtx
from app.db.models import AppUser, EbroAccount, Vehicle
from app.db.session import async_session_factory
from app.services import accounts, ebro_sessions, tenant
from app.services.ebro_sessions import (
    account_session_path,
    attach,
    seed_account_session,
    session_lock,
    write_token_file,
)
from app.services.poller import VehiclePoller

CAR_A, CAR_B, CAR_C = "LSJA0000000000081", "LSJA0000000000082", "LSJA0000000000083"


def token(n: int) -> dict:
    return {"access_token": f"a{n}", "refresh_token": f"r{n}", "expires_in": 7200}


def read(path: str) -> dict:
    with open(path, encoding="utf-8") as fh:
        return json.load(fh)


def car_copy(vin: str, n: int, age_s: float) -> str:
    path = os.path.join(accounts.vehicle_data_dir(vin), "token.json")
    write_token_file(path, token(n))
    when = time.time() - age_s
    os.utime(path, (when, when))
    return path


# ───────────────────────── the file and its lock ─────────────────────────
def test_the_first_time_the_newest_copy_becomes_the_account_session(data_dir) -> None:
    old, new = car_copy(CAR_A, 1, age_s=3600), car_copy(CAR_B, 2, age_s=60)
    assert seed_account_session(7, [old, new], token(0), datetime.datetime(2026, 1, 1)) == "car"
    path = account_session_path(7)
    assert read(path)["access_token"] == "a2" and stat.S_IMODE(os.stat(path).st_mode) == 0o600
    assert seed_account_session(7, [old, new], token(9)) is None and read(path)["access_token"] == "a2"   # never overwritten


def test_a_password_login_newer_than_the_cars_copies_wins(data_dir) -> None:
    old = car_copy(CAR_A, 1, age_s=7200)                        # the copy of a previous account of the manager
    assert seed_account_session(8, [old], token(5), datetime.datetime.utcnow()) == "account"
    assert read(account_session_path(8))["access_token"] == "a5"
    assert seed_account_session(9, [], None) is None and not os.path.exists(account_session_path(9))


def test_contexts_of_one_file_share_its_renewal_lock(data_dir) -> None:
    a, b, c = CoreCtx(vin=CAR_A), CoreCtx(vin=CAR_B), CoreCtx(vin=CAR_C)
    assert attach(a, account_session_path(7)) and attach(b, account_session_path(7)) and attach(c, account_session_path(8))
    assert a.token_path == b.token_path == account_session_path(7)
    assert a.state.token_lock is b.state.token_lock is session_lock(account_session_path(7))
    assert c.state.token_lock is not a.state.token_lock
    assert not attach(a, account_session_path(7))               # already there: nothing changes


def test_the_cars_of_one_account_share_the_wrong_pin_lockout(data_dir) -> None:
    """The PIN and Chery's lock belong to the account: with one counter per car, each car spent its own tries."""
    a, b, c = CoreCtx(vin=CAR_A), CoreCtx(vin=CAR_B), CoreCtx(vin=CAR_C)
    attach(a, account_session_path(17)), attach(b, account_session_path(17)), attach(c, account_session_path(18))
    assert a.lockout is b.lockout and a.lockout is not c.lockout
    with a.lockout.attempt("123456") as attempt:
        attempt.record_failure()
    assert b.lockout.was_rejected("123456") and not c.lockout.was_rejected("123456")
    b.reset_pin_lockout()                                        # the PIN configured again from either car clears it for both
    assert not a.lockout.was_rejected("123456")


@pytest.mark.parametrize("shared", [True, False])
def test_two_cars_renewing_at_once_renew_the_shared_session_only_once(tmp_path, monkeypatch, shared) -> None:
    """Both cars saw the same expired token. With the shared lock the second one re-reads the file inside it and finds it renewed;
    with a lock per car both renew with the same refresh token (the real cloud rejects the second and the session dies) and both
    write the same file at once (one of the writes fails)."""
    path = str(tmp_path / "token.json")
    write_token_file(path, token(1))
    calls, renewing = [], threading.Event()

    def refresh(rt, host=None, **kw):
        calls.append(rt)
        renewing.set()
        time.sleep(0.3)                                         # the other car arrives while this renewal is in flight
        return True, {"raw": token(len(calls) + 1)}

    monkeypatch.setattr(ebro_login, "refresh_token", refresh)
    a, b = CoreCtx(vin=CAR_A, token_path=path), CoreCtx(vin=CAR_B, token_path=path)
    if shared:
        attach(a, path)
        attach(b, path)
    results = {}
    first = threading.Thread(target=lambda: results.setdefault("a", wake._refresh_token_detail(a)))
    first.start()
    assert renewing.wait(2)
    second = threading.Thread(target=lambda: results.setdefault("b", wake._refresh_token_detail(b)))
    second.start()
    first.join(5)
    second.join(5)
    if shared:
        assert calls == ["r1"] and results["a"][0] and results["b"][0] and read(path)["refresh_token"] == "r2"
    else:
        assert calls == ["r1", "r1"]


# ───────────────────────── the poller ─────────────────────────
@pytest.fixture
async def poller(db, data_dir):
    async with async_session_factory() as s:
        s.add_all([Vehicle(owner_id=1, vin=CAR_A, name="Uno"), Vehicle(owner_id=1, vin=CAR_B, name="Dos"),
                   Vehicle(owner_id=2, vin=CAR_C, name="Tres")])
        s.add(EbroAccount(id=7, user_id=1, phone="600000001", password="x", area_code="34", is_active=True,
                          token_data=token(0), last_login=datetime.datetime(2026, 1, 1)))
        await s.commit()
    p = VehiclePoller()
    p._contexts = {}
    return p


async def test_the_cars_of_an_account_share_its_session_and_a_car_without_one_keeps_its_own(poller) -> None:
    car_copy(CAR_A, 1, age_s=3600)
    car_copy(CAR_B, 2, age_s=60)
    await poller.bind_sessions()
    a, b, c = (poller._contexts[v] for v in (CAR_A, CAR_B, CAR_C))
    assert a.token_path == b.token_path == account_session_path(7) and a.state.token_lock is b.state.token_lock
    assert read(account_session_path(7))["access_token"] == "a2"           # the newest copy the cars had
    assert c.token_path == os.path.join(accounts.vehicle_data_dir(CAR_C), "token.json")


async def test_each_car_speaks_its_managers_language_to_the_cloud(poller) -> None:
    """The cloud writes its messages in the request's `Accept-Language`: they came in Italian until 25-11-2026."""
    async with async_session_factory() as s:
        s.add_all([AppUser(id=1, username="ana", role="manager", password_hash="x", language="pt"),
                   AppUser(id=2, username="luis", role="manager", password_hash="x", language="es")])
        await s.commit()
    await poller.bind_sessions()
    assert [poller._contexts[v].accept_language for v in (CAR_A, CAR_B, CAR_C)] == ["pt-PT", "pt-PT", "es-ES"]
    assert CoreCtx(vin=CAR_A).accept_language == "es-ES"                    # EVbro's default for every other request
    assert accounts.ebro_language("en") == "en-GB" and accounts.ebro_language(None) == "es-ES"


async def test_another_account_of_the_manager_moves_its_cars_to_that_session(poller) -> None:
    await poller.bind_sessions()
    async with async_session_factory() as s:
        (await s.get(EbroAccount, 7)).is_active = False
        s.add(EbroAccount(id=9, user_id=1, phone="600000009", password="y", area_code="34", is_active=True,
                          token_data=token(4), last_login=datetime.datetime.utcnow()))
        await s.commit()
    await poller.bind_sessions()
    assert poller._contexts[CAR_A].token_path == account_session_path(9)
    assert read(account_session_path(9))["access_token"] == "a4"


async def test_the_account_list_works_on_the_shared_session_and_says_when_it_does_not_open(poller, monkeypatch) -> None:
    seen = {}

    def bff_login(ctx):
        seen["path"], seen["lock"] = ctx.token_path, ctx.state.token_lock
        return ("user-token", "42") if read(ctx.token_path)["access_token"] == "fresh" else (None, None)

    monkeypatch.setattr(accounts, "_bff_login", bff_login)
    monkeypatch.setattr(accounts, "query_list", lambda ctx, body=None: {"data": [{"vin": CAR_A}]})
    monkeypatch.setattr(accounts, "iter_vehicles", lambda raw: raw["data"])
    path = account_session_path(7)
    assert accounts.fetch_account_vehicles({"access_token": "fresh", "refresh_token": "r"}, frozenset(), path) == [{"vin": CAR_A}]
    assert read(path)["access_token"] == "fresh" and seen == {"path": path, "lock": session_lock(path)}
    assert accounts.fetch_account_vehicles(None, frozenset(), path) == [{"vin": CAR_A}]      # the stored one, renewed in place
    write_token_file(path, token(3))
    with pytest.raises(RuntimeError, match="no_usertoken"):
        accounts.fetch_account_vehicles(None, frozenset(), path)
    assert not os.path.exists(os.path.join(accounts.vehicle_data_dir(CAR_A), "token.json"))   # no copies per car any more


async def test_deleting_a_manager_takes_its_session_and_its_cars_folders(poller) -> None:
    async with async_session_factory() as s:
        s.add(AppUser(id=1, username="ana", role="manager", password_hash="x"))
        await s.commit()
    await poller.bind_sessions()
    car_copy(CAR_A, 1, age_s=10)
    assert os.path.exists(account_session_path(7))
    async with async_session_factory() as s:
        await tenant.delete_manager_space(s, 1)
        await s.commit()
        assert (await s.execute(select(Vehicle.vin))).scalars().all() == [CAR_C]
    assert not os.path.exists(os.path.dirname(account_session_path(7)))
    assert not os.path.exists(os.path.join(ebro_sessions.get_data_dir(), CAR_A))
