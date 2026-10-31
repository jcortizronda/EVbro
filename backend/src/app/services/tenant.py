"""Deleting a manager's whole space: cars with everything that hangs from them, member accounts, geofences, Ebro account and files."""
from __future__ import annotations

import logging
from typing import Dict, List

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import (
    AppUser,
    AuthSession,
    Base,
    ChargePoint,
    ChargingSession,
    DeviceAccess,
    Drive,
    DrivePosition,
    EbroAccount,
    Geofence,
    MaintenanceItem,
    MaintenanceLog,
    NotificationConfig,
    NotificationLog,
    PasskeyCredential,
    PushSubscription,
    Vehicle,
    VehicleDocument,
)
from app.services import uploads

_LOGGER = logging.getLogger(__name__)


async def delete_member(session: AsyncSession, member_id: int) -> None:
    """Deletes one account with everything keyed by it (sessions, links, devices, passkeys, notification channels and history)."""
    await session.execute(delete(AuthSession).where(AuthSession.user_id == member_id))
    await session.execute(delete(DeviceAccess).where(DeviceAccess.user_id == member_id))
    await session.execute(delete(PushSubscription).where(PushSubscription.user_id == member_id))
    await session.execute(delete(PasskeyCredential).where(PasskeyCredential.user_id == member_id))
    await session.execute(delete(NotificationConfig).where(NotificationConfig.user_id == member_id))     # holds channel tokens
    await session.execute(delete(NotificationLog).where(NotificationLog.user_id == member_id))
    await session.execute(delete(AppUser).where(AppUser.id == member_id))


async def delete_vehicles(session: AsyncSession, vins: List[str]) -> None:
    """Deletes cars with everything that hangs from them (history, expenses, parts, links...) and their files. Does not commit."""
    if not vins:
        return
    # files on disk first (their names come from rows that are about to go)
    photos = [p for (p,) in (await session.execute(select(Vehicle.photo_filename).where(Vehicle.vin.in_(vins)))).all() if p]
    invoices = [i for (i,) in (await session.execute(
        select(MaintenanceLog.invoice_filename).join(MaintenanceItem, MaintenanceLog.item_id == MaintenanceItem.id)
        .where(MaintenanceItem.vin.in_(vins)))).all() if i]
    documents = [d for (d,) in (await session.execute(select(VehicleDocument.filename).where(VehicleDocument.vin.in_(vins)))).all() if d]

    # children keyed by something other than the vin
    await session.execute(delete(DrivePosition).where(DrivePosition.drive_id.in_(select(Drive.id).where(Drive.vin.in_(vins)))))
    await session.execute(delete(ChargePoint).where(ChargePoint.charging_session_id.in_(select(ChargingSession.id).where(ChargingSession.vin.in_(vins)))))
    await session.execute(delete(MaintenanceLog).where(MaintenanceLog.item_id.in_(select(MaintenanceItem.id).where(MaintenanceItem.vin.in_(vins)))))
    # every other table with a `vin` column (a new one is covered automatically)
    for table in Base.metadata.sorted_tables:
        if "vin" in table.c and table.name != "vehicles":
            await session.execute(delete(table).where(table.c.vin.in_(vins)))
    await session.execute(delete(Vehicle).where(Vehicle.vin.in_(vins)))

    for folder, names in ((uploads.PHOTOS_FOLDER, photos), (uploads.INVOICES_FOLDER, invoices), (uploads.DOCUMENTS_FOLDER, documents)):
        for name in names:
            uploads.remove(folder, name)


async def delete_manager_space(session: AsyncSession, manager_id: int) -> Dict[str, int]:
    """Deletes everything of a manager (and the manager). Returns what was removed, for the audit log. Does not commit."""
    vins: List[str] = [v for (v,) in (await session.execute(select(Vehicle.vin).where(Vehicle.owner_id == manager_id))).all()]
    member_ids = [m for (m,) in (await session.execute(select(AppUser.id).where(AppUser.parent_id == manager_id))).all()]
    await delete_vehicles(session, vins)
    for member_id in member_ids:
        await delete_member(session, member_id)
    await session.execute(delete(Geofence).where(Geofence.owner_id == manager_id))
    await session.execute(delete(EbroAccount).where(EbroAccount.user_id == manager_id))
    await delete_member(session, manager_id)
    removed = {"vehicles": len(vins), "members": len(member_ids)}
    _LOGGER.warning("Espacio del gestor #%s borrado: %s", manager_id, removed)
    return removed
