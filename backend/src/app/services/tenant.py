"""Deleting a manager's whole space: cars with everything that hangs from them, member accounts, geofences, Ebro account and files."""
from __future__ import annotations

import logging
import os
from typing import Dict, List

from sqlalchemy import delete, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import (
    AppUser,
    AuthSession,
    Base,
    CatalogEntry,
    ChargePoint,
    ChargingSession,
    DeviceAccess,
    Drive,
    DrivePosition,
    EbroAccount,
    Geofence,
    MaintenanceItem,
    MaintenanceLog,
    MqttLink,
    NotificationConfig,
    NotificationLog,
    PasskeyCredential,
    PushSubscription,
    Vehicle,
    VehicleDocument,
)
from app.services import uploads
from app.services.accounts import remove_vehicle_data
from app.services.ebro_sessions import remove_account_session

_LOGGER = logging.getLogger(__name__)


async def delete_member(session: AsyncSession, member_id: int) -> None:
    """Deletes one account with everything keyed by it (sessions, links, devices, passkeys, notification channels and history)."""
    await session.execute(delete(AuthSession).where(AuthSession.user_id == member_id))
    await session.execute(delete(DeviceAccess).where(DeviceAccess.user_id == member_id))
    await session.execute(delete(PushSubscription).where(PushSubscription.user_id == member_id))
    await session.execute(delete(PasskeyCredential).where(PasskeyCredential.user_id == member_id))
    await session.execute(delete(NotificationConfig).where(NotificationConfig.user_id == member_id))     # holds channel tokens
    await session.execute(delete(NotificationLog).where(NotificationLog.user_id == member_id))
    await session.execute(delete(MqttLink).where(MqttLink.user_id == member_id))                         # holds the broker's password
    # What the account proposed to the shared catalogue stays (it is everybody's, and approved by the admin), without its author
    await session.execute(update(CatalogEntry).where(CatalogEntry.proposed_by == member_id).values(proposed_by=None))
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
    for vin in vins:                      # its folder: an old copy of the Ebro session and the taskId are credentials
        try:
            remove_vehicle_data(vin)
        except ValueError:
            pass


async def delete_manager_space(session: AsyncSession, manager_id: int) -> Dict[str, int]:
    """Deletes everything of a manager (and the manager). Returns what was removed, for the audit log. Does not commit."""
    vins: List[str] = [v for (v,) in (await session.execute(select(Vehicle.vin).where(Vehicle.owner_id == manager_id))).all()]
    member_ids = [m for (m,) in (await session.execute(select(AppUser.id).where(AppUser.parent_id == manager_id))).all()]
    await delete_vehicles(session, vins)
    for member_id in member_ids:
        await delete_member(session, member_id)
    await session.execute(delete(Geofence).where(Geofence.owner_id == manager_id))
    accounts = [a for (a,) in (await session.execute(select(EbroAccount.id).where(EbroAccount.user_id == manager_id))).all()]
    await session.execute(delete(EbroAccount).where(EbroAccount.user_id == manager_id))
    for account_id in accounts:          # the Ebro session its cars shared (services/ebro_sessions.py)
        remove_account_session(account_id)
    await delete_member(session, manager_id)
    removed = {"vehicles": len(vins), "members": len(member_ids)}
    # Something a person decided, not a fault of the code: it stays in the log and is not reported to the project
    _LOGGER.warning("Space of manager #%s deleted: %s", manager_id, removed, extra={"telemetry": False})
    return removed


async def vins_of(session: AsyncSession, manager_id: int) -> List[str]:
    return [v for (v,) in (await session.execute(select(Vehicle.vin).where(Vehicle.owner_id == manager_id))).all()]


def forget_vehicle_state(vin: str) -> None:
    """What the server keeps in memory about a car that no longer exists: its session and PIN, its last reading (position
    included) and the open trip or charge the detectors were following. Without this it would stay until the next restart, and a
    car registered again would inherit the trip of rows that are gone."""
    from app.services import charge_detector, gps_positions, refuel_detector, trip_detector
    from app.services.charge_manager import ChargeManager
    from app.services.poller import VehiclePoller

    VehiclePoller.get_instance().forget_vehicle(vin)
    for module in (charge_detector, gps_positions, refuel_detector, trip_detector):     # a new per-car store is covered automatically
        for name, value in vars(module).items():
            if name.startswith("_") and name.isupper() and isinstance(value, dict):
                value.pop(vin, None)
    manager = ChargeManager.get_instance()
    for store in (manager._locks, manager._holds, manager._last_auto):
        store.pop(vin, None)


async def release_manager_runtime(manager_id: int, vins: List[str]) -> None:
    """Closes what was running for a manager whose space has just been deleted (after the commit): the car's live channel and
    its «stopped» mark, the link to their MQTT broker, their Traccar connections and the cars' state in memory."""
    from app.config import get_data_dir
    from app.services.car_events import CarEventHub
    from app.services.mqtt_hub import MqttHub
    from app.services.traccar import TraccarHub

    hub = CarEventHub.get_instance()
    try:
        await hub.stop_owner(manager_id)
    except Exception as exc:                                  # noqa: BLE001 — the data is already gone: the rest still has to be closed
        _LOGGER.warning("Could not stop the car channel of the deleted manager #%s: %s", manager_id, type(exc).__name__)
    hub._services.pop(manager_id, None)
    try:
        os.remove(os.path.join(get_data_dir(), "car_channel", f"stopped-{manager_id}"))
    except OSError:
        pass
    for vin in vins:
        forget_vehicle_state(vin)
    for reload in (lambda: MqttHub.get_instance().reload_user(manager_id), lambda: TraccarHub.get_instance().reload()):
        try:
            await reload()
        except Exception as exc:                              # noqa: BLE001
            _LOGGER.warning("Could not close a connection of the deleted manager #%s: %s", manager_id, type(exc).__name__)
