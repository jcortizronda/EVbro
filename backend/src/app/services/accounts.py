"""Ebro account sign-up: lists the account's vehicles on the account's session (services/ebro_sessions.py).

The login and the poller used to discover the vehicles each with its own copy of this code, and both
left a stray `token.json` in the working directory (outside the data volume). Here it is done
once and leaves no trace.
"""
from __future__ import annotations

import json
import logging
import os
import re
import shutil
import tempfile
from typing import Optional

from app.config import get_data_dir, settings
from app.core import context as core_context
from app.core import ebro_account, ebro_login, ebro_reset
from app.core.context import CoreCtx
from app.core.errors import CommandError
from app.core.vehicles import iter_vehicles, query_list
from app.core.wake import _bff_login
from app.services.ebro_sessions import session_lock, store_session, write_token_file  # noqa: F401  (write_token_file: re-exported)

_LOGGER = logging.getLogger("app.services.accounts")

# The Ebro cloud writes its messages and SMS in the `Accept-Language` of each request (the official app sends the user's
# language). EVbro speaks Spanish by default and, where the person is known, their language (`ebro_language`).
EBRO_LANGUAGES = {"es": "es-ES", "en": "en-GB", "pt": "pt-PT"}     # the app's own values
core_context.DEFAULT_ACCEPT_LANGUAGE = EBRO_LANGUAGES["es"]


def ebro_language(lang: Optional[str]) -> str:
    """The `Accept-Language` for an EVbro language (es | en | pt)."""
    return EBRO_LANGUAGES.get((lang or "es")[:2].lower(), EBRO_LANGUAGES["es"])


_VIN_RE = re.compile(r"[A-Za-z0-9]{5,32}")


def vehicle_data_dir(vin: str) -> str:
    """The vehicle's persistent folder: token.json and taskid.txt.

    The VIN sometimes comes from the URL: it must be alphanumeric so it cannot escape
    DATA_DIR (`..`, slashes) when the path is built."""
    if not _VIN_RE.fullmatch(vin or ""):
        raise ValueError("invalid VIN")
    path = os.path.join(get_data_dir(), vin)
    os.makedirs(path, exist_ok=True)
    return path


def remove_vehicle_data(vin: str) -> None:
    """A deleted car takes its folder with it: its old session copy and its taskId are credentials."""
    if not _VIN_RE.fullmatch(vin or ""):
        raise ValueError("invalid VIN")
    shutil.rmtree(os.path.join(get_data_dir(), vin), ignore_errors=True)


def fetch_account_vehicles(token_data: Optional[dict], skip_vins: frozenset = frozenset(), session_path: Optional[str] = None) -> list[dict]:
    """Lists the account's vehicles.

    With `session_path` (an account connected in the web: the normal case) it works on that file, the session every car of the
    account shares (services/ebro_sessions.py): `token_data`, if given, is a session just obtained with the password and is kept there
    first; without it the stored one is used, renewed in place when it expired. Raises if that session does not open, so the caller can
    try the password.

    Without `session_path` (the server's own `.env` account) the old way: a provisional copy that is handed to each car's
    `data/<vin>/token.json`. `skip_vins`: cars of ANOTHER manager, whose token is left alone.

    BLOCKING (network): call it from a thread (`run_in_executor`). Raises if the BFF login or the query fails; the caller decides
    how to report it."""
    if session_path is not None:
        if token_data is not None:
            store_session(session_path, token_data)
        ctx = CoreCtx(tsp_host=settings.EBRO_TSP_HOST, bff=settings.EBRO_BFF_HOST, channel_id=settings.EBRO_CHANNEL_ID,
                      token_path=session_path)
        ctx.state.token_lock = session_lock(session_path)        # a renewal here and one of a car never overlap
        user_token, t_user_id = _bff_login(ctx)
        if not user_token:
            raise RuntimeError("no_usertoken")
        if t_user_id:
            ctx.tuserid = str(t_user_id)
        body = {"channelId": ctx.channel_id, "tUserId": str(t_user_id)} if t_user_id else None
        return iter_vehicles(query_list(ctx, body=body))

    data_dir = get_data_dir()
    os.makedirs(data_dir, exist_ok=True)
    work = tempfile.mkdtemp(prefix=".login-", dir=data_dir)
    try:
        token_file = os.path.join(work, "token.json")
        write_token_file(token_file, token_data)
        ctx = CoreCtx(
            tsp_host=settings.EBRO_TSP_HOST,
            bff=settings.EBRO_BFF_HOST,
            channel_id=settings.EBRO_CHANNEL_ID,
            token_path=token_file,
        )
        _user_token, t_user_id = _bff_login(ctx)
        if t_user_id:
            ctx.tuserid = str(t_user_id)
        body = {"channelId": ctx.channel_id, "tUserId": str(t_user_id)} if t_user_id else None
        items = iter_vehicles(query_list(ctx, body=body))

        # `_bff_login` may have renewed the token: hand out the latest one.
        try:
            with open(token_file, encoding="utf-8") as fh:
                latest = json.load(fh)
        except (OSError, ValueError):
            latest = token_data
        for item in items:
            vin = item.get("vin")
            if not vin or vin in skip_vins:
                continue
            try:
                write_token_file(os.path.join(vehicle_data_dir(vin), "token.json"), latest)
            except OSError as exc:
                _LOGGER.warning("Could not write the token of %s: %s", vin, exc)
        return items
    finally:
        shutil.rmtree(work, ignore_errors=True)


