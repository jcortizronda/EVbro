"""Detector y gestor de trayectos / viajes."""
from __future__ import annotations

import datetime
import logging
from typing import Dict, Optional

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Drive, DrivePosition, TelemetrySnapshot, Vehicle
from app.services.geofence import (
    find_geofence,
    reverse_geocode,
)

_LOGGER = logging.getLogger(__name__)

# Memoria de viajes activos por VIN
_ACTIVE_DRIVES: Dict[str, int] = {}
# Última hora en que se detectó movimiento (para saber si se ha aparcado definitivamente)
_LAST_MOTION_TIME: Dict[str, datetime.datetime] = {}
# Última lectura SIN movimiento por VIN: si el viaje se detecta tarde (con el coche ya en marcha), de ahí se recupera el inicio
_LAST_IDLE: Dict[str, Dict] = {}
# Cuándo se vio por primera vez la alta tensión apagada tras el último movimiento: es la hora de fin del viaje
# (el viaje se cierra a los 5 min, pero termina cuando el coche se apagó)
_HV_OFF_AT: Dict[str, datetime.datetime] = {}
# Minutos de parada para considerar un viaje finalizado
IDLE_MINUTES_TO_END_TRIP = 5
# Un viaje abierto tras un reinicio solo se retoma si hubo actividad hace menos de esto
RESUME_MAX_AGE = datetime.timedelta(minutes=30)

# Recuperación del inicio de un viaje detectado tarde
ASSUMED_TRIP_SPEED_KMH = 35.0          # para estimar cuánto llevaba en marcha según los km recorridos desde la lectura en parado
BACKFILL_MAX_AGE = datetime.timedelta(hours=6)       # una lectura en parado más antigua ya no sirve de punto de partida
BACKFILL_MAX_MINUTES = 90.0

# Un «viaje» de menos de este tiempo y sin kilómetros es ruido (maniobra de aparcamiento, una sola lectura en marcha)
MIN_TRIP_SECONDS = 60


def resolve_distance(start_odometer: Optional[float], end_odometer: Optional[float]) -> Optional[float]:
    """Distance of a drive: the odometer alone, in whole kilometres, never negative.

    The GPS track is deliberately left out. The car's odometer only shows whole kilometres, so adding the GPS decimals on top
    (as earlier versions did) counted them twice: the next drive's odometer step already included them, and sums of drives
    drifted above the car's own total. The track is kept for the map, the replay and the geofences, where a coverage gap is
    visible instead of silently wrong. Short hops may read 0 km, exactly as on the dashboard."""
    if start_odometer is None or end_odometer is None:
        return None
    return float(max(0, int(end_odometer) - int(start_odometer)))


