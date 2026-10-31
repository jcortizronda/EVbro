"""Plan de mantenimiento de un coche: oficial de EBRO o personalizado.

Un único plan por coche con un interruptor (`mode`). El historial de intervenciones y las facturas son comunes a los dos
modos (ver `routes_maintenance.py`). La lógica de la tabla oficial está en `services/maintenance_official.py`.
"""
from __future__ import annotations

import datetime
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.routes_maintenance import calculate_item_health
from app.api.routes_parts import record_parts
from app.db.models import MaintenanceItem, MaintenanceLog, MaintenancePlan, Vehicle
from app.db.session import get_db
from app.i18n import _
from app.services import maintenance_official as official
from app.services.parts import PartIn

router = APIRouter(tags=["Maintenance plan"])

OFFICIAL_ITEM_NAME = "Revisión oficial EBRO"


class PlanUpdate(BaseModel):
    """Modo del plan y odómetro de entrega. Las FECHAS de entrega y matriculación se configuran en el vehículo
    (`PUT /vehicles/{vin}`); `baseline_date` se acepta por compatibilidad y escribe la fecha de entrega del vehículo."""
    mode: Optional[str] = Field(default=None, pattern="^(official|custom)$")
    baseline_date: Optional[datetime.datetime] = None
    baseline_odometer: Optional[float] = Field(default=None, ge=0)
    clear_baseline_date: bool = False


class ItvCompleteRequest(BaseModel):
    service_date: Optional[datetime.datetime] = None
    valid_until: Optional[datetime.datetime] = None        # «válida hasta» de la pegatina; si no, se calcula
    odometer: Optional[float] = None
    cost: float = 0.0
    workshop: Optional[str] = None
    notes: Optional[str] = None


class EventRequest(BaseModel):
    """Intervención puntual, sin intervalo (pastillas de freno, reparar una rueda…)."""
    title: str = Field(min_length=2, max_length=150)
    category: str = "General"
    service_date: Optional[datetime.datetime] = None
    odometer: Optional[float] = None
    cost: float = 0.0
    workshop: Optional[str] = None
    notes: Optional[str] = None
    parts: Optional[List[PartIn]] = None
    update_parts: bool = True                                # also update the car sheet with these parts


class OfficialCompleteRequest(BaseModel):
    step_km: int                                             # escalón que se registra (15000, 30000…)
    odometer: Optional[float] = None
    cost: float = 0.0
    workshop: Optional[str] = "Taller Oficial Ebro"
    notes: Optional[str] = ""
    service_date: Optional[datetime.datetime] = None
    parts: Optional[List[PartIn]] = None
    update_parts: bool = True


async def _vehicle(session: AsyncSession, vin: str) -> Vehicle:
    vehicle = (await session.execute(select(Vehicle).where(Vehicle.vin == vin))).scalar_one_or_none()
    if vehicle is None:
        raise HTTPException(status_code=404, detail="Vehículo no encontrado.")
    return vehicle


async def get_or_create_plan(session: AsyncSession, vin: str) -> MaintenancePlan:
    plan = await session.get(MaintenancePlan, vin)
    if plan is None:
        plan = MaintenancePlan(vin=vin, mode="official", baseline_odometer=0.0)
        session.add(plan)
        await session.flush()
    return plan


async def _holder(session: AsyncSession, vin: str, origin: str, name: str, category: str) -> MaintenanceItem:
    """«Contenedor» de los registros de una procedencia (revisión oficial, ITV, intervenciones puntuales): no es una tarea propia."""
    item = (await session.execute(
        select(MaintenanceItem).where(MaintenanceItem.vin == vin, MaintenanceItem.origin == origin)
    )).scalars().first()
    if item is None:
        item = MaintenanceItem(vin=vin, name=name, category=category, origin=origin)
        session.add(item)
        await session.flush()
    return item


async def _official_item(session: AsyncSession, vin: str) -> MaintenanceItem:
    item = await _holder(session, vin, "official", OFFICIAL_ITEM_NAME, "Oficial")
    if item.interval_km is None:
        item.interval_km, item.interval_months = float(official.STEPS[0]["km"]), 12
    return item


async def _official_logs(session: AsyncSession, vin: str) -> List[MaintenanceLog]:
    stmt = (
        select(MaintenanceLog)
        .join(MaintenanceItem, MaintenanceLog.item_id == MaintenanceItem.id)
        .where(MaintenanceItem.vin == vin, MaintenanceLog.revision_km.is_not(None))
        .order_by(MaintenanceLog.revision_km, MaintenanceLog.service_date)
    )
    return list((await session.execute(stmt)).scalars().all())


