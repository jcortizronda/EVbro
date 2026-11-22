"""Commands of the poller: what the web and EVBro itself send to the car, with the PIN rules, the audit and the commands
each car refuses.

Part of `VehiclePoller` (services/poller.py), split out on 2026-11-09 without changing behaviour.
"""
from __future__ import annotations

import asyncio
import dataclasses
import logging
from typing import Dict, Optional

from sqlalchemy import select

from app.config import settings
from app.core import commands
from app.core.context import CoreCtx
from app.core.errors import CommandError
from app.db.models import AppUser, CommandAudit, EbroAccount, Vehicle
from app.db.session import async_session_factory
from app.security.access import first_manager_id
from app.services import command_policy

_LOGGER = logging.getLogger("app.services.poller")

UNEXPECTED_COMMAND_ERROR = "No se pudo enviar la orden al coche por un error inesperado del servidor (mira el log)."
# The cloud's answer when a car has no permission for a command («No vehicle control command permission»): remembered per car
NO_COMMAND_PERMISSION = "A00084"


# A busy car (`A00082`: it handles one order at a time) did NOT run the order and will take it in a few seconds: it is sent
# again instead of handing the person an error to tap through (12 of the 23 failed orders of a week, 22-11-2026).
BUSY_RETRIES = 2
BUSY_WAIT_SECONDS = 6.0


