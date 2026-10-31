"""What the administrator did, and when: a plain append-only log shown in the console (and the basis of any later data-protection
answer: "who touched this account"). Logs actions, never secrets."""
from __future__ import annotations

import datetime
from typing import Any, Dict, List, Optional

from fastapi import Request
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import AdminAudit, AppUser
from app.security import auth


async def log(db: AsyncSession, admin: Optional[AppUser], action: str, target: str = "", detail: str = "",
              request: Optional[Request] = None, commit: bool = True) -> None:
    db.add(AdminAudit(admin_id=admin.id if admin else None, username=admin.username if admin else "?", action=action[:60], target=(target or "")[:120],
                      detail=(detail or "")[:255], ip=auth.client_ip(request)[:64] if request else None, created_at=datetime.datetime.utcnow()))
    if commit:
        await db.commit()


async def recent(db: AsyncSession, limit: int = 200) -> List[Dict[str, Any]]:
    rows = (await db.execute(select(AdminAudit).order_by(AdminAudit.id.desc()).limit(limit))).scalars().all()
    return [{"id": r.id, "username": r.username, "action": r.action, "target": r.target, "detail": r.detail, "ip": r.ip,
             "created_at": r.created_at.isoformat() if r.created_at else None} for r in rows]
