"""Charges written down by the user: one by one (services/manual_charges.py) or from a spreadsheet (services/charge_import.py).
Any two of the charger's kWh, the price per kWh and the total paid give the third; the battery % alone gives the battery side.

Every route takes the car from its path (`vin` or `charge_id`), so `security/access.py` asks for the «edit» level to change
anything, like the refuels: the manager and the member accounts allowed to edit that car."""
from __future__ import annotations

import datetime
import json
from typing import Any, Dict, Optional

from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, Response, UploadFile
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import ChargingSession, Geofence, Vehicle
from app.db.session import get_db
from app.services import charge_import, manual_charges
from app.services.manual_charges import ChargeData, ChargeError

router = APIRouter(tags=["Charging"])


class ManualCharge(BaseModel):
    kwh: Optional[float] = Field(default=None, gt=0, le=manual_charges.KWH_MAX)     # the charger's
    day: Optional[datetime.date] = None                 # local day; left out = not known
    hour: Optional[datetime.time] = None                # local hour; left out = midday
    battery_gain_pct: Optional[float] = Field(default=None, gt=0, le=100)
    price_per_kwh: Optional[float] = Field(default=None, ge=0, le=manual_charges.PRICE_MAX)
    total_cost: Optional[float] = Field(default=None, ge=0, le=manual_charges.COST_MAX)
    free: bool = False
    geofence_id: Optional[int] = None
    place: Optional[str] = Field(default=None, max_length=manual_charges.PLACE_MAX)
    charge_type: str = Field(default="ac", pattern="^(ac|dc)$")

    def data(self) -> ChargeData:
        return ChargeData(kwh=self.kwh, day=self.day, hour=self.hour, battery_gain_pct=self.battery_gain_pct,
                          price_per_kwh=None if self.free else self.price_per_kwh, total_cost=None if self.free else self.total_cost,
                          free=self.free, geofence_id=self.geofence_id, place=self.place, charge_type=self.charge_type)


async def _vehicle(session: AsyncSession, vin: str) -> Vehicle:
    vehicle = (await session.execute(select(Vehicle).where(Vehicle.vin == vin))).scalar_one_or_none()
    if vehicle is None:
        raise HTTPException(status_code=404, detail="Vehículo no encontrado.")
    return vehicle


async def _manual(session: AsyncSession, charge_id: int) -> ChargingSession:
    charge = await session.get(ChargingSession, charge_id)
    if charge is None:
        raise HTTPException(status_code=404, detail="Sesión de recarga no encontrada.")
    if not charge.manual:
        # what the car itself reported is not rewritten by hand: its cost and its type have their own routes
        raise HTTPException(status_code=409, detail="Esa carga la registró el coche: solo se pueden cambiar las añadidas a mano.")
    return charge


@router.get("/vehicles/{vin}/charges/places")
async def charge_places(vin: str, session: AsyncSession = Depends(get_db)):
    """What the «Dónde» list of the form offers: the geofences of the car's manager and the places typed before."""
    return await manual_charges.places(session, await _vehicle(session, vin))


@router.post("/vehicles/{vin}/charges")
async def add_charge(vin: str, req: ManualCharge, session: AsyncSession = Depends(get_db)):
    """Writes down a charge the car did not report: one from before EVbro, or one it missed. Only the kWh are required."""
    vehicle = await _vehicle(session, vin)
    try:
        charge = await manual_charges.create(session, vehicle, req.data())
    except ChargeError as err:
        raise HTTPException(status_code=422, detail=str(err)) from err
    await session.commit()
    await session.refresh(charge)
    return manual_charges.out(charge)


@router.get("/charges/{charge_id}/manual")
async def get_manual_charge(charge_id: int, session: AsyncSession = Depends(get_db)):
    """A manual charge as its form needs it (local day and hour, what was typed)."""
    return manual_charges.out(await _manual(session, charge_id))


