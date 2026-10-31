"""Rutas de gestión y seguimiento de mantenimiento vehicular."""
from __future__ import annotations

import datetime
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from pydantic import BaseModel
from sqlalchemy import desc, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.routes_parts import record_parts
from app.db.models import MaintenanceItem, MaintenanceLog, Vehicle
from app.db.session import get_db
from app.services import storage, uploads
from app.services.maintenance_official import add_months
from app.services.parts import PartIn

router = APIRouter(tags=["Maintenance"])

INVOICES_FOLDER = uploads.INVOICES_FOLDER
MAX_FILE_SIZE = 15 * 1024 * 1024  # 15 MB


class MaintenanceCreate(BaseModel):
    name: str
    category: str = "General"  # Motor, Neumáticos, Frenos, Filtros, ITV...
    interval_km: Optional[float] = 15000.0
    interval_months: Optional[int] = 12
    last_service_odometer: Optional[float] = None
    last_service_date: Optional[datetime.datetime] = None
    notes: Optional[str] = None


class MaintenanceUpdate(BaseModel):
    name: Optional[str] = None
    category: Optional[str] = None
    interval_km: Optional[float] = None
    interval_months: Optional[int] = None
    next_due_odometer: Optional[float] = None
    next_due_date: Optional[datetime.datetime] = None
    notes: Optional[str] = None


class CompleteServiceRequest(BaseModel):
    odometer: Optional[float] = None
    cost: float = 0.0
    workshop: Optional[str] = None
    notes: Optional[str] = None
    service_date: Optional[datetime.datetime] = None
    parts: Optional[List[PartIn]] = None
    update_parts: bool = True


def calculate_item_health(item: MaintenanceItem, current_odo: float, now: datetime.datetime) -> Dict[str, Any]:
    """Calcula el estado de salud, kilómetros/días restantes y porcentaje de desgaste."""
    rem_km = None
    if item.next_due_odometer is not None:
        rem_km = round(item.next_due_odometer - current_odo, 1)

    rem_days = None
    if item.next_due_date is not None:
        rem_days = (item.next_due_date.date() - now.date()).days

    # Estado: 'due' (vencido), 'warning' (próximo), 'ok' (buen estado)
    status = "ok"
    if (rem_km is not None and rem_km <= 0) or (rem_days is not None and rem_days <= 0):
        status = "due"
    elif (rem_km is not None and rem_km <= 1500) or (rem_days is not None and rem_days <= 30):
        status = "warning"

    # Progreso de 0% a 100% de uso del intervalo
    progress_pct = 0.0
    if item.interval_km and item.last_service_odometer is not None:
        km_done = max(0.0, current_odo - item.last_service_odometer)
        progress_pct = min(100.0, round((km_done / item.interval_km) * 100.0, 1))

    return {
        "id": item.id,
        "vin": item.vin,
        "name": item.name,
        "category": item.category,
        "last_service_date": item.last_service_date.isoformat() if item.last_service_date else None,
        "last_service_odometer": item.last_service_odometer,
        "interval_km": item.interval_km,
        "interval_months": item.interval_months,
        "next_due_odometer": item.next_due_odometer,
        "next_due_date": item.next_due_date.isoformat() if item.next_due_date else None,
        "remaining_km": rem_km,
        "remaining_days": rem_days,
        "progress_pct": progress_pct,
        "status": status,
        "notes": item.notes or "",
    }


@router.get("/vehicles/{vin}/maintenance")
async def list_vehicle_maintenance(vin: str, session: AsyncSession = Depends(get_db)):
    """Lista las tareas PROPIAS (plan personalizado) con su estado. Ya no se crean tareas solas: ver `/maintenance/plan`."""
    v_stmt = select(Vehicle).where(Vehicle.vin == vin)
    vehicle = (await session.execute(v_stmt)).scalar_one_or_none()
    current_odo = vehicle.odometer if vehicle and vehicle.odometer else 0.0
    now = datetime.datetime.utcnow()

    stmt = (
        select(MaintenanceItem)
        .where(MaintenanceItem.vin == vin, MaintenanceItem.origin == "custom")
        .order_by(MaintenanceItem.id)
    )
    items = (await session.execute(stmt)).scalars().all()

    return [calculate_item_health(item, current_odo, now) for item in items]


@router.post("/vehicles/{vin}/maintenance")
async def create_maintenance_item(
    vin: str, req: MaintenanceCreate, session: AsyncSession = Depends(get_db)
):
    """Crea una nueva tarea o componente de mantenimiento."""
    v_stmt = select(Vehicle).where(Vehicle.vin == vin)
    vehicle = (await session.execute(v_stmt)).scalar_one_or_none()
    current_odo = vehicle.odometer if vehicle and vehicle.odometer else 0.0
    now = datetime.datetime.utcnow()

    last_odo = req.last_service_odometer if req.last_service_odometer is not None else current_odo
    last_dt = req.last_service_date if req.last_service_date is not None else now

    next_odo = (last_odo + req.interval_km) if req.interval_km else None
    next_dt = add_months(last_dt, req.interval_months) if req.interval_months else None

    item = MaintenanceItem(
        vin=vin,
        name=req.name,
        category=req.category,
        interval_km=req.interval_km,
        interval_months=req.interval_months,
        last_service_odometer=last_odo,
        last_service_date=last_dt,
        next_due_odometer=next_odo,
        next_due_date=next_dt,
        notes=req.notes,
    )
    session.add(item)
    await session.commit()
    await session.refresh(item)
    return calculate_item_health(item, current_odo, now)