def _matrix(done_by_km: Dict[int, MaintenanceLog], next_km: Optional[int], model: Optional[str]) -> Dict[str, Any]:
    labor = official.LABOR_HOURS[official.labor_key(model)]
    columns = [{
        "km": st["km"], "months": st["months"], "km_label": f"{st['km'] // 1000}k", "label": st["label"],
        "labor_hours": labor.get(st["km"], 1.0),
        "is_done": st["km"] in done_by_km, "is_next": st["km"] == next_km,
    } for st in official.STEPS]
    rows = [{
        "id": r["id"], "name": r["name"], "category": r["category"], "spec": r["spec"],
        "operations": {st["km"]: r["operations"].get(st["km"], "-") for st in official.STEPS},
    } for r in official.RULES]
    return {"columns": columns, "rows": rows}


async def build_plan(session: AsyncSession, vin: str, today: Optional[datetime.date] = None) -> Dict[str, Any]:
    vehicle = await _vehicle(session, vin)
    plan = await get_or_create_plan(session, vin)
    odo = vehicle.odometer or 0.0
    now = datetime.datetime.utcnow()
    today = today or now.date()

    logs = await _official_logs(session, vin)
    done_by_km = {log.revision_km: log for log in logs}
    last_done = max(done_by_km) if done_by_km else None
    delivery = vehicle.delivery_date or plan.baseline_date          # (plan.baseline_date: dato anterior a mover la fecha al vehículo)
    nxt = official.official_next(odo, today, last_done, delivery, plan.baseline_odometer or 0.0, vehicle.model)

    items = (await session.execute(
        select(MaintenanceItem).where(MaintenanceItem.vin == vin, MaintenanceItem.origin == "custom").order_by(MaintenanceItem.id)
    )).scalars().all()
    existing = {i.name.strip().lower() for i in items}
    official_available = official.is_ebro(vehicle.brand)

    return {
        "mode": plan.mode if official_available else "custom",      # another brand: always the custom plan
        "official_available": official_available,
        "vehicle": {"vin": vin, "name": vehicle.name, "model": vehicle.model, "odometer": odo},
        "baseline": {
            "date": delivery.isoformat() if delivery else None,
            "odometer": plan.baseline_odometer or 0.0,
            "configured": delivery is not None,
            "registration_date": vehicle.registration_date.isoformat() if vehicle.registration_date else None,
        },
        "itv": await _itv(session, vehicle, today),
        "official": {
            "next": nxt,
            "finished": nxt is None,
            "history": [{
                "log_id": log.id, "step_km": log.revision_km, "date": log.service_date.isoformat(),
                "odometer": log.odometer, "cost": log.cost, "workshop": log.workshop,
            } for log in logs],
            "matrix": _matrix(done_by_km, nxt["km"] if nxt else None, vehicle.model),
        },
        "custom": {
            "items": [calculate_item_health(i, odo, now) for i in items],
            "suggestions": [s for s in official.SUGGESTIONS if s["name"].strip().lower() not in existing],
        },
    }


async def _itv(session: AsyncSession, vehicle: Vehicle, today: datetime.date) -> Dict[str, Any]:
    """Estado de la ITV: la fecha de matriculación manda (cuenta desde ella); la «válida hasta» de la última pegatina, si se indicó."""
    item = (await session.execute(
        select(MaintenanceItem).where(MaintenanceItem.vin == vehicle.vin, MaintenanceItem.origin == "itv")
    )).scalars().first()
    last = None
    if item is not None:
        last = (await session.execute(
            select(func.max(MaintenanceLog.service_date)).where(MaintenanceLog.item_id == item.id)
        )).scalar_one_or_none()
    nxt = official.itv_next_due(vehicle.registration_date, last, item.next_due_date if item is not None else None)
    due = nxt["due_date"]
    return {
        "registration_date": vehicle.registration_date.isoformat() if vehicle.registration_date else None,
        "last_date": last.isoformat() if last else None,
        "next_due_date": due.isoformat() if due else None,
        "basis": nxt["basis"],
        "rule": "La primera a los 4 años de la matriculación; después cada 2 años hasta los 10 y cada año a partir de ahí.",
        **official.itv_status(due, today),
    }


@router.get("/vehicles/{vin}/maintenance/plan")
async def get_maintenance_plan(vin: str, session: AsyncSession = Depends(get_db)):
    """Plan del coche: modo, punto de partida, próxima revisión oficial, tareas propias y sugerencias."""
    data = await build_plan(session, vin)
    await session.commit()          # (el plan se crea la primera vez que se consulta)
    return data


