"""One Ebro session per account, shared by all its cars.

Chery rotates the refresh token every time it is used: the previous one stops working. Until 2026-11-09 EVBro kept a copy of the
session per car (`data/<vin>/token.json`) plus one in the account (`ebro_accounts.token_data`), and each car renewed only its own
copy. So two cars of one account invalidated each other's session on every renewal, the account's copy aged until «Buscar
vehículos» had to log in with the password again, and two cars read in parallel could renew at the same instant.

Now every car of a connected account reads and renews one file, `data/accounts/<id>/token.json`. The core already serialises a
renewal with a lock and re-reads the file inside it (`core/wake.py`, `_refresh_token_detail`): whoever waited finds the token renewed
and does not renew it again. That lock belonged to each car's context, so the contexts that share a file now share that lock too
(`attach`). The core is not modified. A car whose manager has no account connected (the server's own `.env` account) keeps its own
file, as before.

The copy in `ebro_accounts.token_data` is still written at every password login; it only seeds the file the first time.
"""
from __future__ import annotations

import datetime
import json
import logging
import os
import shutil
import threading
from typing import Dict, Iterable, Optional

from app.config import get_data_dir

_LOGGER = logging.getLogger("app.services.ebro_sessions")

_locks: Dict[str, threading.Lock] = {}
_locks_guard = threading.Lock()


def write_token_file(path: str, token_data: dict) -> None:
    """Writes a session atomically, readable by the owner only (it is a credential)."""
    tmp = f"{path}.tmp"
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as fh:
        json.dump(token_data, fh)
    os.replace(tmp, path)


def account_session_path(account_id: int) -> str:
    """The session every car of the account reads and renews."""
    return os.path.join(get_data_dir(), "accounts", str(int(account_id)), "token.json")


def session_lock(path: str) -> threading.Lock:
    """The renewal lock of the session kept in `path`: the same object for every context that uses that file."""
    with _locks_guard:
        lock = _locks.get(path)
        if lock is None:
            lock = _locks[path] = threading.Lock()
        return lock


def attach(ctx, path: str) -> bool:
    """Points a car's context at the session file `path`, with the renewal lock all the contexts of that file share. Returns
    whether the file changed."""
    changed = ctx.token_path != path
    ctx.token_path = path
    ctx.state.token_lock = session_lock(path)
    return changed


def store_session(path: str, token_data: dict) -> None:
    """Keeps a session just obtained with the password. Under the file's lock, so it cannot interleave with a renewal. BLOCKING."""
    with session_lock(path):
        os.makedirs(os.path.dirname(path), exist_ok=True)
        write_token_file(path, token_data)


def seed_account_session(account_id: int, car_copies: Iterable[str], stored: Optional[dict],
                         stored_at: Optional[datetime.datetime] = None) -> Optional[str]:
    """Creates the account's file the first time, from the newest session there is: the copy one of its cars kept (the poller renews
    those) or the copy kept in the account at its last password login (`stored_at`, naive UTC). Returns `"car"` or `"account"` for
    where it came from, None when there was nothing to do. BLOCKING."""
    path = account_session_path(account_id)
    if os.path.exists(path):
        return None
    with session_lock(path):
        if os.path.exists(path):
            return None
        copies = [p for p in car_copies if os.path.isfile(p)]
        newest = max(copies, key=os.path.getmtime) if copies else None
        usable = isinstance(stored, dict) and bool(stored.get("access_token"))
        login_ts = stored_at.replace(tzinfo=datetime.timezone.utc).timestamp() if stored_at else None
        if newest and not (usable and login_ts is not None and login_ts > os.path.getmtime(newest)):
            os.makedirs(os.path.dirname(path), exist_ok=True)
            tmp = f"{path}.tmp"
            shutil.copyfile(newest, tmp)
            os.chmod(tmp, 0o600)
            os.replace(tmp, path)
            return "car"
        if usable:
            os.makedirs(os.path.dirname(path), exist_ok=True)
            write_token_file(path, stored)
            return "account"
    return None


def remove_account_session(account_id: int) -> None:
    """The session of a deleted account goes with it: it is a credential."""
    path = account_session_path(account_id)
    shutil.rmtree(os.path.dirname(path), ignore_errors=True)
    with _locks_guard:
        _locks.pop(path, None)