@router.put("/maintenance/{item_id}")
async def update_maintenance_item(
    item_id: int, req: MaintenanceUpdate, session: AsyncSession = Depends(get_db)
):
    """Actualiza la configuración de un elemento de mantenimiento."""
    item = await session.get(MaintenanceItem, item_id)
    if not item:
        raise HTTPException(status_code=404, detail="Elemento de mantenimiento no encontrado.")

    data = req.model_dump(exclude_unset=True) if hasattr(req, "model_dump") else req.dict(exclude_unset=True)
    for field, val in data.items():
        setattr(item, field, val)

    # Si cambia el intervalo, el próximo vencimiento se recalcula desde el último servicio (salvo que se envíe a mano)
    if ("interval_km" in data or "interval_months" in data) and not {"next_due_odometer", "next_due_date"} & data.keys():
        item.next_due_odometer = (
            item.last_service_odometer + item.interval_km
            if item.interval_km and item.last_service_odometer is not None else None
        )
        item.next_due_date = (
            add_months(item.last_service_date, item.interval_months)
            if item.interval_months and item.last_service_date else None
        )

    await session.commit()
    await session.refresh(item)

    v_stmt = select(Vehicle).where(Vehicle.vin == item.vin)
    vehicle = (await session.execute(v_stmt)).scalar_one_or_none()
    current_odo = vehicle.odometer if vehicle and vehicle.odometer else 0.0

    return calculate_item_health(item, current_odo, datetime.datetime.utcnow())


@router.post("/maintenance/{item_id}/complete")
async def complete_maintenance_service(
    item_id: int, req: CompleteServiceRequest, session: AsyncSession = Depends(get_db)
):
    """Registra una revisión o intervención realizada, actualizando el próximo vencimiento."""
    item = await session.get(MaintenanceItem, item_id)
    if not item:
        raise HTTPException(status_code=404, detail="Elemento de mantenimiento no encontrado.")

    v_stmt = select(Vehicle).where(Vehicle.vin == item.vin)
    vehicle = (await session.execute(v_stmt)).scalar_one_or_none()
    current_odo = vehicle.odometer if vehicle and vehicle.odometer else 0.0

    service_odo = req.odometer if req.odometer is not None else current_odo
    service_dt = req.service_date if req.service_date is not None else datetime.datetime.utcnow()

    # 1. Registrar en el historial de revisiones (Log)
    log = MaintenanceLog(
        item_id=item.id,
        service_date=service_dt,
        odometer=service_odo,
        cost=req.cost,
        workshop=req.workshop,
        notes=req.notes,
    )
    session.add(log)
    await record_parts(session, item.vin, log, req.parts, req.update_parts)

    # 2. Resetear ciclo de mantenimiento
    item.last_service_date = service_dt
    item.last_service_odometer = service_odo

    if item.interval_km:
        item.next_due_odometer = service_odo + item.interval_km
    if item.interval_months:
        item.next_due_date = add_months(service_dt, item.interval_months)

    await session.commit()
    await session.refresh(item)

    health = calculate_item_health(item, current_odo, datetime.datetime.utcnow())
    health["log_id"] = log.id
    return health


@router.delete("/maintenance/{item_id}")
async def delete_maintenance_item(item_id: int, session: AsyncSession = Depends(get_db)):
    """Elimina un elemento de mantenimiento programado."""
    item = await session.get(MaintenanceItem, item_id)
    if not item:
        raise HTTPException(status_code=404, detail="Elemento no encontrado.")
    logs = (await session.execute(select(MaintenanceLog).where(MaintenanceLog.item_id == item_id))).scalars().all()
    invoices = [log.invoice_filename for log in logs if log.invoice_filename]
    for log in logs:                     # explicitly, not only by the database cascade: their invoice files go too
        await session.delete(log)
    await session.delete(item)
    await session.commit()
    for name in invoices:
        uploads.remove(INVOICES_FOLDER, name)
    return {"status": "ok", "deleted_id": item_id}


