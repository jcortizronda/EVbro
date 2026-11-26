"""What an installation lets each account use.

EVbro as anybody installs it has no restriction: every account can use everything, and this module answers «yes» to every
question. An installation may plug in a rule of its own (`set_checker`) to switch some areas off for some accounts; this module
only asks, it knows no rule and stores nothing.

The areas that can be switched off are listed in `FEATURES`. Whatever is not there (the car's state, the orders to the car, the
recent history, the account's own data and its deletion) is always available.
"""
from __future__ import annotations

import datetime
import logging
from typing import Awaitable, Callable, Dict, List, Optional

from fastapi import Depends, HTTPException, Request
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import AppUser
from app.db.session import get_db
from app.i18n import _
from app.security import auth
from app.timeutil import utcnow

_LOGGER = logging.getLogger(__name__)

FEATURES = (
    "history_full",      # trips, charges and timeline older than HISTORY_DAYS
    "stats",             # statistics
    "costs",             # costs and expenses
    "maintenance",       # maintenance plan, EVbro's recommendations and parts
    "automation",        # what EVbro does by itself on a timetable: the charge target and the scheduled climate
    "integrations",      # the manager's own MQTT broker (Home Assistant) and Traccar
    "members",           # member accounts
    "book",              # the car book (PDF)
)
HISTORY_DAYS = 30        # how far back an account without `history_full` sees
HEADER = "X-EVbro-Feature"
HISTORY_FLOOR = "history_floor"      # key in `session.info`: reads of the history tables in that session start at this moment (db/history_view.py)

Checker = Callable[[AsyncSession, AppUser, str], Awaitable[bool]]
_checker: Optional[Checker] = None


def set_checker(checker: Optional[Checker]) -> None:
    """Plugs in (or removes, with None) the installation's own rule."""
    global _checker
    _checker = checker


def has_rule() -> bool:
    return _checker is not None


async def allowed(session: AsyncSession, user: AppUser, feature: str) -> bool:
    """May this account use `feature`? Without a rule, always. A rule that fails must never lock anybody out: it counts as «yes»."""
    if _checker is None:
        return True
    try:
        return bool(await _checker(session, user, feature))
    except Exception as e:      # noqa: BLE001 — the installation's own code: whatever it does wrong, the account keeps working
        _LOGGER.error("The entitlement rule failed for «%s» (%s): allowed.", feature, type(e).__name__)
        return True


async def disabled_for(session: AsyncSession, user: AppUser) -> List[str]:
    """The areas this account cannot use (empty for everybody unless the installation has a rule)."""
    if _checker is None:
        return []
    return [f for f in FEATURES if not await allowed(session, user, f)]


async def owner_allowed(owner_id: Optional[int], feature: str) -> bool:
    """The same question for what runs without anybody logged in (timetables, alerts, the manager's own connections): may the
    account that owns the car use `feature`? Without a rule it does not even look the account up. A car without an owner, or
    whose owner is gone, has nobody to switch anything off for: yes."""
    if _checker is None or owner_id is None:
        return True
    from app.db.session import async_session_factory
    try:
        async with async_session_factory() as session:
            user = await session.get(AppUser, owner_id)
            return True if user is None else await allowed(session, user, feature)
    except Exception as e:      # noqa: BLE001 — same promise as `allowed`: a failure here never switches anything off
        _LOGGER.error("The entitlement rule could not be asked for «%s» (%s): allowed.", feature, type(e).__name__)
        return True


async def vin_allowed(vin: str, feature: str) -> bool:
    """`owner_allowed` for the owner of a car."""
    if _checker is None:
        return True
    from sqlalchemy import select

    from app.db.models import Vehicle
    from app.db.session import async_session_factory
    try:
        async with async_session_factory() as session:
            owner_id = (await session.execute(select(Vehicle.owner_id).where(Vehicle.vin == vin))).scalar_one_or_none()
    except Exception as e:      # noqa: BLE001
        _LOGGER.error("The owner of a car could not be read for «%s» (%s): allowed.", feature, type(e).__name__)
        return True
    return await owner_allowed(owner_id, feature)


async def refresh_services() -> None:
    """Brings the manager's own connections (MQTT, Traccar) in line with what each account may use now. The installation calls it
    when its rule changes for somebody, and the poller every few minutes while there is a rule (a rule may depend on the date)."""
    if _checker is None:
        return
    from app.services.mqtt_hub import MqttHub
    from app.services.traccar import TraccarHub
    for name, sync in (("MQTT", MqttHub.get_instance().sync_allowed), ("Traccar", TraccarHub.get_instance().sync_allowed)):
        try:
            await sync()
        except Exception as e:  # noqa: BLE001 — one service failing to resync must not stop the other, nor the caller
            _LOGGER.error("%s could not be brought in line with the accounts' areas: %s", name, type(e).__name__)


async def history_floor(session: AsyncSession, user: AppUser) -> Optional[datetime.datetime]:
    """From when this account sees its history: `None` (all of it) or the moment `HISTORY_DAYS` ago. Nothing is ever deleted for
    this: it is only what the account is shown."""
    if _checker is None or await allowed(session, user, "history_full"):
        return None
    return utcnow() - datetime.timedelta(days=HISTORY_DAYS)


async def limit_history(request: Request, user: AppUser = Depends(auth.require_auth), session: AsyncSession = Depends(get_db)) -> None:
    """Dependency of the routers that show history: the reads of an account without `history_full` only find the last
    `HISTORY_DAYS` days (db/history_view.py applies it to every query of the request). Requests that change data are left alone:
    what they check against (a charge already written down, a drive being merged) must be the whole table."""
    if request.method not in ("GET", "HEAD"):
        return
    floor = await history_floor(session, user)
    if floor is not None:
        session.info[HISTORY_FLOOR] = floor


def require(feature: str, removal_always: bool = True):
    """Route dependency: 403 (with the `X-EVbro-Feature` header naming the area) when the account does not have `feature`.
    `DELETE` passes by default: an account that loses an area must still be able to remove what it had set up in it."""
    assert feature in FEATURES, feature

    async def dependency(request: Request, user: AppUser = Depends(auth.require_auth), session: AsyncSession = Depends(get_db)) -> None:
        if removal_always and request.method == "DELETE":
            return
        if not await allowed(session, user, feature):
            raise HTTPException(status_code=403, detail=_("Tu cuenta no incluye esta función."), headers={HEADER: feature})

    return dependency


def describe() -> Dict[str, object]:
    """What the page needs to know to explain itself (nothing about any rule)."""
    return {"history_days": HISTORY_DAYS}
