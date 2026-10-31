"""Traccar Server link of a car (Coches → ficha → «Posición por GPS»): configure, test, status, latest fix."""
from __future__ import annotations

import datetime
from typing import Any, Dict, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import GpsPosition, TraccarLink, Vehicle
from app.db.session import get_db
from app.services.traccar import TraccarClient, TraccarError, TraccarHub

router = APIRouter(tags=["Traccar"])


class TraccarTest(BaseModel):
    host: str = Field(min_length=1, max_length=255)
    port: Optional[int] = Field(default=None, ge=1, le=65535)
    use_ssl: bool = False
    verify_ssl: bool = True
    api_token: Optional[str] = Field(default=None, max_length=200)    # empty on an existing link: the stored one is used


class TraccarSave(TraccarTest):
    device_id: int = Field(ge=1)
    device_name: Optional[str] = Field(default=None, max_length=120)
    enabled: bool = True
    build_trips: bool = False
    stop_minutes: int = Field(default=5, ge=1, le=120)


def serialize(link: TraccarLink) -> Dict[str, Any]:
    return {"host": link.host, "port": link.port, "use_ssl": link.use_ssl, "verify_ssl": link.verify_ssl, "has_token": bool(link.api_token),
            "device_id": link.device_id, "device_name": link.device_name, "enabled": link.enabled, "build_trips": link.build_trips,
            "stop_minutes": link.stop_minutes, "last_position_at": link.last_position_at.isoformat() if link.last_position_at else None,
            "last_latitude": link.last_latitude, "last_longitude": link.last_longitude, "last_error": link.last_error,
            **TraccarHub.get_instance().status(link.vin)}


async def _link(session: AsyncSession, vin: str) -> Optional[TraccarLink]:
    return (await session.execute(select(TraccarLink).where(TraccarLink.vin == vin))).scalar_one_or_none()


@router.get("/vehicles/{vin}/traccar")
async def get_link(vin: str, session: AsyncSession = Depends(get_db)):
    link = await _link(session, vin)
    return {"link": serialize(link) if link else None}


@router.post("/vehicles/{vin}/traccar/test")
async def test_link(vin: str, req: TraccarTest, session: AsyncSession = Depends(get_db)):
    """Checks server and token and returns the devices to choose from (never the token)."""
    token = (req.api_token or "").strip()
    if not token:
        link = await _link(session, vin)
        token = link.api_token if link else ""
    if not token:
        raise HTTPException(status_code=422, detail="Falta el token de la API de Traccar.")
    client = TraccarClient(req.host, token, req.port, req.use_ssl, req.verify_ssl)
    try:
        server = await client.get_server()
        devices = await client.get_devices()
    except TraccarError as ex:
        raise HTTPException(status_code=502, detail=str(ex)) from ex
    return {"server": {"version": server.get("version"), "name": server.get("attributes", {}).get("title") if isinstance(server, dict) else None},
            "devices": [{"id": d.get("id"), "name": d.get("name"), "unique_id": d.get("uniqueId"), "status": d.get("status"),
                         "last_update": d.get("lastUpdate")} for d in devices if isinstance(d, dict)]}


@router.put("/vehicles/{vin}/traccar")
async def save_link(vin: str, req: TraccarSave, session: AsyncSession = Depends(get_db)):
    if (await session.execute(select(Vehicle.vin).where(Vehicle.vin == vin))).scalar_one_or_none() is None:
        raise HTTPException(status_code=404, detail="Vehículo no encontrado.")
    link = await _link(session, vin)
    token = (req.api_token or "").strip()
    if link is None:
        if not token:
            raise HTTPException(status_code=422, detail="Falta el token de la API de Traccar.")
        link = TraccarLink(vin=vin, api_token=token)
        session.add(link)
    elif token:
        link.api_token = token
    link.host, link.port, link.use_ssl, link.verify_ssl = req.host.strip(), req.port, req.use_ssl, req.verify_ssl
    link.device_id, link.device_name = req.device_id, (req.device_name or "").strip() or None
    link.enabled, link.build_trips, link.stop_minutes = req.enabled, req.build_trips, req.stop_minutes
    link.last_error = None
    await session.commit()
    await TraccarHub.get_instance().reload()
    return {"link": serialize(link)}


@router.delete("/vehicles/{vin}/traccar")
async def delete_link(vin: str, session: AsyncSession = Depends(get_db)):
    link = await _link(session, vin)
    if link is None:
        raise HTTPException(status_code=404, detail="Este coche no tiene Traccar configurado.")
    await session.delete(link)
    await session.commit()
    await TraccarHub.get_instance().reload()
    return {"status": "ok"}


@router.get("/vehicles/{vin}/gps/latest")
async def latest_fix(vin: str, session: AsyncSession = Depends(get_db)):
    """The freshest GPS fix of the car (for the live map)."""
    row = (await session.execute(
        select(GpsPosition).where(GpsPosition.vin == vin).order_by(GpsPosition.timestamp.desc()).limit(1))).scalar_one_or_none()
    if row is None:
        return {"position": None}
    return {"position": {"timestamp": row.timestamp.isoformat(), "latitude": row.latitude, "longitude": row.longitude, "speed_kmh": row.speed_kmh,
                         "course": row.course, "accuracy_m": row.accuracy_m, "ignition": row.ignition, "motion": row.motion,
                         "age_s": int((datetime.datetime.utcnow() - row.timestamp).total_seconds())}}