@router.put("/vehicles/{vin}/maintenance/plan")
async def update_maintenance_plan(vin: str, req: PlanUpdate, session: AsyncSession = Depends(get_db)):
    """Cambia el modo (official/custom) y/o el punto de partida del plan oficial (fecha de entrega y odómetro)."""
    vehicle = await _vehicle(session, vin)
    if req.mode == "official" and not official.is_ebro(vehicle.brand):
        raise HTTPException(status_code=409, detail="El plan oficial de EBRO solo está disponible para coches de la marca Ebro.")
    plan = await get_or_create_plan(session, vin)
    if req.mode is not None:
        plan.mode = req.mode
    if req.clear_baseline_date:
        vehicle.delivery_date = None
        plan.baseline_date = None
    elif req.baseline_date is not None:
        vehicle.delivery_date = req.baseline_date.replace(tzinfo=None)
    if req.baseline_odometer is not None:
        plan.baseline_odometer = req.baseline_odometer
    await session.commit()
    return await build_plan(session, vin)


@router.post("/vehicles/{vin}/maintenance/official/complete")
async def complete_official_step(vin: str, req: OfficialCompleteRequest, session: AsyncSession = Depends(get_db)):
    """Registra como realizada una revisión oficial: el plan pasa al escalón siguiente."""
    vehicle = await _vehicle(session, vin)
    step = next((s for s in official.STEPS if s["km"] == req.step_km), None)
    if step is None:
        raise HTTPException(status_code=422, detail=_("El escalón {p0} km no existe en el plan oficial.", p0=req.step_km))
    already = (await session.execute(
        select(func.count()).select_from(MaintenanceLog)
        .join(MaintenanceItem, MaintenanceLog.item_id == MaintenanceItem.id)
        .where(MaintenanceItem.vin == vin, MaintenanceLog.revision_km == req.step_km)
    )).scalar_one()
    if already:
        raise HTTPException(status_code=409, detail=_("La revisión de {p0} km ya está registrada.", p0=format(req.step_km, ',')).replace(",", "."))

    holder = await _official_item(session, vin)
    detail = official.step_detail(req.step_km, vehicle.model)
    summary = [f"Reemplazo: {i['name']}" for i in detail["replace_items"]] + [f"Inspección: {i['name']}" for i in detail["inspect_items"]]
    notes = f"Revisión oficial {step['label']}. Operaciones: {', '.join(summary)}."
    if req.notes:
        notes += f" Observaciones: {req.notes}"

    log = MaintenanceLog(
        item_id=holder.id,
        service_date=(req.service_date or datetime.datetime.utcnow()).replace(tzinfo=None),
        odometer=req.odometer if req.odometer is not None else (vehicle.odometer or 0.0),
        cost=req.cost, workshop=req.workshop or "Taller Oficial Ebro", notes=notes, revision_km=req.step_km,
    )
    session.add(log)
    holder.last_service_date, holder.last_service_odometer = log.service_date, log.odometer
    await record_parts(session, vin, log, req.parts, req.update_parts)
    await session.commit()
    return {"status": "ok", "log_id": log.id, "message": f"Revisión de {step['label']} registrada."}


@router.post("/vehicles/{vin}/maintenance/itv/complete")
async def complete_itv(vin: str, req: ItvCompleteRequest, session: AsyncSession = Depends(get_db)):
    """Registra una ITV pasada. Con `valid_until` (la de la pegatina) esa es la próxima; si no, se calcula por antigüedad."""
    vehicle = await _vehicle(session, vin)
    when = (req.service_date or datetime.datetime.utcnow()).replace(tzinfo=None)
    holder = await _holder(session, vin, "itv", "Inspección técnica (ITV)", "Legal")
    nxt = official.itv_next_due(vehicle.registration_date, when, req.valid_until.replace(tzinfo=None) if req.valid_until else None)
    log = MaintenanceLog(
        item_id=holder.id, service_date=when, odometer=req.odometer if req.odometer is not None else (vehicle.odometer or 0.0),
        cost=req.cost, workshop=req.workshop or "Estación ITV", notes=req.notes or "", title="ITV", category="Legal",
    )
    session.add(log)
    holder.last_service_date, holder.next_due_date = when, nxt["due_date"]
    await session.commit()
    return {"status": "ok", "log_id": log.id, "next_due_date": nxt["due_date"].isoformat() if nxt["due_date"] else None}


@router.post("/vehicles/{vin}/maintenance/events")
async def add_event(vin: str, req: EventRequest, session: AsyncSession = Depends(get_db)):
    """Registra una intervención puntual (sin intervalo): pastillas de freno, reparación de una rueda…"""
    vehicle = await _vehicle(session, vin)
    holder = await _holder(session, vin, "oneoff", "Intervenciones puntuales", "Puntual")
    log = MaintenanceLog(
        item_id=holder.id, service_date=(req.service_date or datetime.datetime.utcnow()).replace(tzinfo=None),
        odometer=req.odometer if req.odometer is not None else (vehicle.odometer or 0.0),
        cost=req.cost, workshop=req.workshop, notes=req.notes, title=req.title.strip(), category=req.category,
    )
    session.add(log)
    await record_parts(session, vin, log, req.parts, req.update_parts)
    await session.commit()
    return {"status": "ok", "log_id": log.id}
