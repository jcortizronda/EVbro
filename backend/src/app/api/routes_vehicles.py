"""Vehicle and live telemetry routes."""
from __future__ import annotations

import datetime
import re
import uuid
from typing import List, Optional

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from pydantic import BaseModel, Field, field_validator
from sqlalchemy import desc, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import AppUser, EbroAccount, TelemetrySnapshot, Vehicle
from app.db.session import get_db
from app.security import auth
from app.security.access import accessible_vehicles, within_vehicle_limit
from app.services import energy_recalc, storage, uploads
from app.services.poller import VehiclePoller

router = APIRouter(prefix="/vehicles", tags=["Vehicles"])


class VehicleOut(BaseModel):
    id: int
    vin: str
    name: str
    model: str
    brand: str
    license_plate: Optional[str] = None
    chassis_number: Optional[str] = None          # manual cars: the real chassis number, informative (their `vin` is internal)
    state: str
    battery_level: Optional[float] = None
    electric_range_km: Optional[float] = None
    total_range_km: Optional[float] = None
    odometer: Optional[float] = None
    latitude: Optional[float] = None
    longitude: Optional[float] = None
    # vehicle profile
    model_year: Optional[int] = None
    registration_date: Optional[datetime.datetime] = None
    delivery_date: Optional[datetime.datetime] = None
    purchase_price: Optional[float] = None
    purchase_odometer: Optional[float] = None
    insurance_company: Optional[str] = None
    insurance_policy: Optional[str] = None
    insurance_coverage: Optional[str] = None
    insurance_renewal_date: Optional[datetime.datetime] = None
    insurance_assistance_phone: Optional[str] = None
    trim: Optional[str] = None
    color: Optional[str] = None
    env_label: Optional[str] = None
    tyre_front_size: Optional[str] = None
    tyre_rear_size: Optional[str] = None
    tyre_front_pressure_bar: Optional[float] = None
    tyre_rear_pressure_bar: Optional[float] = None
    tyre_pressure_tolerance_pct: Optional[float] = None
    fuel_low_threshold: Optional[int] = None
    low_battery_threshold: Optional[int] = None
    battery_capacity_kwh: Optional[float] = None
    fuel_tank_liters: Optional[float] = None
    fuel_type: Optional[str] = None               # gasoline | diesel | phev | ev
    photo_url: Optional[str] = None
    is_manual: bool = False
    access_level: Optional[str] = None            # what the signed-in account may do with it: view/edit/control/owner

    class Config:
        from_attributes = True


class VehicleUpdate(BaseModel):
    """Fields sent as `null` (or empty text) clear the value; fields not sent are left alone."""
    name: Optional[str] = None
    model: Optional[str] = None
    brand: Optional[str] = Field(default=None, max_length=50)
    license_plate: Optional[str] = None
    chassis_number: Optional[str] = Field(default=None, max_length=32)
    model_year: Optional[int] = Field(default=None, ge=1990, le=2100)
    registration_date: Optional[datetime.datetime] = None
    delivery_date: Optional[datetime.datetime] = None
    purchase_price: Optional[float] = Field(default=None, ge=0, le=10_000_000)
    purchase_odometer: Optional[float] = Field(default=None, ge=0, le=2_000_000)
    insurance_company: Optional[str] = Field(default=None, max_length=100)
    insurance_policy: Optional[str] = Field(default=None, max_length=60)
    insurance_coverage: Optional[str] = Field(default=None, max_length=40)
    insurance_renewal_date: Optional[datetime.datetime] = None
    insurance_assistance_phone: Optional[str] = Field(default=None, max_length=30)
    trim: Optional[str] = Field(default=None, max_length=60)
    color: Optional[str] = Field(default=None, max_length=40)
    env_label: Optional[str] = Field(default=None, max_length=10)
    tyre_front_size: Optional[str] = Field(default=None, max_length=30)
    tyre_rear_size: Optional[str] = Field(default=None, max_length=30)
    tyre_front_pressure_bar: Optional[float] = Field(default=None, ge=0.5, le=6)
    tyre_rear_pressure_bar: Optional[float] = Field(default=None, ge=0.5, le=6)
    tyre_pressure_tolerance_pct: Optional[float] = Field(default=None, ge=1, le=50)
    fuel_low_threshold: Optional[int] = Field(default=None, ge=1, le=100)
    low_battery_threshold: Optional[int] = Field(default=None, ge=1, le=100)
    battery_capacity_kwh: Optional[float] = Field(default=None, gt=1, le=500)
    fuel_tank_liters: Optional[float] = Field(default=None, ge=5, le=200)
    fuel_type: Optional[str] = Field(default=None, pattern="^(gasoline|diesel|phev|ev)$")

    @field_validator("chassis_number")
    @classmethod
    def _chassis(cls, value: Optional[str]) -> Optional[str]:
        """11 to 17 letters and digits, stored in upper case; empty clears it."""
        if value is None:
            return None
        clean = value.strip().replace(" ", "").upper()
        if not clean:
            return None
        if not re.fullmatch(r"[A-Z0-9]{11,17}", clean):
            raise ValueError("El número de bastidor debe tener entre 11 y 17 letras o números.")
        return clean


