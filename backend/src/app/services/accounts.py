"""Ebro account sign-up: lists the account's vehicles and leaves one token per VIN.

The login and the poller used to discover the vehicles each with its own copy of this code, and both
left a stray `token.json` in the working directory (outside the data volume). Here it is done
once and leaves no trace: the provisional token lives in a temporary folder
inside DATA_DIR that is deleted at the end.
"""
from __future__ import annotations

import json
import logging
import os
import re
import shutil
import tempfile

from app.config import get_data_dir, settings
from app.core.context import CoreCtx
from app.core.vehicles import iter_vehicles, query_list
from app.core.wake import _bff_login

_LOGGER = logging.getLogger("app.services.accounts")


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


def write_token_file(path: str, token_data: dict) -> None:
    """Writes the token atomically with 0600 permissions."""
    tmp = f"{path}.tmp"
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as fh:
        json.dump(token_data, fh)
    os.replace(tmp, path)


def fetch_account_vehicles(token_data: dict, skip_vins: frozenset = frozenset()) -> list[dict]:
    """Lists the account's vehicles and stores the token in `data/<vin>/token.json`.

    `skip_vins`: cars that already belong to ANOTHER manager; their token is left alone (another Ebro account that sees them cannot overwrite the owner's).

    BLOCKING (network): call it from a thread (`run_in_executor`). Raises if the
    BFF login or the query fails; the caller decides how to report it."""
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
