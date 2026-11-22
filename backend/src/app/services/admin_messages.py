"""Messages from the server administrator to the manager accounts (maintenance windows, news about the service).

They travel the road every other notice travels (services/notification_service.py): each recipient gets the message in its notice
centre (the bell of the header) and through the channels it set up (phone notification, Telegram, Pushover, webhook). An account
may SILENCE them in Ajustes → Avisos: the message is then kept in its notice centre and nothing is sent.

Plain text only: the title and the body are stored and shown as typed, never as HTML. The administrator still sees nothing of the
accounts: only how many received the message.
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import AdminMessage, AppUser
from app.security.auth import account_usable
from app.services.notification_service import QUIET_FLAG, NotificationService
from app.timeutil import utcnow

CATEGORY = "admin_message"
TITLE_MAX = 80
BODY_MAX = 1000          # a phone notification carries about 4 kB in all


class MessageError(ValueError):
    """The message cannot be sent as asked (shown to the administrator)."""


def clean(text: str) -> str:
    """The text as typed, without control characters (line breaks are kept) or spaces around."""
    kept = "".join(ch for ch in (text or "").replace("\r\n", "\n").replace("\r", "\n") if ch == "\n" or ch.isprintable())
    return "\n".join(line.rstrip() for line in kept.split("\n")).strip()


async def audience(db: AsyncSession, manager_ids: Optional[List[int]], include_members: bool) -> List[AppUser]:
    """The accounts a message goes to: every manager (`manager_ids` None) or the chosen ones, plus their member accounts when asked.
    Only accounts that can sign in: a deactivated or expired one would find an old message the day it came back."""
    stmt = select(AppUser).where(AppUser.role == "manager", AppUser.is_admin.is_(False))
    if manager_ids is not None:
        stmt = stmt.where(AppUser.id.in_(manager_ids))
    managers = [u for u in (await db.execute(stmt.order_by(AppUser.id))).scalars().all() if await account_usable(db, u)]
    if manager_ids is not None and len(managers) != len(set(manager_ids)):
        raise MessageError("Alguno de los gestores elegidos no existe o no puede entrar.")
    accounts = list(managers)
    if include_members and managers:
        members = (await db.execute(select(AppUser).where(AppUser.role == "member", AppUser.parent_id.in_([m.id for m in managers]))
                                    .order_by(AppUser.id))).scalars().all()
        accounts += [u for u in members if await account_usable(db, u)]
    return accounts


async def send(db: AsyncSession, admin: Optional[AppUser], title: str, body: str, manager_ids: Optional[List[int]] = None,
               include_members: bool = False) -> AdminMessage:
    """Sends the message and returns the administrator's record of it. A channel that fails does not stop the rest: the message
    is in the notice centre of every recipient either way."""
    title, body = clean(title).replace("\n", " "), clean(body)
    if not title or not body:
        raise MessageError("El mensaje necesita un título y un texto.")
    if len(title) > TITLE_MAX or len(body) > BODY_MAX:
        raise MessageError("El mensaje es demasiado largo.")
    accounts = await audience(db, manager_ids, include_members)
    if not accounts:
        raise MessageError("No hay ninguna cuenta a la que enviarlo.")

    ids = [u.id for u in accounts]                            # plain values: the session commits while sending
    record = AdminMessage(created_at=utcnow(), admin_id=admin.id if admin else None, username=admin.username if admin else "?",
                          title=title, body=body, audience="all" if manager_ids is None else "selected",
                          targets=None if manager_ids is None else ", ".join(u.username for u in accounts if u.role == "manager"),
                          include_members=include_members, recipients=len(ids))
    db.add(record)
    await db.flush()                                          # its id tells one message from the next on the phone
    tag, flag, pushed, silenced = f"{CATEGORY}-{record.id}", QUIET_FLAG[CATEGORY], 0, 0
    for uid in ids:
        config = await NotificationService.get_or_create_config(db, uid)
        if getattr(config, flag, True) is False:
            silenced += 1
        results = await NotificationService._dispatch_to_user(db, uid, CATEGORY, title, body, tag=tag)
        if any(r.get("success") for r in results):
            pushed += 1
    record.pushed, record.silenced = pushed, silenced
    await db.commit()
    await db.refresh(record)
    return record


def out(m: AdminMessage) -> Dict[str, Any]:
    return {"id": m.id, "created_at": m.created_at.isoformat() if m.created_at else None, "username": m.username, "title": m.title,
            "body": m.body, "audience": m.audience, "targets": m.targets, "include_members": bool(m.include_members),
            "recipients": m.recipients, "pushed": m.pushed, "silenced": m.silenced}


async def recent(db: AsyncSession, limit: int = 100) -> List[Dict[str, Any]]:
    rows = (await db.execute(select(AdminMessage).order_by(AdminMessage.id.desc()).limit(limit))).scalars().all()
    return [out(m) for m in rows]