# These can be cleared by sending null. The rest (name, model, brand, capacities, tolerance) always has a value.
_CLEARABLE_TEXT = ("license_plate", "chassis_number", "insurance_company", "insurance_policy", "insurance_coverage", "insurance_assistance_phone", "trim", "color", "env_label", "tyre_front_size", "tyre_rear_size")
_CLEARABLE = ("registration_date", "delivery_date", "insurance_renewal_date", "purchase_price", "model_year",
              "tyre_front_pressure_bar", "tyre_rear_pressure_bar")

PHOTO_MAX_BYTES = 6 * 1024 * 1024


async def _get_vehicle(session: AsyncSession, vin: str) -> Vehicle:
    vehicle = (await session.execute(select(Vehicle).where(Vehicle.vin == vin))).scalar_one_or_none()
    if not vehicle:
        raise HTTPException(status_code=404, detail="Vehículo no encontrado")
    return vehicle


@router.get("", response_model=List[VehicleOut])
async def list_vehicles(session: AsyncSession = Depends(get_db), user: AppUser = Depends(auth.require_auth)):
    """The cars this account can see (its own and the ones shared with it), with its level on each."""
    levels = await accessible_vehicles(session, user)
    if not levels:
        return []
    rows = (await session.execute(select(Vehicle).where(Vehicle.vin.in_(levels)).order_by(Vehicle.id))).scalars().all()
    for v in rows:
        v.access_level = levels[v.vin]
    return rows


@router.delete("/{vin}")
async def delete_vehicle(vin: str, session: AsyncSession = Depends(get_db)):
    """Removes a car and everything recorded about it (owner only). A connected car comes back if the Ebro account is discovered again."""
    from app.services import tenant
    await tenant.delete_vehicles(session, [vin])
    await session.commit()
    VehiclePoller.get_instance()._contexts.pop(vin, None)
    return {"ok": True}