def change_account_password(session_path: str, phone: str, area_code: str, current: str, new: str) -> dict:
    """Changes the password of the Ebro account (core/ebro_account.py) on the session its cars share, then signs in with the new
    one: the official app does the same, because the change closes the sessions of the old password.

    Returns {"changed": bool, "reason": the cloud's text when it refused, "token_data": the new session or None}. Raises
    RuntimeError("no_usertoken") when the stored session does not open (the account has to be connected again first), and lets
    network errors through. BLOCKING (network): call it from a thread."""
    ctx = CoreCtx(tsp_host=settings.EBRO_TSP_HOST, bff=settings.EBRO_BFF_HOST, channel_id=settings.EBRO_CHANNEL_ID,
                  token_path=session_path)
    ctx.state.token_lock = session_lock(session_path)
    user_token, _t_user_id = _bff_login(ctx)            # renews the access token when it expired
    if not user_token:
        raise RuntimeError("no_usertoken")
    answer = ebro_account.change_password(ctx, current, new)
    if not ebro_account.accepted(answer):
        return {"changed": False, "reason": ebro_account.refusal(answer), "token_data": None}
    ok, token_data = ebro_login.password_login(phone, new, area_code, host=settings.EBRO_BFF_HOST)
    if not ok or not isinstance(token_data, dict):
        _LOGGER.warning("The Ebro cloud took the new password but refused a login with it: %s", str(token_data)[:120],
                        extra={"telemetry": False})
        return {"changed": True, "reason": None, "token_data": None}
    store_session(session_path, token_data)
    return {"changed": True, "reason": None, "token_data": token_data}


def change_account_pin(ctx, current_pin: str, new_pin: str) -> dict:
    """Changes the car-control PIN of the Ebro account in the cloud (core/ebro_account.py), through the car `ctx` (the request names a
    car), then proves the new PIN with `checkPassword`: what this route answers on success is not known, and the PIN that matters is
    the one the cloud accepts.

    Returns {"changed": bool, "unconfirmed": bool, "reason": text or None, "code": the cloud's code}. Raises RuntimeError("no_usertoken")
    when the session does not open and CommandError when the current PIN is refused or locked (the same as a command). BLOCKING."""
    from app.core import taskid

    user_token, tuid = _bff_login(ctx)
    if not user_token:
        raise RuntimeError("no_usertoken")
    answer = ebro_account.change_pin(ctx, tuid, current_pin, new_pin)
    code = answer.get("code")
    _LOGGER.info("Ebro PIN change of %s answered code=%s", ctx.vin[-6:], code)
    if not ebro_account.pin_change_looks_accepted(answer):
        return {"changed": False, "unconfirmed": False, "reason": ebro_account.refusal(answer), "code": code}
    ctx.invalidate_taskid()                                # minted with the old PIN
    try:
        taskid.mint_with_pin(ctx, tuid, new_pin)
    except CommandError as err:
        return {"changed": False, "unconfirmed": True, "reason": str(err), "code": code}
    ctx.invalidate_taskid()                                # the next command mints its own with the PIN it is given
    return {"changed": True, "unconfirmed": False, "reason": None, "code": code}


def rename_car_in_ebro(ctx, nickname: str) -> dict:
    """Changes the car's nickname in the Ebro account (core/ebro_account.py) and reads the account's list again to see it there:
    what the PUT answers on success is taken from the app (`code == 0`), and the list is the proof.

    Returns {"changed": bool, "unconfirmed": bool, "reason": text or None, "code": the cloud's code}. Raises RuntimeError("no_usertoken")
    when the session does not open. BLOCKING."""
    user_token, _t_user_id = _bff_login(ctx)
    if not user_token:
        raise RuntimeError("no_usertoken")

    def listed():
        return next((c for c in ebro_account.owner_cars(ctx) if str(c.get("vin", "")).upper() == ctx.vin.upper()), None)

    car = listed()
    if car is None:
        return {"changed": False, "unconfirmed": False, "reason": "el coche no aparece en la lista de la cuenta", "code": None}
    if car.get("nickname") == nickname:
        return {"changed": True, "unconfirmed": False, "reason": None, "code": None}
    answer = ebro_account.set_car_nickname(ctx, ctx.vin, nickname, car.get("defaultFlag"))
    code = answer.get("code")
    _LOGGER.info("Ebro nickname change of %s answered code=%s", ctx.vin[-6:], code)
    if not ebro_account.accepted(answer):
        return {"changed": False, "unconfirmed": False, "reason": ebro_account.refusal(answer), "code": code}
    now = (listed() or {}).get("nickname")
    return {"changed": now == nickname, "unconfirmed": now != nickname, "reason": None, "code": code}


