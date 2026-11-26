"""Is the Ebro cloud answering?

Nothing is asked for this: it only watches how the reads the poller already makes end (rule 6: no extra polling). When for a
while every read fails before getting an answer, from several cars and with none getting through, the cloud (or the way to it) is
taken to be down, and everybody connected is told once; the first read that gets an answer clears it.

What counts as «no answer» is a read that failed on the way (no connection, timeout, an error from the server). A session the cloud
rejects is an answer: that is one account's matter, never an outage. From inside the server an outage of the Ebro cloud and the
loss of the server's own connection look the same; the notice says «does not answer», which is true in both cases.

Kept in memory: after a restart the state is rebuilt by the next reads.
"""
from __future__ import annotations

import datetime
from typing import Any, Dict, Optional, Set

from app.timeutil import utcnow

DOWN_AFTER_S = 600          # how long the failures must have gone on, with no read getting an answer in between
MIN_CARS = 3                # from how many different cars (all of them, on a server with fewer)

_first_failure: Optional[datetime.datetime] = None
_failed_cars: Set[str] = set()
_down_since: Optional[datetime.datetime] = None


def reset() -> None:
    global _first_failure, _down_since
    _first_failure = _down_since = None
    _failed_cars.clear()


def answered(now: Optional[datetime.datetime] = None) -> Optional[str]:
    """A read got an answer from the cloud (with data or without). Returns `"up"` when that ends an outage."""
    was_down = _down_since is not None
    reset()
    return "up" if was_down else None


def unanswered(vin: str, cars_total: int, now: Optional[datetime.datetime] = None) -> Optional[str]:
    """A read of `vin` failed before getting an answer. Returns `"down"` the moment the outage is declared."""
    global _first_failure, _down_since
    now = now or utcnow()
    if _first_failure is None:
        _first_failure = now
    _failed_cars.add(vin)
    if _down_since is not None:
        return None
    needed = max(1, min(MIN_CARS, cars_total))
    if len(_failed_cars) >= needed and (now - _first_failure).total_seconds() >= DOWN_AFTER_S:
        _down_since = _first_failure
        return "down"
    return None


def status() -> Dict[str, Any]:
    """What the page is told: whether the cloud is taken to be down, and since when (UTC)."""
    return {"down": _down_since is not None, "since": _down_since.isoformat() + "Z" if _down_since else None}


def event() -> Dict[str, Any]:
    """The same, as the message every connected page receives."""
    return {"type": "cloud_status", **status()}
