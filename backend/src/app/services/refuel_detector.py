"""Detector de repostajes: una subida del nivel del depósito con el coche parado.

El coche da el nivel como un % (`oilSurplus`) que baja al conducir y sube al repostar. Se guarda el nivel MÁS BAJO desde el último
repostaje y, cuando una lectura con el coche parado queda al menos `FUEL_JUMP_PCT` puntos por encima, hay un repostaje. Los litros
son una estimación (puntos de % × capacidad del depósito, configurable en el vehículo); el precio lo pone el usuario después.

Cautelas: una lectura de 0 % que luego «vuelve» no es un repostaje (el coche a veces la manda con el depósito sin leer), y no se
registran dos repostajes con menos de `MIN_GAP` de diferencia.
"""
from __future__ import annotations

import datetime
import logging
from typing import Dict, Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Refuel, TelemetrySnapshot, Vehicle
from app.services.geofence import reverse_geocode

_LOGGER = logging.getLogger(__name__)

FUEL_JUMP_PCT = 8.0                                  # ≈ 4 L en un depósito de 50 L; el bamboleo del indicador es de 2-3 puntos
MIN_GAP = datetime.timedelta(minutes=30)

# Nivel más bajo visto desde el último repostaje, por VIN
_FUEL_LOW: Dict[str, float] = {}
_LAST_REFUEL_AT: Dict[str, datetime.datetime] = {}


class RefuelDetector:
    @classmethod
    async def _baseline(cls, session: AsyncSession, vin: str, now: datetime.datetime) -> Optional[float]:
        """Tras un reinicio no hay nivel en memoria: se toma el de la última lectura guardada."""
        if vin in _FUEL_LOW:
            return _FUEL_LOW[vin]
        row = (await session.execute(
            select(TelemetrySnapshot.fuel_level)
            .where(TelemetrySnapshot.vin == vin, TelemetrySnapshot.timestamp < now - datetime.timedelta(seconds=1),
                   TelemetrySnapshot.fuel_level.is_not(None), TelemetrySnapshot.fuel_level > 0)
            .order_by(TelemetrySnapshot.timestamp.desc()).limit(1)
        )).first()
        return row[0] if row else None

    @classmethod
    async def process_snapshot(cls, session: AsyncSession, vehicle: Vehicle, telemetry: Dict) -> Optional[Refuel]:
        vin = vehicle.vin
        fuel = telemetry.get("fuel_level")
        if fuel is None:
            return None
        now = datetime.datetime.utcnow()
        low = await cls._baseline(session, vin, now)
        if low is None or (fuel <= 0 and low > 2.0):
            # Primera lectura, o un 0 % de un depósito que no estaba casi vacío: lectura inválida, no cuenta para nada
            if low is None and fuel > 0:
                _FUEL_LOW[vin] = fuel
            return None

        parado = (telemetry.get("speed_kmh") or 0.0) <= 2.0 and telemetry.get("state") != "driving"
        if fuel <= low:
            _FUEL_LOW[vin] = fuel                                       # consumo: el mínimo baja
            return None
        if not parado or fuel - low < FUEL_JUMP_PCT:
            return None                                                 # bamboleo o subida con el coche en marcha: nada
        last = _LAST_REFUEL_AT.get(vin)
        if last and now - last < MIN_GAP:
            _FUEL_LOW[vin] = fuel
            return None

        lat, lon = telemetry.get("latitude"), telemetry.get("longitude")
        address = await reverse_geocode(lat, lon) if lat is not None and lon is not None else None
        liters = round((fuel - low) / 100.0 * (vehicle.fuel_tank_liters or 50.0), 1)
        refuel = Refuel(vin=vin, timestamp=now, odometer=telemetry.get("odometer_km"), fuel_before_pct=low, fuel_after_pct=fuel,
                        liters=liters, latitude=lat, longitude=lon, address=address, detected=True,
                        full_tank=None)                 # the gauge decides (>= 95 %) until the user says otherwise

        session.add(refuel)
        await session.flush()
        _FUEL_LOW[vin] = fuel
        _LAST_REFUEL_AT[vin] = now
        _LOGGER.info(f"Repostaje detectado para {vin}: {low:.0f}% -> {fuel:.0f}% (~{liters} L) [Refuel #{refuel.id}]")
        return refuel
