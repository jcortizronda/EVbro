"""Invitation tokens for the hosted service: created by an admin, single use, expiring. Only the hash is stored."""
from __future__ import annotations

import datetime
import secrets
from typing import Optional, Tuple

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Invite
from app.security.auth import hash_token

TOKEN_PREFIX = "ebm_"


def _now() -> datetime.datetime:
    return datetime.datetime.utcnow()


def invite_state(invite: Invite, now: Optional[datetime.datetime] = None) -> str:
    now = now or _now()
    if invite.used_at:
        return "used"
    if invite.revoked:
        return "revoked"
    if invite.expires_at <= now:
        return "expired"
    return "active"


async def create_invite(db: AsyncSession, admin_id: int, note: Optional[str], days: int, vehicle_limit: Optional[int] = None,
                        member_limit: Optional[int] = None, storage_limit_mb: Optional[int] = None) -> Tuple[Invite, str]:
    """Creates an invitation and returns it with the plain token, which is shown once and cannot be recovered afterwards."""
    token = TOKEN_PREFIX + secrets.token_urlsafe(24)
    invite = Invite(token_hash=hash_token(token), note=(note or "").strip() or None, created_by=admin_id,
                    expires_at=_now() + datetime.timedelta(days=days), vehicle_limit=vehicle_limit, member_limit=member_limit,
                    storage_limit_mb=storage_limit_mb)
    db.add(invite)
    await db.commit()
    await db.refresh(invite)
    return invite, token


async def find_valid_invite(db: AsyncSession, token: str) -> Optional[Invite]:
    invite = (await db.execute(select(Invite).where(Invite.token_hash == hash_token((token or "").strip())))).scalar_one_or_none()
    return invite if invite is not None and invite_state(invite) == "active" else None


async def mark_used(db: AsyncSession, invite_id: int, user_id: int) -> bool:
    """Atomically spends the invitation: False if somebody else spent (or revoked) it first."""
    res = await db.execute(update(Invite).where(Invite.id == invite_id, Invite.used_at.is_(None), Invite.revoked.is_(False))
                           .values(used_at=_now(), used_by=user_id))
    return (res.rowcount or 0) == 1