# ───────────────────────── Resetting the password or the PIN with a code by SMS (core/ebro_reset.py) ─────────────────────────
def reset_open_session(ctx) -> str:
    """Make sure the account's session opens (renewing it if it expired) and return the `tUserId`. Raises RuntimeError("no_usertoken").
    BLOCKING."""
    user_token, t_user_id = _bff_login(ctx)
    if not user_token:
        raise RuntimeError("no_usertoken")
    return str(t_user_id or "")


def reset_captcha(ctx) -> dict:
    """A new puzzle from the cloud (see `ebro_reset.captcha_create`). BLOCKING."""
    reset_open_session(ctx)
    return ebro_reset.captcha_create(ctx)


def reset_send_sms(ctx, phone: str, area_code: str, module: str, token: str, secret_key: str, x: float) -> dict:
    """Check the puzzle and, if the piece fits, ask for the SMS. {"sent": bool, "captcha_failed": bool, "reason": text}. BLOCKING."""
    reset_open_session(ctx)
    verification = ebro_reset.captcha_check(ctx, token, secret_key, x)
    if verification is None:
        return {"sent": False, "captcha_failed": True, "reason": None}
    answer = ebro_reset.send_sms_code(ctx, phone, area_code, module, verification)
    _LOGGER.info("Ebro SMS code (%s) answered code=%s", module, answer.get("code"))
    if not ebro_reset.succeeded(answer):
        return {"sent": False, "captcha_failed": False, "reason": ebro_reset.refusal(answer)}
    return {"sent": True, "captcha_failed": False, "reason": None}


def reset_pin_with_code(ctx, phone: str, area_code: str, code: str, new_pin: str) -> dict:
    """Set a new PIN with the SMS code, then prove it with `checkPassword`. {"changed", "unconfirmed", "reason"}. BLOCKING."""
    from app.core import taskid

    tuid = reset_open_session(ctx)
    check, answer = ebro_reset.reset_pin(ctx, tuid, phone, area_code, code, new_pin)
    _LOGGER.info("Ebro PIN reset of %s: smsCheck code=%s, retrievePassword code=%s", ctx.vin[-6:], check.get("code"),
                 None if answer is None else answer.get("code"))
    if answer is None:
        return {"changed": False, "unconfirmed": False, "reason": ebro_reset.refusal(check)}
    if not ebro_reset.succeeded(answer):
        return {"changed": False, "unconfirmed": False, "reason": ebro_reset.refusal(answer)}
    ctx.invalidate_taskid()
    try:
        taskid.mint_with_pin(ctx, tuid, new_pin)
    except CommandError as err:
        return {"changed": False, "unconfirmed": True, "reason": str(err)}
    ctx.invalidate_taskid()
    return {"changed": True, "unconfirmed": False, "reason": None}


def reset_password_with_code(ctx, session_path: str, phone: str, area_code: str, code: str, new_password: str) -> dict:
    """Set a new password with the SMS code, then sign in with it and keep the new session for every car of the account.
    {"changed", "reason", "token_data"}. BLOCKING."""
    reset_open_session(ctx)
    answer = ebro_reset.reset_password(ctx, phone, area_code, code, new_password)
    _LOGGER.info("Ebro password reset answered code=%s", answer.get("code"))
    if not ebro_reset.succeeded(answer):
        return {"changed": False, "reason": ebro_reset.refusal(answer), "token_data": None}
    ok, token_data = ebro_login.password_login(phone, new_password, area_code, host=settings.EBRO_BFF_HOST)
    if not ok or not isinstance(token_data, dict):
        return {"changed": True, "reason": None, "token_data": None}
    store_session(session_path, token_data)
    return {"changed": True, "reason": None, "token_data": token_data}


def account_ctx(session_path: str) -> CoreCtx:
    """A context on the account's shared session (no car): for what belongs to the account, not to a car."""
    ctx = CoreCtx(tsp_host=settings.EBRO_TSP_HOST, bff=settings.EBRO_BFF_HOST, channel_id=settings.EBRO_CHANNEL_ID, token_path=session_path)
    ctx.state.token_lock = session_lock(session_path)
    return ctx