class CommandsMixin:
    """Methods of `VehiclePoller`; the state they use is declared there."""

    async def _send_command(self, vin: str, cmd_ctx: CoreCtx, command_key: str, params: Optional[Dict], origin: str):
        """Sends one order with the remedies that need nobody: (success, message, code, reason).

        * One conversation with the cloud per account at a time (`cloud_lock`).
        * Car busy → wait and send again, up to `BUSY_RETRIES` times.
        * Session refused → once more: with a cloud code (`A00000`: the token was overtaken by another sign-in of the account)
          the next send signs in again by itself; without one (the sign-in itself failed) the session is reopened first with
          the stored password, within its own limits (`_recover_session`)."""
        loop = asyncio.get_running_loop()
        busy_left, session_retry = BUSY_RETRIES, True
        while True:
            try:
                async with self.cloud_lock(vin):
                    message = await loop.run_in_executor(None, lambda: commands.send(cmd_ctx, command_key, emit=lambda m: None, params=params))
                return True, message, None, None
            except CommandError as e:
                if e.retryable and busy_left > 0:
                    busy_left -= 1
                    _LOGGER.info("'%s' for %s: the car is busy, sending it again in %.0f s", command_key, vin[-6:], BUSY_WAIT_SECONDS)
                    await asyncio.sleep(BUSY_WAIT_SECONDS)
                    continue
                if e.reason == "reauth" and session_retry:
                    session_retry = False
                    if e.code or await self._recover_session(vin):
                        _LOGGER.info("'%s' for %s: the session was refused, signing in again and sending it once more", command_key, vin[-6:])
                        continue
                return False, str(e), e.code, e.reason
            except Exception:
                # The text of an unexpected exception (library internals, hosts) is for the log, not for the client.
                _LOGGER.exception("Unexpected failure sending '%s' (%s) to %s", command_key, origin, vin[-6:])
                return False, UNEXPECTED_COMMAND_ERROR, None, None

    async def execute_command(
        self,
        vin: str,
        command_key: str,
        pin: str,
        params: Optional[Dict] = None,
        client_ip: Optional[str] = None,
    ) -> Dict:
        """Runs a remote command on the vehicle.

        Returns `{"success", "message", "reason", "retry_after"}`. `reason` is stable (the UI
        acts on it): `pin_required`, `pin_invalid`, `pin_locked`, `params_invalid` (rejected
        before sending), or the core's `reason` (`pin`, `reauth`, `config`) if sending failed.
        The PIN and parameter rules live in `command_policy`."""
        ctx = self._get_or_create_ctx(vin)
        stored_pin = await self._stored_pin(ctx)
        policy = await self._pin_policy(vin)

        try:
            clean_params = command_policy.sanitize_params(command_key, params)
            effective_pin = command_policy.authorize(command_key, pin, stored_pin, vin=vin, policy=policy)
        except command_policy.CommandRejected as rej:
            await self._audit_command(vin, command_key, params, False, rej.reason, rej.message, client_ip)
            return {"success": False, "message": rej.message, "reason": rej.reason, "retry_after": rej.retry_after}

        # The typed PIN is good for THIS send only: a copy of the context (it shares the vehicle's
        # state: lock-out guard and taskId cache). It used to be assigned to `ctx.pin` for good,
        # so a mistyped PIN stayed and used up attempts on the following commands.
        cmd_ctx = dataclasses.replace(ctx, pin=effective_pin)
        if command_policy.requires_pin(command_key, policy) and not stored_pin:
            # Without a stored PIN, the cached taskId no longer proves the sender knows the PIN:
            # Chery is forced to check the typed one.
            ctx.invalidate_taskid()

        success, message, code, reason = await self._send_command(vin, cmd_ctx, command_key, clean_params, client_ip or "api")

        await self._audit_command(vin, command_key, clean_params, success, code or reason, message, client_ip)
        await self._remember_rejection(vin, command_key, success, code)

        # If the command succeeded, trigger a quick poll to refresh the state
        if success:
            asyncio.create_task(self.poll_vehicle(vin, force=True))

        return {"success": success, "message": message, "reason": reason, "code": code, "retry_after": 0}

    async def execute_internal(self, vin: str, command_key: str, params: Optional[Dict], origin: str) -> Dict:
        """A command EVBro sends itself (e.g. the charge manager): it uses the stored PIN and does NOT go through the
        API's parameter whitelist, which exists to restrain a client, not EVBro. It is audited all the same."""
        ctx = self._get_or_create_ctx(vin)
        stored_pin = await self._stored_pin(ctx)
        if not stored_pin:
            msg = "Falta el PIN de la cuenta Ebro: guárdalo en Ajustes → Cuenta Ebro para que EVBro pueda gestionar la carga."
            await self._audit_command(vin, command_key, params, False, "pin", msg, origin)
            return {"success": False, "message": msg, "reason": "pin"}
        cmd_ctx = dataclasses.replace(ctx, pin=stored_pin)
        success, message, code, reason = await self._send_command(vin, cmd_ctx, command_key, params, origin)
        await self._audit_command(vin, command_key, params, success, code or reason, message, origin)
        await self._remember_rejection(vin, command_key, success, code)
        if success:
            asyncio.create_task(self.poll_vehicle(vin, force=True))
        return {"success": success, "message": message, "reason": reason, "code": code}

    @staticmethod
    async def _remember_rejection(vin: str, command_key: str, success: bool, code: Optional[str]) -> None:
        """`A00084` is the cloud saying this car has no permission for the command (the S900 answers it to the rear window
        heater, 08-11-2026, five tries by its owner). Kept per car in `vehicles.rejected_commands` so the interface and the
        Home Assistant discovery can say so; an accepted send takes the command off the list again."""
        if not success and code != NO_COMMAND_PERMISSION:
            return
        async with async_session_factory() as session:
            vehicle = (await session.execute(select(Vehicle).where(Vehicle.vin == vin))).scalar_one_or_none()
            if vehicle is None:
                return
            current = list(vehicle.rejected_commands or [])
            if success and command_key in current:
                current.remove(command_key)
            elif not success and command_key not in current:
                current.append(command_key)
            else:
                return
            vehicle.rejected_commands = sorted(current)
            await session.commit()

    @staticmethod
    async def _pin_policy(vin: str) -> str:
        """When the car's owner wants the PIN asked for (AppUser.pin_policy); the default when the owner is unknown."""
        async with async_session_factory() as session:
            policy = (await session.execute(select(AppUser.pin_policy).join(Vehicle, Vehicle.owner_id == AppUser.id).where(Vehicle.vin == vin))).scalar_one_or_none()
        return policy if policy in command_policy.POLICIES else command_policy.DEFAULT_POLICY

    async def _stored_pin(self, ctx: CoreCtx) -> str:
        """The stored PIN of THIS car: the context's, or its owner's Ebro account, or (admin's cars only) the environment's ('' if none)."""
        if (ctx.pin or "").strip():
            return ctx.pin.strip()
        async with async_session_factory() as session:
            owner_id = (await session.execute(select(Vehicle.owner_id).where(Vehicle.vin == ctx.vin))).scalar_one_or_none()
            acc = None
            if owner_id is not None:
                acc = (await session.execute(select(EbroAccount).where(EbroAccount.is_active == True, EbroAccount.user_id == owner_id)  # noqa: E712
                                             .order_by(EbroAccount.id.desc()))).scalars().first()
            is_server_owner_car = owner_id is not None and owner_id == await first_manager_id(session)
        if acc and (acc.pin or "").strip():
            ctx.pin = acc.pin.strip()
        elif is_server_owner_car and (settings.EBRO_PIN or "").strip():
            ctx.pin = settings.EBRO_PIN.strip()
        return (ctx.pin or "").strip()

    @staticmethod
    async def _audit_command(vin, command_key, params, success, code, message, client_ip) -> None:
        """Records the attempt in the audit log, rejected ones too (wrong PIN, etc.)."""
        async with async_session_factory() as session:
            session.add(CommandAudit(
                vin=vin, command=command_key, params=params, success=success,
                code=(str(code)[:20] if code else None), message=message, client_ip=client_ip,
            ))
            await session.commit()
