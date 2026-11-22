"""Why accounts are deleted. A manager deleting its own account may tick some reasons and leave a comment (both optional);
the administrator reads them in the console (Gestores → «Por qué se borran las cuentas»).

The answer is the one thing that outlives the account, and it carries its USERNAME, so that the administrator knows who said
what (the owner's choice, 2026-11-22); with it go how long the account lived and how many cars and member accounts it had. The
dialog does not say it (the owner took that line out the same day); /legal and the manual do. Nothing answered, nothing kept.
"""
from __future__ import annotations

import datetime
from typing import Any, Dict, Iterable, List, Optional

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import AccountExitSurvey
from app.services.admin_messages import clean
from app.timeutil import utcnow

# The boxes of the dialog (their texts live in the page and in the console; only the key is stored)
REASONS = ("not_interested", "dislike", "not_working", "missing_features", "hard_to_use", "privacy", "other_app", "sold_car",
           "just_testing", "other")
COMMENT_MAX = 1000


def valid_reasons(reasons: Optional[Iterable[Any]]) -> List[str]:
    """The known keys among `reasons`, once each and in the order of the dialog. Anything else is dropped without complaint:
    an old page or a mangled answer must never stop somebody from deleting their account."""
    asked = {r for r in (reasons or []) if isinstance(r, str)}
    return [r for r in REASONS if r in asked]


async def record(db: AsyncSession, username: Optional[str], reasons: Optional[Iterable[Any]], comment: Optional[str],
                 account_created_at: Optional[datetime.datetime], removed: Dict[str, int]) -> bool:
    """Adds the answer to the session (the caller commits it with the deletion). Nothing ticked and nothing typed: nothing kept."""
    keys, text = valid_reasons(reasons), clean(comment or "")[:COMMENT_MAX]
    if not keys and not text:
        return False
    now = utcnow()
    age = max(0, (now - account_created_at).days) if account_created_at else None
    db.add(AccountExitSurvey(created_at=now, username=(username or "")[:64] or None, reasons=keys, comment=text or None, account_age_days=age,
                             vehicles=int(removed.get("vehicles") or 0), members=int(removed.get("members") or 0)))
    return True


async def summary(db: AsyncSession, limit: int = 200) -> Dict[str, Any]:
    """For the console: how many answers there are, how many times each reason was ticked, and the latest answers."""
    total = (await db.execute(select(func.count()).select_from(AccountExitSurvey))).scalar_one()
    counts = {key: 0 for key in REASONS}
    for (reasons,) in (await db.execute(select(AccountExitSurvey.reasons))).all():
        for key in reasons or []:
            if key in counts:
                counts[key] += 1
    rows = (await db.execute(select(AccountExitSurvey).order_by(AccountExitSurvey.id.desc()).limit(limit))).scalars().all()
    return {"total": total, "reasons": counts,
            "entries": [{"id": r.id, "created_at": r.created_at.isoformat() if r.created_at else None, "username": r.username,
                         "reasons": list(r.reasons or []),
                         "comment": r.comment, "account_age_days": r.account_age_days, "vehicles": r.vehicles, "members": r.members}
                        for r in rows]}