@router.get("/vehicles/{vin}/maintenance/logs")
async def list_maintenance_logs(vin: str, session: AsyncSession = Depends(get_db)):
    """Historial completo de revisiones e intervenciones registradas con facturas adjuntas."""
    stmt = (
        select(MaintenanceLog, MaintenanceItem.name, MaintenanceItem.category, MaintenanceItem.origin)
        .join(MaintenanceItem, MaintenanceLog.item_id == MaintenanceItem.id)
        .where(MaintenanceItem.vin == vin)
        .order_by(desc(MaintenanceLog.service_date))
    )
    result = await session.execute(stmt)
    rows = result.all()

    logs = []
    for log, item_name, category, origin in rows:
        has_inv = bool(log.invoice_filename)
        logs.append({
            "id": log.id,
            "item_name": log.title or item_name,
            "title": log.title,
            "origin": origin,
            "revision_km": log.revision_km,
            "category": log.category or category,
            "service_date": log.service_date.isoformat(),
            "odometer": log.odometer,
            "cost": log.cost,
            "workshop": log.workshop or "Taller no especificado",
            "notes": log.notes or "",
            "parts_used": log.parts_used or [],
            "has_invoice": has_inv,
            "invoice_original_name": log.invoice_original_name,
            "invoice_content_type": log.invoice_content_type,
            "invoice_filesize": log.invoice_filesize,
            "invoice_url": f"/api/maintenance/logs/{log.id}/invoice" if has_inv else None,
        })
    return logs


# ============================================================================
# GESTIÓN Y ADJUNCIÓN DE FACTURAS Y DOCUMENTOS DE MANTENIMIENTO
# ============================================================================

async def _vin_of_log(session: AsyncSession, log: MaintenanceLog) -> Optional[str]:
    return (await session.execute(select(MaintenanceItem.vin).where(MaintenanceItem.id == log.item_id))).scalar_one_or_none()


@router.post("/maintenance/logs/{log_id}/invoice")
async def upload_maintenance_invoice(log_id: int, file: UploadFile = File(...), session: AsyncSession = Depends(get_db)):
    """Attaches an invoice or receipt (PDF or image, up to 15 MB) to an intervention, replacing the previous one.

    Same rules as the car documents (`services/uploads.py`): the type comes from the content, the size is checked while reading, the file
    is stored encrypted under a random name, and it counts towards the storage of the car's owner (whoever uploads it)."""
    log = await session.get(MaintenanceLog, log_id)
    if not log:
        raise HTTPException(status_code=404, detail="Registro de mantenimiento no encontrado.")

    data = await uploads.read_limited(file, MAX_FILE_SIZE, "La factura pesa más de 15 MB.")
    sniffed = uploads.sniff_type(data)
    if sniffed is None:
        raise HTTPException(status_code=415, detail="Solo se admiten facturas en PDF o imágenes JPG, PNG o WebP.")
    content_type, extension = sniffed
    owner_id = await storage.owner_of_vehicle(session, await _vin_of_log(session, log))
    replacing = (log.invoice_filesize or 0) if log.invoice_filename else 0
    await storage.check_quota(session, owner_id, len(data), replacing_bytes=replacing)

    stored = uploads.store(INVOICES_FOLDER, data, extension, prefix=f"factura_log_{log_id}_")
    old = log.invoice_filename
    original_name = uploads.clean_original_name(file.filename, extension, fallback="factura")
    log.invoice_filename = stored
    log.invoice_original_name = original_name
    log.invoice_content_type = content_type
    log.invoice_filesize = len(data)
    try:
        await session.commit()
    except Exception:
        uploads.remove(INVOICES_FOLDER, stored)
        raise
    if old and old != stored:
        uploads.remove(INVOICES_FOLDER, old)

    return {
        "status": "ok",
        "log_id": log.id,
        "filename": stored,
        "original_name": original_name,
        "content_type": content_type,
        "filesize": len(data),
        "invoice_url": f"/api/maintenance/logs/{log.id}/invoice",
    }


@router.get("/maintenance/logs/{log_id}/invoice")
async def get_maintenance_invoice(log_id: int, session: AsyncSession = Depends(get_db)):
    """The attached invoice, inline (the browser shows the PDF or the image)."""
    log = await session.get(MaintenanceLog, log_id)
    if not log or not log.invoice_filename:
        raise HTTPException(status_code=404, detail="No hay ninguna factura adjunta a este registro de mantenimiento.")
    data = uploads.load(INVOICES_FOLDER, log.invoice_filename, missing="El fichero de la factura no está en el servidor.")
    # The type is sniffed again from the bytes: rows written before the sniffing kept whatever the browser had sent.
    sniffed = uploads.sniff_type(data)
    content_type = sniffed[0] if sniffed else "application/octet-stream"
    return uploads.file_response(data, content_type, log.invoice_original_name or f"factura{sniffed[1] if sniffed else ''}")


@router.delete("/maintenance/logs/{log_id}/invoice")
async def delete_maintenance_invoice(log_id: int, session: AsyncSession = Depends(get_db)):
    """Removes the invoice attached to an intervention."""
    log = await session.get(MaintenanceLog, log_id)
    if not log:
        raise HTTPException(status_code=404, detail="Registro de mantenimiento no encontrado.")
    stored = log.invoice_filename
    log.invoice_filename = None
    log.invoice_original_name = None
    log.invoice_content_type = None
    log.invoice_filesize = None
    await session.commit()
    uploads.remove(INVOICES_FOLDER, stored)
    return {"status": "ok", "message": "Factura eliminada correctamente."}
