"""«Now» in naive UTC: the convention of every timestamp of the database.

`datetime.datetime.utcnow()` returns exactly that, but Python marks it deprecated from 3.12 on (a warning on every call). This is
the same value without the warning. `core/` and `vehicle/` keep their own helpers: they mirror the Home Assistant integration.
"""
from __future__ import annotations

import datetime


def utcnow() -> datetime.datetime:
    return datetime.datetime.now(datetime.timezone.utc).replace(tzinfo=None)
