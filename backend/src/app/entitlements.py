"""What an installation lets each account use.

EVbro as anybody installs it has no restriction: every account can use everything, and this module answers «yes» to every
question. An installation may plug in a rule of its own (`set_checker`) to switch some areas off for some accounts; this module
only asks, it knows no rule and stores nothing.

The areas that can be switched off are listed in `FEATURES`. Whatever is not there (the car's state, the orders to the car, the
recent history, the account's own data and its deletion) is always available.
"""
from __future__ import annotations

import logging
from typing import Awaitable, Callable, Dict, List, Optional

from fastapi import Depends, HTTPException, Request
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import AppUser
from app.db.session import get_db
from app.i18n import _
from app.security import auth

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

Checker = Callable[[AsyncSession, AppUser, str], Awaitable[bool]]
_checker: Optional[Checker] = None


def set_checker(checker: Optional[Checker]) -> None:
    """Plugs in (or removes, with None) the installation's own rule."""
    global _checker
    _checker = checker


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
