"""One way to say «which dates» for every list and statistic: a preset (`period`) or a custom range (`from`/`to`).

Presets: `all`, `month`, `last_month`, `year`, `last_year`, `6m`, `12m`, `7d`, `30d`, `90d`. A custom range comes as `period=custom`
(or no preset) with `from` and `to` as `YYYY-MM` (whole months) or `YYYY-MM-DD` (days); `to` is inclusive. Month and day boundaries
follow the server's time zone (`TIMEZONE`), and the bounds come back as naive UTC datetimes, which is how the tables store time.
The frontend counterpart is `features/period-picker.js`.
"""
from __future__ import annotations

import calendar
import datetime
import re
from typing import Optional, Tuple

from fastapi import HTTPException

from app.config import get_timezone

PRESETS = ("all", "month", "last_month", "year", "last_year", "6m", "12m", "7d", "30d", "90d", "custom")
_MONTH = re.compile(r"^(\d{4})-(\d{2})$")
_DAY = re.compile(r"^(\d{4})-(\d{2})-(\d{2})$")

Bounds = Tuple[Optional[datetime.datetime], Optional[datetime.datetime]]     # [start, end) in naive UTC; None = open


def _to_utc(local: datetime.datetime, tz) -> datetime.datetime:
    return local.replace(tzinfo=tz).astimezone(datetime.timezone.utc).replace(tzinfo=None)


def _parse_edge(value: str, *, end: bool) -> datetime.datetime:
    """A local date for one edge: `YYYY-MM` is the first day of that month (or the first day of the next one for `end`)."""
    m = _MONTH.match(value)
    if m:
        y, mo = int(m.group(1)), int(m.group(2))
        if not 1 <= mo <= 12:
            raise HTTPException(status_code=422, detail="Mes no válido (AAAA-MM).")
        if end:
            return datetime.datetime(y + (mo == 12), 1 if mo == 12 else mo + 1, 1)
        return datetime.datetime(y, mo, 1)
    d = _DAY.match(value)
    if d:
        y, mo, day = (int(x) for x in d.groups())
        if not (1 <= mo <= 12 and 1 <= day <= calendar.monthrange(y, mo)[1]):
            raise HTTPException(status_code=422, detail="Fecha no válida (AAAA-MM-DD).")
        return datetime.datetime(y, mo, day) + (datetime.timedelta(days=1) if end else datetime.timedelta())
    raise HTTPException(status_code=422, detail="Fecha no válida: usa AAAA-MM o AAAA-MM-DD.")


def resolve(period: Optional[str], frm: Optional[str] = None, to: Optional[str] = None, now: Optional[datetime.datetime] = None) -> Bounds:
    """[start, end) for a preset or a custom range. Unknown presets are a 422, like the old per-route checks."""
    tz = get_timezone()
    now_utc = now or datetime.datetime.utcnow()
    local_now = now_utc.replace(tzinfo=datetime.timezone.utc).astimezone(tz).replace(tzinfo=None)
    key = "custom" if (frm or to) else ((period or "").strip() or "all")         # explicit dates always win over a preset
    if key not in PRESETS:
        raise HTTPException(status_code=422, detail="Periodo no válido.")
    if key == "custom":
        start = _to_utc(_parse_edge(frm.strip(), end=False), tz) if frm and frm.strip() else None
        end = _to_utc(_parse_edge(to.strip(), end=True), tz) if to and to.strip() else None
        if start and end and end <= start:
            raise HTTPException(status_code=422, detail="El final del periodo es anterior al inicio.")
        return start, end
    if key == "all":
        return None, None
    y, m = local_now.year, local_now.month
    if key == "month":
        return _to_utc(datetime.datetime(y, m, 1), tz), None
    if key == "last_month":
        first_this = datetime.datetime(y, m, 1)
        first_last = datetime.datetime(y - (m == 1), 12 if m == 1 else m - 1, 1)
        return _to_utc(first_last, tz), _to_utc(first_this, tz)
    if key == "year":
        return _to_utc(datetime.datetime(y, 1, 1), tz), None
    if key == "last_year":
        return _to_utc(datetime.datetime(y - 1, 1, 1), tz), _to_utc(datetime.datetime(y, 1, 1), tz)
    days = {"6m": 183, "12m": 365, "7d": 7, "30d": 30, "90d": 90}[key]
    return now_utc - datetime.timedelta(days=days), None


def inside(value: Optional[datetime.datetime], bounds: Bounds) -> bool:
    """Python-side test for rows already loaded."""
    if value is None:
        return False
    start, end = bounds
    return (start is None or value >= start) and (end is None or value < end)


def condition(column, bounds: Bounds):
    """SQLAlchemy condition for a datetime column (`True` when the range is open on both sides)."""
    from sqlalchemy import and_, true
    start, end = bounds
    conds = []
    if start is not None:
        conds.append(column >= start)
    if end is not None:
        conds.append(column < end)
    return and_(*conds) if conds else true()


def is_everything(period: Optional[str], frm: Optional[str], to: Optional[str]) -> bool:
    return (period or "all") == "all" and not frm and not to
