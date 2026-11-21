"""Ebro sessions of the poller: which cars can be read, and reopening a session the cloud no longer accepts.

Part of `VehiclePoller` (services/poller.py), split out on 2026-11-09 without changing behaviour. docs/ebro-protocol.md, «Sesión muerta».
"""
from __future__ import annotations

import asyncio
import datetime
import logging
import os
from typing import Optional, Set

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.core import ebro_login
from app.db.models import EbroAccount, Vehicle
from app.db.session import async_session_factory
from app.services.accounts import vehicle_data_dir
from app.services.car_events import CarEventHub
from app.services.ebro_sessions import account_session_path, attach, seed_account_session, store_session
from app.services.notification_service import NotificationService
from app.timeutil import utcnow

_LOGGER = logging.getLogger("app.services.poller")

# A session the cloud no longer accepts (the refresh token rejected too) is opened again with the password the manager saved when
# connecting the account, at most once a day per account. A rejected password stops the retries until the account is reconnected:
# hammering the login with a wrong password is how accounts get locked. (07-11-2026: a car went dark for 36 hours this way.)
SESSION_RELOGIN_INTERVAL = datetime.timedelta(hours=24)
# A network failure while reopening says nothing about the password: retried soon instead of burning the day's attempt (09-11-2026:
# the first poll after a restart ran before the container had a route and the account waited 24 h for nothing)
SESSION_RELOGIN_NETWORK_RETRY = datetime.timedelta(minutes=15)


async def connected_owner_ids(session: AsyncSession) -> Set[int]:
    """Managers with an active Ebro account: the only cars that can be read."""
    return {uid for (uid,) in (await session.execute(select(EbroAccount.user_id).where(EbroAccount.is_active == True))).all() if uid is not None}  # noqa: E712


class LoginFailed(RuntimeError):
    """The Ebro cloud could not be entered with an account's password. `rejected` tells a refusal (wrong password, blocked
    account: the manager has to act) from a network failure (nothing to conclude)."""

    def __init__(self, message: str, rejected: bool) -> None:
        super().__init__(message)
        self.rejected = rejected


def pollable(vehicle: Vehicle, connected: Set[int], lost: Set[int] = frozenset()) -> bool:
    """Whether a car can be read: its manager keeps an Ebro account connected, or the server runs on the account of its `.env`
    (cars of nobody, or installs without any account in the database). `lost`: managers whose account the cloud rejected
    (password included); their cars wait for the account to be reconnected instead of failing every hour."""
    if vehicle.is_manual:
        return False
    if vehicle.owner_id is not None and vehicle.owner_id in lost:
        return False
    if vehicle.owner_id in connected:
        return True
    env_account = bool(settings.EBRO_PHONE and settings.EBRO_PASSWORD)
    return env_account and (vehicle.owner_id is None or not connected)


def without_secrets(text: str) -> str:
    """The cloud's answer for the log: its sentence, never a token or a number it may echo."""
    return text.split("access_token")[0][:160]