@router.put("/charges/{charge_id}")
async def edit_charge(charge_id: int, req: ManualCharge, session: AsyncSession = Depends(get_db)):
    """Corrects a charge added by hand."""
    charge = await _manual(session, charge_id)
    try:
        await manual_charges.fill(session, charge, await _vehicle(session, charge.vin), req.data())
    except ChargeError as err:
        raise HTTPException(status_code=422, detail=str(err)) from err
    await session.commit()
    await session.refresh(charge)
    return manual_charges.out(charge)


@router.delete("/charges/{charge_id}")
async def delete_charge(charge_id: int, session: AsyncSession = Depends(get_db)):
    """Deletes a charge added by hand (a detected one cannot be deleted)."""
    await session.delete(await _manual(session, charge_id))
    await session.commit()
    return {"status": "ok"}


# ───────────────────────── from a spreadsheet ─────────────────────────
@router.get("/vehicles/{vin}/charges/import/template")
async def import_template(vin: str, session: AsyncSession = Depends(get_db)):
    """An example Excel book to fill in."""
    await _vehicle(session, vin)
    return Response(content=charge_import.template(), media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                    headers={"Content-Disposition": 'attachment; filename="evbro-cargas.xlsx"', "Cache-Control": "no-store"})


async def _reviewed(session: AsyncSession, vehicle: Vehicle, file: UploadFile, mapping: Optional[str]):
    data = await file.read(charge_import.MAX_BYTES + 1)
    try:
        titles, rows = charge_import.read_table(file.filename or "", data)
    except charge_import.ImportError_ as err:
        raise HTTPException(status_code=422, detail=str(err)) from err
    chosen: Any = None
    if mapping:
        try:
            chosen = json.loads(mapping)
        except ValueError as err:
            raise HTTPException(status_code=422, detail="La asignación de columnas no es válida.") from err
    columns = charge_import.clean_mapping(chosen, len(titles)) if chosen is not None else charge_import.guess_mapping(titles)
    return titles, columns, await charge_import.review(session, vehicle, titles, rows, columns)


async def _geofence_names(session: AsyncSession, vehicle: Vehicle) -> Dict[int, str]:
    if vehicle.owner_id is None:
        return {}
    return {g.id: g.name for g in (await session.execute(select(Geofence).where(Geofence.owner_id == vehicle.owner_id))).scalars()}


@router.post("/vehicles/{vin}/charges/import/preview")
async def import_preview(vin: str, file: UploadFile = File(...), mapping: Optional[str] = Form(default=None), session: AsyncSession = Depends(get_db)):
    """Reads the file and says how it was understood: what each column is and what would happen to each row. Stores nothing.
    `mapping` (JSON {field: column index or null}) replaces the guess when the user corrects it."""
    vehicle = await _vehicle(session, vin)
    titles, columns, reviewed = await _reviewed(session, vehicle, file, mapping)
    return {"filename": (file.filename or "")[:120], **charge_import.summary(titles, columns, reviewed, await _geofence_names(session, vehicle))}


@router.post("/vehicles/{vin}/charges/import")
async def import_charges(vin: str, file: UploadFile = File(...), mapping: Optional[str] = Form(default=None), session: AsyncSession = Depends(get_db)):
    """Stores the rows of the file that are ready, as manual charges of one batch (so the import can be undone whole)."""
    vehicle = await _vehicle(session, vin)
    _titles, columns, reviewed = await _reviewed(session, vehicle, file, mapping)
    if not charge_import.energy_columns(columns):
        raise HTTPException(status_code=422, detail="Indica qué columna tiene los kWh, o las del total y el precio por kWh, o la de la batería cargada.")
    try:
        result = await charge_import.store(session, vehicle, reviewed)
    except charge_import.ImportError_ as err:
        raise HTTPException(status_code=422, detail=str(err)) from err
    await session.commit()
    return result


@router.delete("/vehicles/{vin}/charges/import")
async def undo_import(vin: str, batch: str = Query(max_length=32), session: AsyncSession = Depends(get_db)):
    """Deletes every charge an import brought in."""
    await _vehicle(session, vin)
    removed = await charge_import.undo(session, vin, batch)
    if not removed:
        raise HTTPException(status_code=404, detail="Esa importación no existe o ya se deshizo.")
    await session.commit()
    return {"status": "ok", "removed": removed}