class TripDetector:
    @staticmethod
    def has_open_trip(vin: str) -> bool:
        """True mientras haya un viaje abierto: el poller acorta el sondeo para poder cerrarlo a los 5 min de parada."""
        return vin in _ACTIVE_DRIVES

    @classmethod
    async def _resume_open_drive(cls, session: AsyncSession, vin: str, now: datetime.datetime) -> Optional[int]:
        """Tras un reinicio: retoma el viaje abierto de este coche si hubo actividad reciente (si no, se deja como está)."""
        drive = (await session.execute(
            select(Drive).where(Drive.vin == vin, Drive.end_time.is_(None)).order_by(Drive.start_time.desc()).limit(1)
        )).scalar_one_or_none()
        if drive is None:
            return None
        last = (await session.execute(
            select(DrivePosition).where(DrivePosition.drive_id == drive.id).order_by(DrivePosition.timestamp.desc()).limit(1)
        )).scalar_one_or_none()
        last_at = last.timestamp if last else drive.start_time
        if now - last_at > RESUME_MAX_AGE:
            return None
        _ACTIVE_DRIVES[vin] = drive.id
        _LAST_MOTION_TIME[vin] = last_at
        _LOGGER.info(f"Viaje abierto retomado tras un reinicio para {vin} [Drive #{drive.id}]")
        return drive.id

    @classmethod
    async def _previous_idle(cls, session: AsyncSession, vin: str, now: datetime.datetime) -> Optional[Dict]:
        """Última lectura en parado antes de ésta: de memoria o, tras un reinicio, de la base de datos."""
        prev = _LAST_IDLE.get(vin)
        if prev is not None:
            return prev
        stmt = (
            select(TelemetrySnapshot)
            .where(TelemetrySnapshot.vin == vin, TelemetrySnapshot.timestamp < now - datetime.timedelta(seconds=1))
            .order_by(TelemetrySnapshot.timestamp.desc())
            .limit(5)
        )
        for row in (await session.execute(stmt)).scalars():
            if (row.speed_kmh or 0.0) <= 2.0:
                return {"time": row.timestamp, "lat": row.latitude, "lon": row.longitude, "odometer": row.odometer_km,
                        "battery": row.battery_level, "fuel": row.fuel_level}
        return None

    @staticmethod
    def _backfill(prev: Optional[Dict], now: datetime.datetime, odometer: Optional[float]) -> Optional[Dict]:
        """Inicio estimado de un viaje detectado con el coche ya en marcha, o None si no hay base fiable.

        Hace falta una lectura en parado reciente con posición y que el odómetro haya avanzado desde entonces (si no, el viaje
        empezó justo ahora). La hora se estima por los km recorridos, sin pasar nunca de la lectura en parado."""
        if not prev or prev.get("lat") is None or prev.get("lon") is None:
            return None
        if odometer is None or prev.get("odometer") is None or odometer <= prev["odometer"]:
            return None
        if now - prev["time"] > BACKFILL_MAX_AGE:
            return None
        minutes = min(BACKFILL_MAX_MINUTES, (odometer - prev["odometer"]) / ASSUMED_TRIP_SPEED_KMH * 60.0)
        return {**prev, "start_time": max(prev["time"], now - datetime.timedelta(minutes=minutes))}

    @classmethod
    async def process_snapshot(
        cls,
        session: AsyncSession,
        vehicle: Vehicle,
        telemetry: Dict,
    ) -> Optional[Drive]:
        """Procesa una instantánea de telemetría y gestiona el ciclo de vida del viaje."""
        vin = vehicle.vin
        now = datetime.datetime.utcnow()
        speed = telemetry.get("speed_kmh", 0.0) or 0.0
        lat = telemetry.get("latitude")
        lon = telemetry.get("longitude")
        alt = telemetry.get("altitude")
        heading = telemetry.get("heading")
        battery = telemetry.get("battery_level")
        odometer = telemetry.get("odometer_km")

        active_drive_id = _ACTIVE_DRIVES.get(vin)
        if active_drive_id is None and vin not in _LAST_IDLE:
            active_drive_id = await cls._resume_open_drive(session, vin, now)
        is_moving = speed > 2.0 or telemetry.get("state") == "driving"

        if is_moving:
            _LAST_MOTION_TIME[vin] = now
        # Alta tensión: la primera lectura con ella apagada fija la hora de fin; volver a encenderla la descarta
        hv = telemetry.get("high_voltage_on")
        if hv is False and not is_moving:
            _HV_OFF_AT.setdefault(vin, now)
        else:
            _HV_OFF_AT.pop(vin, None)

        # 1. Comprobar si hay que iniciar un nuevo viaje
        if active_drive_id is None and is_moving and lat is not None and lon is not None:
            # Si el viaje se detecta tarde (la lectura llega con el coche ya en marcha), el inicio es el de la última lectura en parado
            back = cls._backfill(await cls._previous_idle(session, vin, now), now, odometer)
            s_lat, s_lon = (back["lat"], back["lon"]) if back else (lat, lon)

            # Buscar si está en una geocerca
            gf = await find_geofence(session, s_lat, s_lon, owner_id=vehicle.owner_id)
            addr = await reverse_geocode(s_lat, s_lon)
            if gf:
                addr = gf.name

            temp = telemetry.get("outside_temp")
            climate = telemetry.get("climate_on", False)

            new_drive = Drive(
                vin=vin,
                start_time=back["start_time"] if back else now,
                start_lat=s_lat,
                start_lon=s_lon,
                start_address=addr,
                start_geofence_id=gf.id if gf else None,
                start_odometer=back["odometer"] if back else odometer,
                start_battery_pct=back["battery"] if back else battery,
                start_fuel=back["fuel"] if back else telemetry.get("fuel_level"),
                start_estimated=bool(back),
                outside_temp_avg=temp,
                climate_used=climate,
                speed_max_kmh=speed,
                speed_avg_kmh=speed,
            )
            session.add(new_drive)
            await session.flush()
            if back:
                # El recorrido empieza en el punto de partida, no a mitad de ruta
                session.add(DrivePosition(drive_id=new_drive.id, timestamp=new_drive.start_time, latitude=s_lat, longitude=s_lon,
                                          speed_kmh=0.0, battery_pct=back["battery"]))

            active_drive_id = new_drive.id
            _ACTIVE_DRIVES[vin] = active_drive_id
            _LOGGER.info(f"Nuevo viaje iniciado para {vin} [Drive #{active_drive_id}]" + (" (inicio estimado)" if back else ""))

        # 2. Si hay un viaje activo, registrar punto y comprobar si finaliza
        if active_drive_id is not None:
            drive = await session.get(Drive, active_drive_id)
            if not drive:
                _ACTIVE_DRIVES.pop(vin, None)
                return None

            # Añadir posición si hay coordenadas válidas
            if lat is not None and lon is not None:
                # Comprobar si el punto se movió al menos 5 metros o si la velocidad es positiva
                pos = DrivePosition(
                    drive_id=active_drive_id,
                    timestamp=now,
                    latitude=lat,
                    longitude=lon,
                    altitude=alt,
                    heading=heading,
                    speed_kmh=speed,
                    battery_pct=battery,
                )
                session.add(pos)

                # Actualizar velocidad máxima
                if speed > (drive.speed_max_kmh or 0.0):
                    drive.speed_max_kmh = round(speed, 1)

            # 3. Comprobar condición de fin de viaje (detenido por más de IDLE_MINUTES_TO_END_TRIP)
            last_motion = _LAST_MOTION_TIME.get(vin, drive.start_time)
            stopped_duration = (now - last_motion).total_seconds() / 60.0

            # También finalizar si el coche se pone a cargar o se duerme
            force_end = telemetry.get("is_charging") or telemetry.get("cable_connected")

            if stopped_duration >= IDLE_MINUTES_TO_END_TRIP or force_end:
                # Cerrar viaje
                hv_off = _HV_OFF_AT.get(vin)
                if stopped_duration >= IDLE_MINUTES_TO_END_TRIP:
                    # El viaje acaba cuando se apagó el coche (si se vio), nunca antes del último movimiento
                    drive.end_time = hv_off if hv_off and last_motion <= hv_off <= now else last_motion
                else:
                    drive.end_time = now
                drive.end_lat = lat
                drive.end_lon = lon
                drive.end_odometer = odometer
                drive.end_battery_pct = battery

                # Distance (odometer only) and duration
                distance = resolve_distance(drive.start_odometer, drive.end_odometer)
                if distance is not None:
                    drive.distance_km = distance

                drive.duration_seconds = max(0, int((drive.end_time - drive.start_time).total_seconds()))

                if drive.start_battery_pct is not None and drive.end_battery_pct is not None:
                    drive.battery_used_pct = max(0.0, round(drive.start_battery_pct - drive.end_battery_pct, 1))
                    drive.energy_consumed_kwh = round((drive.battery_used_pct / 100.0) * (vehicle.battery_capacity_kwh or 19.27), 2)

                drive.end_fuel = telemetry.get("fuel_level")
                if drive.start_fuel is not None and drive.end_fuel is not None:
                    drive.fuel_used_l = max(0.0, round(drive.start_fuel - drive.end_fuel, 2))

                # Geocodificar destino
                end_gf = await find_geofence(session, lat, lon, owner_id=vehicle.owner_id)
                end_addr = await reverse_geocode(lat, lon)
                if end_gf:
                    end_addr = end_gf.name
                drive.end_geofence_id = end_gf.id if end_gf else None
                drive.end_address = end_addr

                # Velocidad media
                if drive.duration_seconds > 0 and drive.distance_km > 0:
                    hours = drive.duration_seconds / 3600.0
                    drive.speed_avg_kmh = round(drive.distance_km / hours, 1)

                if drive.duration_seconds < MIN_TRIP_SECONDS and not (distance and distance > 0):
                    # Ruido: una maniobra o una sola lectura en marcha, sin kilómetros. No es un viaje.
                    _LOGGER.info(f"Descartado un viaje de {drive.duration_seconds} s sin kilómetros para {vin} [Drive #{drive.id}]")
                    await session.flush()      # (el punto de esta misma lectura aún no estaba guardado: se guarda y se borra con el resto)
                    await session.execute(delete(DrivePosition).where(DrivePosition.drive_id == drive.id))
                    await session.delete(drive)
                    await session.flush()
                    _ACTIVE_DRIVES.pop(vin, None)
                    _LAST_MOTION_TIME.pop(vin, None)
                    _HV_OFF_AT.pop(vin, None)
                    _remember_idle(vin, now, lat, lon, odometer, battery, telemetry, is_moving)
                    return None

                await session.flush()
                _LOGGER.info(
                    f"Viaje completado para {vin} [Drive #{drive.id}]: "
                    f"{drive.distance_km} km en {drive.duration_seconds // 60} min."
                )
                _ACTIVE_DRIVES.pop(vin, None)
                _LAST_MOTION_TIME.pop(vin, None)
                _HV_OFF_AT.pop(vin, None)
                _remember_idle(vin, now, lat, lon, odometer, battery, telemetry, is_moving)
                return drive

        _remember_idle(vin, now, lat, lon, odometer, battery, telemetry, is_moving)
        return None


def _remember_idle(vin, now, lat, lon, odometer, battery, telemetry, is_moving) -> None:
    """Guarda la última lectura en parado (punto de partida si el siguiente viaje se detecta tarde)."""
    if not is_moving:
        _LAST_IDLE[vin] = {"time": now, "lat": lat, "lon": lon, "odometer": odometer, "battery": battery,
                           "fuel": telemetry.get("fuel_level")}
