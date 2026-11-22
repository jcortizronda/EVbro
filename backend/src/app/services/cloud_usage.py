"""How much this server asks of the Ebro cloud, day by day: reads with data, empty and failed, and the other questions. Shown to the administrator in
the console (Estadísticas → «Lecturas y órdenes a la nube de Ebro»), next to the orders of `command_audit`.

Every server reads all its cars from one address, and the cloud's tolerance is unknown: the count is there to see the volume
and the failures before they become a block. Kept in memory and written every minute by the polling loop (and when it stops);
a crash loses at most that minute. Always on: it stays in this server and has nothing to do with the anonymous reports.
"""
from __future__ import annotations

import datetime
import threading
from collections import defaultdict
from typing import Any, Dict, List, Tuple

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import CloudUsage, CommandAudit
from app.db.session import async_session_factory
from app.timeutil import utcnow

READ_OK, READ_EMPTY, READ_FAILED = "read_ok", "read_empty", "read_failed"
QUERY = "query"                       # a question outside the reads: the alarm (Inicio asks while it is open) or the charge plan
KINDS = (READ_OK, READ_EMPTY, READ_FAILED, QUERY)
FLUSH_SECONDS = 60
REQUESTS_PER_READ = 4                 # the sign-in and the three questions (state, position, scheduled departure)
REQUESTS_PER_QUERY = 2                # the sign-in and the question
REQUESTS_PER_COMMAND = 2              # the sign-in and the order (plus the PIN check when its proof has expired)

_lock = threading.Lock()
_pending: Dict[Tuple[datetime.date, str], int] = defaultdict(int)
_last_flush: List[datetime.datetime] = []


def count(kind: str) -> None:
    if kind in KINDS:
        with _lock:
            _pending[(utcnow().date(), kind)] += 1


async def flush(force: bool = False) -> None:
    """Adds what was counted since the last time to the table. Called on every pass of the polling loop: it writes once a minute."""
    now = utcnow()
    if not force and _last_flush and (now - _last_flush[0]).total_seconds() < FLUSH_SECONDS:
        return
    _last_flush[:] = [now]
    with _lock:
        batch = dict(_pending)
        _pending.clear()
    if not batch:
        return
    try:
        async with async_session_factory() as session:
            for (day, kind), n in batch.items():
                row = await session.get(CloudUsage, (day, kind))
                if row is None:
                    session.add(CloudUsage(day=day, kind=kind, count=n))
                else:
                    row.count += n
            await session.commit()
    except Exception:                                         # noqa: BLE001 — a counter must never stop the loop; keep it for the next try
        with _lock:
            for key, n in batch.items():
                _pending[key] += n
        raise


async def daily(db: AsyncSession, days: int = 14) -> List[Dict[str, Any]]:
    """One row per day, newest first, with the reads of this table and the orders of the audit log. Days with nothing are left out."""
    since = utcnow().date() - datetime.timedelta(days=max(1, days) - 1)
    out: Dict[str, Dict[str, Any]] = {}

    def row(day: Any) -> Dict[str, Any]:
        key = str(day)[:10]
        return out.setdefault(key, {"day": key, READ_OK: 0, READ_EMPTY: 0, READ_FAILED: 0, QUERY: 0, "commands_ok": 0, "commands_failed": 0})

    for usage in (await db.execute(select(CloudUsage).where(CloudUsage.day >= since))).scalars():
        if usage.kind in KINDS:
            row(usage.day)[usage.kind] = usage.count
    with _lock:                                              # what has not been written yet
        for (day, kind), n in _pending.items():
            if day >= since:
                row(day)[kind] += n
    start = datetime.datetime.combine(since, datetime.time.min)
    day_of = func.date(CommandAudit.timestamp)
    for day, success, n in (await db.execute(select(day_of, CommandAudit.success, func.count()).where(CommandAudit.timestamp >= start)
                                             .group_by(day_of, CommandAudit.success))).all():
        row(day)["commands_ok" if success else "commands_failed"] += n
    for r in out.values():
        r["reads"] = r[READ_OK] + r[READ_EMPTY] + r[READ_FAILED]
        r["requests"] = (r["reads"] * REQUESTS_PER_READ + r[QUERY] * REQUESTS_PER_QUERY
                         + (r["commands_ok"] + r["commands_failed"]) * REQUESTS_PER_COMMAND)        # an estimate, not a count
    return [out[k] for k in sorted(out, reverse=True)]
