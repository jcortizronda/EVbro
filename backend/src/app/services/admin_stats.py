"""Statistics for the administrator: how big and how used the server is.

Only aggregates. The administrator never sees a manager's cars, so nothing here carries a VIN, a plate, a position, a car name or what was
written in a history; per manager there are only counts, and counts of commands are not tied to a car."""
from __future__ import annotations

import datetime
import os
import time
from typing import Any, Dict

from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_data_dir
from app.db.models import (
    AppUser,
    AuthSession,
    ChargingSession,
    CommandAudit,
    Drive,
    EbroAccount,
    Expense,
    Invite,
    MaintenanceLog,
    NotificationLog,
    Refuel,
    TelemetrySnapshot,
    Vehicle,
    VehiclePart,
)
from app.services import invites, storage

_STARTED = time.time()
WEEKS = 8


def _now() -> datetime.datetime:
    return datetime.datetime.utcnow()


async def _count(session: AsyncSession, model, *where) -> int:
    return (await session.execute(select(func.count()).select_from(model).where(*where))).scalar_one()


def _dir_size(path: str) -> int:
    total = 0
    for root, _dirs, files in os.walk(path):
        for name in files:
            try:
                total += os.path.getsize(os.path.join(root, name))
            except OSError:
                pass
    return total


async def _database_bytes(session: AsyncSession) -> int | None:
    dialect = session.bind.dialect.name if session.bind is not None else ""
    try:
        if dialect == "postgresql":
            return (await session.execute(text("SELECT pg_database_size(current_database())"))).scalar_one()
        if dialect == "sqlite":
            pages = (await session.execute(text("PRAGMA page_count"))).scalar_one()
            size = (await session.execute(text("PRAGMA page_size"))).scalar_one()
            return pages * size
    except Exception:       # noqa: BLE001 — a statistic must never break the page
        return None
    return None


async def collect(session: AsyncSession, now: datetime.datetime | None = None) -> Dict[str, Any]:
    now = now or _now()
    d1, d7, d30 = (now - datetime.timedelta(days=n) for n in (1, 7, 30))

    managers = await _count(session, AppUser, AppUser.role == "manager")
    members = await _count(session, AppUser, AppUser.role == "member")
    admins = await _count(session, AppUser, AppUser.role == "admin")
    inactive = await _count(session, AppUser, AppUser.role != "admin", AppUser.is_active.is_(False))
    expiring = await _count(session, AppUser, AppUser.role == "manager", AppUser.expires_at.is_not(None), AppUser.expires_at > now,
                            AppUser.expires_at <= now + datetime.timedelta(days=30))
    vehicles = await _count(session, Vehicle)
    manual = await _count(session, Vehicle, Vehicle.is_manual.is_(True))

    states = {"active": 0, "used": 0, "expired": 0, "revoked": 0}
    for inv in (await session.execute(select(Invite))).scalars():
        states[invites.invite_state(inv, now)] += 1

    # new accounts per week (oldest first), to see the growth
    per_week = []
    start = (now - datetime.timedelta(days=now.weekday())).replace(hour=0, minute=0, second=0, microsecond=0) - datetime.timedelta(weeks=WEEKS - 1)
    for i in range(WEEKS):
        a, b = start + datetime.timedelta(weeks=i), start + datetime.timedelta(weeks=i + 1)
        per_week.append({"week_start": a.date().isoformat(),
                         "new_accounts": await _count(session, AppUser, AppUser.role != "admin", AppUser.created_at >= a, AppUser.created_at < b)})

    # usage per manager: counts only
    per_manager = []
    for m in (await session.execute(select(AppUser).where(AppUser.role == "manager").order_by(AppUser.id))).scalars():
        own = select(Vehicle.vin).where(Vehicle.owner_id == m.id)
        per_manager.append({
            "username": m.username, "vehicles": await _count(session, Vehicle, Vehicle.owner_id == m.id),
            "members": await _count(session, AppUser, AppUser.parent_id == m.id),
            "commands_7d": await _count(session, CommandAudit, CommandAudit.vin.in_(own), CommandAudit.timestamp >= d7),
            "storage_bytes": await storage.storage_usage(session, m.id),
            "last_login": m.last_login.isoformat() if m.last_login else None, "is_active": m.is_active})

    snapshots_oldest = (await session.execute(select(func.min(TelemetrySnapshot.timestamp)))).scalar_one_or_none()
    data_dir = get_data_dir()
    return {
        "generated_at": now.isoformat(),
        "accounts": {"admins": admins, "managers": managers, "members": members, "total": managers + members, "inactive": inactive,
                     "read_only": await _count(session, AppUser, AppUser.role != "admin", AppUser.read_only.is_(True)),
                     "expiring_30d": expiring, "new_30d": await _count(session, AppUser, AppUser.role != "admin", AppUser.created_at >= d30)},
        "vehicles": {"total": vehicles, "connected": vehicles - manual, "manual": manual,
                     "ebro_accounts": await _count(session, EbroAccount, EbroAccount.is_active.is_(True))},
        "invitations": states,
        "activity": {"logins_24h": await _count(session, AppUser, AppUser.role != "admin", AppUser.last_login >= d1),
                     "logins_7d": await _count(session, AppUser, AppUser.role != "admin", AppUser.last_login >= d7),
                     "logins_30d": await _count(session, AppUser, AppUser.role != "admin", AppUser.last_login >= d30),
                     "sessions_open": await _count(session, AuthSession, AuthSession.expires_at > now)},
        "usage": {"commands_24h": await _count(session, CommandAudit, CommandAudit.timestamp >= d1),
                  "commands_7d": await _count(session, CommandAudit, CommandAudit.timestamp >= d7),
                  "commands_failed_7d": await _count(session, CommandAudit, CommandAudit.timestamp >= d7, CommandAudit.success.is_(False)),
                  "notifications_24h": await _count(session, NotificationLog, NotificationLog.timestamp >= d1),
                  "notifications_failed_24h": await _count(session, NotificationLog, NotificationLog.timestamp >= d1, NotificationLog.success.is_(False)),
                  "trips_7d": await _count(session, Drive, Drive.start_time >= d7), "charges_7d": await _count(session, ChargingSession, ChargingSession.start_time >= d7),
                  "refuels_30d": await _count(session, Refuel, Refuel.timestamp >= d30), "expenses_30d": await _count(session, Expense, Expense.date >= d30)},
        "data": {"trips": await _count(session, Drive), "charges": await _count(session, ChargingSession), "snapshots": await _count(session, TelemetrySnapshot),
                 "expenses": await _count(session, Expense), "refuels": await _count(session, Refuel), "parts": await _count(session, VehiclePart),
                 "maintenance_logs": await _count(session, MaintenanceLog),
                 "oldest_snapshot": snapshots_oldest.isoformat() if snapshots_oldest else None},
        "storage": {"database_bytes": await _database_bytes(session), "files_bytes": _dir_size(data_dir) if os.path.isdir(data_dir) else 0},
        "server": {"uptime_s": int(time.time() - _STARTED)},
        "growth_weekly": per_week,
        "managers": per_manager,
    }