class SessionMixin:
    """Methods of `VehiclePoller`; the state they use is declared there."""

    # ───────────────────────── one session per account ─────────────────────────
    async def bind_sessions(self) -> None:
        """Every car reads and renews the session of its manager's connected Ebro account: one file per account
        (services/ebro_sessions.py). A car whose manager has none keeps its own file. The first time, the account's file is made from
        the newest copy its cars had. Cheap: at start-up, after connecting an account or finding cars, and on every pass of the loop."""
        async with async_session_factory() as session:
            accounts = (await session.execute(select(EbroAccount.id, EbroAccount.user_id).where(EbroAccount.is_active == True)  # noqa: E712
                                              .order_by(EbroAccount.id))).all()
            cars = (await session.execute(select(Vehicle.vin, Vehicle.owner_id).where(Vehicle.is_manual.is_(False)))).all()
            by_owner = {uid: acc_id for (acc_id, uid) in accounts if uid is not None}          # the newest account of a manager wins
            loop = asyncio.get_running_loop()
            for owner_id, acc_id in by_owner.items():
                if os.path.exists(account_session_path(acc_id)):
                    continue
                acc = await session.get(EbroAccount, acc_id)
                copies = [os.path.join(vehicle_data_dir(vin), "token.json") for (vin, owner) in cars if owner == owner_id]
                origin = await loop.run_in_executor(None, seed_account_session, acc_id, copies, acc.token_data, acc.last_login)
                if origin:
                    _LOGGER.info("Ebro session of the account of user %s kept in one file for all its cars (from the %s's copy)", owner_id,
                                 "car" if origin == "car" else "account")
        for vin, owner_id in cars:
            acc_id = by_owner.get(owner_id)
            path = account_session_path(acc_id) if acc_id is not None else os.path.join(vehicle_data_dir(vin), "token.json")
            if attach(self._get_or_create_ctx(vin), path):
                _LOGGER.debug("Vehicle %s uses the session of %s", vin[-6:], "its account" if acc_id is not None else "its own file")

    # ───────────────────────── reads paused without a session ─────────────────────────
    def _session_stamp(self, vin: str) -> Optional[float]:
        """When the car's session file was last written (None if there is none): any new session changes it."""
        try:
            return os.path.getmtime(self._get_or_create_ctx(vin).token_path)
        except OSError:
            return None

    def _pause_reads(self, vin: str, now: Optional[datetime.datetime] = None) -> bool:
        """Notes that a read of this car found no session; True the first time (until a read works again)."""
        first = vin not in self._session_paused
        self._session_paused[vin] = {"since": now or utcnow(), "stamp": self._session_stamp(vin)}   # the last read that found it dead
        return first

    def reads_paused(self, vin: str, account_id: Optional[int], now: Optional[datetime.datetime] = None) -> bool:
        """Whether the loop leaves this car alone: its last read found the session dead and nothing has changed since. Every
        read would ask the cloud for a session with a dead token and get a «no» (16-11-2026: one car, 276 times in five hours).
        Reads come back when the session file is written again (Sincronizar, the account reconnected, the relogin of another car
        of the account) or when the daily attempt with the stored password is due, which only a read starts."""
        paused = self._session_paused.get(vin)
        if paused is None or self._session_stamp(vin) != paused["stamp"]:
            return False
        last = self._relogin_attempt_at.get(account_id) if account_id is not None else paused["since"]
        return last is not None and (now or utcnow()) - last < SESSION_RELOGIN_INTERVAL

    # ───────────────────────── Ebro session recovery ─────────────────────────
    async def lost_owner_ids(self, session: AsyncSession) -> Set[int]:
        """Managers whose active Ebro account the cloud rejected (session and stored password): their cars are not read until
        the account is reconnected."""
        rows = (await session.execute(select(EbroAccount.id, EbroAccount.user_id, EbroAccount.last_login)
                                      .where(EbroAccount.is_active == True))).all()  # noqa: E712
        return {uid for (acc_id, uid, last_login) in rows if uid is not None and self.session_lost(acc_id, last_login)}

    def session_lost(self, account_id: int, last_login: Optional[datetime.datetime]) -> bool:
        """True while the cloud rejects the stored password of this account (until the manager reconnects it)."""
        return account_id in self._session_lost and self._session_lost[account_id] == last_login

    async def _recover_session(self, vin: str) -> bool:
        """Signs in again with the password the manager saved when connecting the account, and keeps the new session in the account's
        file, the one all its cars read. At most one attempt per account and day; none at all once the cloud has rejected the password (the manager is
        told once and has to reconnect the account). Returns True when the car can be read again."""
        async with async_session_factory() as session:
            vehicle = (await session.execute(select(Vehicle).where(Vehicle.vin == vin))).scalar_one_or_none()
            if vehicle is None or vehicle.owner_id is None:
                return False
            account = (await session.execute(
                select(EbroAccount).where(EbroAccount.is_active == True, EbroAccount.user_id == vehicle.owner_id).order_by(EbroAccount.id.desc())  # noqa: E712
            )).scalars().first()
            if account is None or self.session_lost(account.id, account.last_login):
                return False
            now = utcnow()
            last = self._relogin_attempt_at.get(account.id)
            if last is not None and now - last < SESSION_RELOGIN_INTERVAL:
                return False
            self._relogin_attempt_at[account.id] = now
            acc_id, owner_id, last_login = account.id, account.user_id, account.last_login
            phone, password, area = account.phone, account.password, account.area_code or "34"
            names = [v.name for (v,) in (await session.execute(select(Vehicle).where(Vehicle.owner_id == owner_id, Vehicle.is_manual.is_(False)))).all()]

        loop = asyncio.get_running_loop()
        try:
            token_data = await loop.run_in_executor(None, lambda: ebro_login.login_with_password(phone, password, area))
        except RuntimeError as exc:
            # The cloud answered and said no (wrong password, blocked account…): stop until the account is reconnected
            _LOGGER.warning("The Ebro cloud rejected the stored password of the account of user %s: %s", owner_id, without_secrets(str(exc)),
                            extra={"telemetry": False})          # the account's problem, not the code's: the manager gets a notice
            self._session_lost[acc_id] = last_login
            await NotificationService.notify_account_session_lost(owner_id, names)
            return False
        except Exception as exc:                              # noqa: BLE001 — network: nothing to conclude, a retry soon tells
            _LOGGER.warning("Could not reopen the Ebro session of the account of user %s: %s", owner_id, type(exc).__name__)
            self._relogin_attempt_at[acc_id] = now - SESSION_RELOGIN_INTERVAL + SESSION_RELOGIN_NETWORK_RETRY
            return False
        if not isinstance(token_data, dict) or "access_token" not in token_data:
            self._session_lost[acc_id] = last_login
            await NotificationService.notify_account_session_lost(owner_id, names)
            return False

        try:
            await loop.run_in_executor(None, store_session, account_session_path(acc_id), token_data)
        except OSError as exc:
            _LOGGER.warning("Could not keep the reopened Ebro session of the account of user %s: %s", owner_id, exc)
            return False
        async with async_session_factory() as session:
            account = await session.get(EbroAccount, acc_id)
            if account is not None:
                account.token_data = token_data
                account.last_login = utcnow()
            await session.commit()
        await self.bind_sessions()                              # in case a car of the account was still on its own file
        self._session_lost.pop(acc_id, None)
        _LOGGER.info("Ebro session of the account of user %s reopened with the stored password", owner_id)
        asyncio.create_task(CarEventHub.get_instance().resume_owner(owner_id, self))        # the car's channel too, unless another app stopped it
        return True
