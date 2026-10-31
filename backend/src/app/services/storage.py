"""Storage quota per manager: how much the files of their cars take, and whether one more fits.

Usage = plaintext bytes of the documents, maintenance invoices and photos of the cars the manager owns (`Vehicle.owner_id`). It is charged
to the car's owner whoever uploads (a member with `edit` adding an invoice spends the owner's space). The limit is
`AppUser.storage_limit_mb`: -1 unlimited, 0 no uploads, NULL = the server default (`DEFAULT_STORAGE_MB`).
"""
from __future__ import annotations

from typing import Optional

from fastapi import HTTPException
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.db.models import AppUser, MaintenanceItem, MaintenanceLog, Vehicle, VehicleDocument
from app.i18n import _

MB = 1024 * 1024


def effective_limit_mb(user: Optional[AppUser]) -> int:
    """The limit that applies to this manager, in MB (-1 = unlimited)."""
    if user is None:
        return 0
    return user.storage_limit_mb if user.storage_limit_mb is not None else settings.DEFAULT_STORAGE_MB


async def storage_usage(session: AsyncSession, owner_id: int) -> int:
    """Bytes (plaintext sizes) of the files of every car this manager owns."""
    owned = select(Vehicle.vin).where(Vehicle.owner_id == owner_id)
    documents = (await session.execute(select(func.coalesce(func.sum(VehicleDocument.size), 0))
                                       .where(VehicleDocument.vin.in_(owned)))).scalar_one()
    invoices = (await session.execute(select(func.coalesce(func.sum(MaintenanceLog.invoice_filesize), 0))
                                      .join(MaintenanceItem, MaintenanceLog.item_id == MaintenanceItem.id)
                                      .where(MaintenanceItem.vin.in_(owned), MaintenanceLog.invoice_filename.is_not(None)))).scalar_one()
    photos = (await session.execute(select(func.coalesce(func.sum(Vehicle.photo_size), 0))
                                    .where(Vehicle.owner_id == owner_id, Vehicle.photo_filename.is_not(None)))).scalar_one()
    return int(documents or 0) + int(invoices or 0) + int(photos or 0)


async def owner_of_vehicle(session: AsyncSession, vin: str) -> Optional[int]:
    return (await session.execute(select(Vehicle.owner_id).where(Vehicle.vin == vin))).scalar_one_or_none()


def _mb(value: float) -> str:
    """12,3 (Spanish decimal comma, one decimal)."""
    return f"{value / MB:.1f}".replace(".", ",")


async def check_quota(session: AsyncSession, owner_id: Optional[int], new_bytes: int, replacing_bytes: int = 0) -> None:
    """409 when `new_bytes` (replacing a file of `replacing_bytes`) does not fit in the owner's space."""
    owner = await session.get(AppUser, owner_id) if owner_id is not None else None
    limit_mb = effective_limit_mb(owner)
    if limit_mb < 0:
        return
    used = await storage_usage(session, owner_id) if owner_id is not None else 0
    if used - replacing_bytes + new_bytes > limit_mb * MB:
        raise HTTPException(status_code=409, detail=_("No queda espacio en tu cuenta: usas {p0} de {p1} MB. Borra documentos o pide más espacio al administrador.",
                                                      p0=_mb(used), p1=limit_mb))