class ManualVehicle(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    model: str = Field(default="Ebro Auto", max_length=100)
    brand: str = Field(default="EBRO", max_length=50)
    fuel_type: Optional[str] = Field(default=None, pattern="^(gasoline|diesel|phev|ev)$")    # None = phev for an Ebro, gasoline otherwise


@router.post("", response_model=VehicleOut)
async def create_manual_vehicle(req: ManualVehicle, session: AsyncSession = Depends(get_db), user: AppUser = Depends(auth.require_auth)):
    """A car without an Ebro account: sheet, expenses, maintenance, costs and parts work; there is no polling and no commands.

    The VIN is generated on purpose (alphanumeric, like real ones): a user-chosen VIN could squat the VIN of somebody else's car."""
    if not await within_vehicle_limit(session, user.id):
        raise HTTPException(status_code=409, detail="Has alcanzado el límite de coches de tu cuenta.")
    vehicle = Vehicle(owner_id=user.id, vin=f"MAN{uuid.uuid4().hex[:14].upper()}", name=req.name.strip(), model=req.model.strip() or "Ebro Auto",
                      brand=req.brand.strip() or "EBRO", is_manual=True, state="parked",
                      fuel_type=req.fuel_type or ("phev" if (req.brand.strip() or "EBRO").lower() == "ebro" else "gasoline"))
    session.add(vehicle)
    await session.commit()
    await session.refresh(vehicle)
    vehicle.access_level = "owner"
    return vehicle


@router.put("/{vin}", response_model=VehicleOut)
async def update_vehicle(vin: str, payload: VehicleUpdate, session: AsyncSession = Depends(get_db)):
    """Updates the vehicle profile (identity, purchase, paperwork, tyres, energy and fuel). A new battery capacity
    re-estimates the energy of every past drive and charge (services/energy_recalc.py)."""
    vehicle = await _get_vehicle(session, vin)
    previous_capacity = vehicle.battery_capacity_kwh
    sent = payload.model_fields_set
    if "chassis_number" in sent and payload.chassis_number and not vehicle.is_manual:
        raise HTTPException(status_code=409, detail="El número de bastidor de un coche conectado se lee de la cuenta Ebro.")

    for field in ("name", "model", "brand"):
        value = getattr(payload, field)
        if value is not None and value.strip():
            setattr(vehicle, field, value.strip())
    for field in _CLEARABLE_TEXT:
        if field in sent:
            value = getattr(payload, field)
            setattr(vehicle, field, value.strip() or None if isinstance(value, str) else None)
    for field in _CLEARABLE:
        if field in sent:
            value = getattr(payload, field)
            setattr(vehicle, field, value.replace(tzinfo=None) if isinstance(value, datetime.datetime) else value)
    for field in ("purchase_odometer", "tyre_pressure_tolerance_pct", "battery_capacity_kwh", "fuel_tank_liters", "fuel_low_threshold", "low_battery_threshold", "fuel_type"):
        value = getattr(payload, field)
        if value is not None:
            setattr(vehicle, field, value)

    if vehicle.battery_capacity_kwh != previous_capacity:
        await energy_recalc.recalculate_for_capacity(session, vehicle)
    await session.commit()
    await session.refresh(vehicle)
    vehicle.access_level = "owner"           # only the owner reaches this route
    return vehicle


@router.post("/{vin}/photo", response_model=VehicleOut)
async def upload_vehicle_photo(vin: str, file: UploadFile = File(...), session: AsyncSession = Depends(get_db)):
    """Uploads the car photo (JPG, PNG or WebP, up to 6 MB), replacing the previous one. Type from the content, stored encrypted
    (`services/uploads.py`); it counts towards the owner's storage."""
    vehicle = await _get_vehicle(session, vin)
    data = await uploads.read_limited(file, PHOTO_MAX_BYTES, "La foto pesa más de 6 MB.")
    sniffed = uploads.sniff_type(data, allowed=uploads.IMAGE_TYPES)
    if sniffed is None:
        raise HTTPException(status_code=415, detail="El fichero no es una imagen válida: la foto debe ser JPG, PNG o WebP.")
    replacing = (vehicle.photo_size or 0) if vehicle.photo_filename else 0
    await storage.check_quota(session, vehicle.owner_id, len(data), replacing_bytes=replacing)

    name = uploads.store(uploads.PHOTOS_FOLDER, data, sniffed[1], prefix=f"{vin}-")
    old = vehicle.photo_filename
    vehicle.photo_filename = name
    vehicle.photo_size = len(data)
    try:
        await session.commit()
    except Exception:
        uploads.remove(uploads.PHOTOS_FOLDER, name)
        raise
    uploads.remove(uploads.PHOTOS_FOLDER, old)
    await session.refresh(vehicle)
    return vehicle


@router.get("/{vin}/photo")
async def get_vehicle_photo(vin: str, session: AsyncSession = Depends(get_db)):
    vehicle = await _get_vehicle(session, vin)
    if not vehicle.photo_filename:
        raise HTTPException(status_code=404, detail="Este vehículo no tiene foto.")
    data = uploads.load(uploads.PHOTOS_FOLDER, vehicle.photo_filename, missing="Foto no encontrada.")
    sniffed = uploads.sniff_type(data, allowed=uploads.IMAGE_TYPES)
    if sniffed is None:
        raise HTTPException(status_code=404, detail="Foto no encontrada.")
    return uploads.file_response(data, sniffed[0], f"foto{sniffed[1]}")


@router.delete("/{vin}/photo", response_model=VehicleOut)
async def delete_vehicle_photo(vin: str, session: AsyncSession = Depends(get_db)):
    vehicle = await _get_vehicle(session, vin)
    if vehicle.photo_filename:
        old = vehicle.photo_filename
        vehicle.photo_filename = None
        vehicle.photo_size = None
        await session.commit()
        uploads.remove(uploads.PHOTOS_FOLDER, old)
        await session.refresh(vehicle)
    return vehicle


@router.post("/discover")
async def discover_vehicles(session: AsyncSession = Depends(get_db), user: AppUser = Depends(auth.require_auth)):
    """Connects to the Ebro cloud with THIS user's account and discovers its new vehicles."""
    account = (await session.execute(select(EbroAccount).where(EbroAccount.is_active == True, EbroAccount.user_id == user.id)  # noqa: E712
                                     .order_by(EbroAccount.id.desc()))).scalars().first()
    if account is None:
        raise HTTPException(status_code=409, detail="Conecta primero tu cuenta de Ebro (Ajustes, Cuenta).")
    poller = VehiclePoller.get_instance()
    vehicles = await poller.discover_vehicles(account=account)
    return {"status": "ok", "count": len(vehicles), "vehicles": [v.vin for v in vehicles]}


@router.get("/{vin}/latest")
async def get_latest_telemetry(vin: str, session: AsyncSession = Depends(get_db)):
    """Gets the vehicle's most recent telemetry snapshot."""
    poller = VehiclePoller.get_instance()
    mem_latest = poller.get_latest(vin)
    if mem_latest:
        return mem_latest

    # if it is not in memory, read the last snapshot from the DB and normalise it
    stmt = (
        select(TelemetrySnapshot)
        .where(TelemetrySnapshot.vin == vin)
        .order_by(desc(TelemetrySnapshot.timestamp))
        .limit(1)
    )
    result = await session.execute(stmt)
    snap = result.scalar_one_or_none()

    if not snap:
        raise HTTPException(status_code=404, detail="No se encontraron datos de telemetría para este vehículo.")

    if snap.raw_data:
        from app.core.telemetry import parse_telemetry_data
        return parse_telemetry_data(snap.raw_data)

    return {
        "online_status": snap.online_status,
        "state": snap.state,
        "battery_level": snap.battery_level,
        "electric_range_km": snap.electric_range_km,
        "total_range_km": snap.total_range_km,
        "fuel_level": snap.fuel_level,
        "odometer_km": snap.odometer_km,
        "speed_kmh": snap.speed_kmh,
        "latitude": snap.latitude,
        "longitude": snap.longitude,
        "altitude": snap.altitude,
        "heading": snap.heading,
        "outside_temp": snap.outside_temp,
        "inside_temp": snap.inside_temp,
        "climate_on": snap.climate_on,
        "target_temp": snap.target_temp,
        "battery_voltage": snap.battery_voltage,
        "battery_current": snap.battery_current,
        "charge_power_kw": snap.charge_power_kw,
        "is_charging": snap.is_charging,
        "cable_connected": snap.cable_connected,
        "fast_charging": snap.fast_charging,
        "remaining_charge_time_min": snap.remaining_charge_time_min,
        "is_locked": snap.is_locked,
        "doors": snap.doors,
        "windows": snap.windows,
        "tyre_pressures": snap.tyre_pressures,
        "tyre_temps": snap.tyre_temps,
        "raw_data": snap.raw_data,
    }


@router.post("/{vin}/poll")
async def poll_vehicle_now(vin: str):
    """Forces an immediate telemetry poll of the Ebro cloud."""
    poller = VehiclePoller.get_instance()
    data = await poller.poll_vehicle(vin, force=True)
    if not data:
        raise HTTPException(status_code=502, detail="No se pudo obtener respuesta del vehículo o la nube Ebro.")
    return {"status": "ok", "telemetry": data}
