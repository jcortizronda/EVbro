"""Detector de viajes: inicio recuperado cuando se detecta tarde, y descarte de viajes fantasma.

Reproduce el caso real del 28/10/2026: el viaje de vuelta se detectó con el coche ya a 54 km/h (lectura disparada por MQTT),
con inicio, odómetro y posición de mitad de ruta, y se cerró una hora después con inicio = fin (0 s, 0 km).
"""
from __future__ import annotations

import datetime

import pytest
from freezegun import freeze_time
from sqlalchemy import select

from app.db.models import Drive, DrivePosition, TelemetrySnapshot, Vehicle
from app.db.session import async_session_factory
from app.services import trip_detector as td
from app.services.trip_detector import TripDetector

VIN = "LSJA0000000000001"
CASA = (40.0, -3.0)          # donde estaba aparcado
DESTINO = (40.005, -2.996)
T0 = datetime.datetime(2026, 10, 28, 14, 36, 21)


@pytest.fixture(autouse=True)
def _estado_limpio(monkeypatch):
    for d in (td._ACTIVE_DRIVES, td._LAST_MOTION_TIME, td._LAST_IDLE, td._HV_OFF_AT):
        d.clear()

    async def sin_geocerca(session, lat, lon, **kwargs):
        return None

    async def direccion(lat, lon):
        return f"Calle {lat:.3f}"

    monkeypatch.setattr(td, "find_geofence", sin_geocerca)
    monkeypatch.setattr(td, "reverse_geocode", direccion)
    yield
    for d in (td._ACTIVE_DRIVES, td._LAST_MOTION_TIME, td._LAST_IDLE, td._HV_OFF_AT):
        d.clear()


def lectura(speed, odo, pos, soc=72.0, fuel=27.0, **extra):
    return {"speed_kmh": speed, "odometer_km": odo, "latitude": pos[0], "longitude": pos[1], "battery_level": soc,
            "fuel_level": fuel, "state": "driving" if speed > 2 else "parked", **extra}


async def procesar(vehicle, telemetry, at):
    with freeze_time(at):
        async with async_session_factory() as s:
            v = (await s.execute(select(Vehicle).where(Vehicle.vin == VIN))).scalar_one()
            res = await TripDetector.process_snapshot(s, v, telemetry)
            await s.commit()
            return res


async def drives():
    async with async_session_factory() as s:
        return list((await s.execute(select(Drive).order_by(Drive.id))).scalars())


@pytest.fixture
async def coche(db):
    async with async_session_factory() as s:
        s.add(Vehicle(owner_id=1, vin=VIN, name="Ebro", odometer=9417.0))
        await s.commit()
    return VIN


async def test_viaje_detectado_a_mitad_de_ruta_recupera_el_inicio_de_la_lectura_en_parado(coche) -> None:
    await procesar(coche, lectura(0, 9417, CASA), T0)                                   # 14:36:21 aparcado
    await procesar(coche, lectura(54, 9419, (40.006, -2.997)), T0 + datetime.timedelta(minutes=3, seconds=39))  # 14:40:00
    (d,) = await drives()
    assert d.start_estimated is True
    assert (d.start_lat, d.start_lon) == CASA and d.start_odometer == 9417 and d.start_battery_pct == 72.0
    # 2 km a 35 km/h ≈ 3,4 min antes de la lectura, y nunca antes de la lectura en parado
    assert T0 <= d.start_time < T0 + datetime.timedelta(minutes=3, seconds=39)
    assert d.start_time == T0 + datetime.timedelta(minutes=3, seconds=39) - datetime.timedelta(minutes=2 / 35 * 60)
    async with async_session_factory() as s:
        pos = list((await s.execute(select(DrivePosition).where(DrivePosition.drive_id == d.id).order_by(DrivePosition.timestamp))).scalars())
    assert (pos[0].latitude, pos[0].longitude) == CASA and pos[0].timestamp == d.start_time     # el recorrido arranca en el origen


async def test_el_viaje_completo_cierra_con_los_datos_recuperados(coche) -> None:
    await procesar(coche, lectura(0, 9417, CASA), T0)
    t = T0 + datetime.timedelta(minutes=3, seconds=39)
    await procesar(coche, lectura(54, 9419, (40.006, -2.997)), t)
    await procesar(coche, lectura(0, 9419, DESTINO), t + datetime.timedelta(seconds=70))             # aparca
    await procesar(coche, lectura(0, 9419, DESTINO), t + datetime.timedelta(minutes=6))              # cierra (≥5 min)
    (d,) = await drives()
    assert d.end_time == t and d.distance_km == 2.0 and d.start_estimated
    assert d.duration_seconds == int((t - d.start_time).total_seconds()) and d.duration_seconds > 60


async def test_sin_lectura_previa_el_inicio_no_se_marca_como_estimado(coche) -> None:
    await procesar(coche, lectura(40, 9417, CASA), T0)
    (d,) = await drives()
    assert d.start_estimated is False and d.start_time == T0 and (d.start_lat, d.start_lon) == CASA


async def test_si_el_odometro_no_ha_avanzado_el_viaje_empieza_ahora(coche) -> None:
    await procesar(coche, lectura(0, 9417, CASA), T0)
    ahora = T0 + datetime.timedelta(minutes=2)
    await procesar(coche, lectura(10, 9417, CASA), ahora)
    (d,) = await drives()
    assert d.start_estimated is False and d.start_time == ahora


async def test_una_lectura_en_parado_muy_antigua_no_sirve_de_punto_de_partida(coche) -> None:
    await procesar(coche, lectura(0, 9417, CASA), T0)
    await procesar(coche, lectura(54, 9440, DESTINO), T0 + datetime.timedelta(hours=7))
    (d,) = await drives()
    assert d.start_estimated is False and (d.start_lat, d.start_lon) == DESTINO


async def test_tras_un_reinicio_el_inicio_se_recupera_de_la_base_de_datos(coche) -> None:
    async with async_session_factory() as s:        # la lectura en parado ya estaba guardada; la memoria del detector está vacía
        s.add(TelemetrySnapshot(vin=VIN, timestamp=T0, state="parked", speed_kmh=0.0, odometer_km=9417.0, battery_level=72.0,
                                fuel_level=27.0, latitude=CASA[0], longitude=CASA[1]))
        await s.commit()
    await procesar(coche, lectura(54, 9419, (40.006, -2.997)), T0 + datetime.timedelta(minutes=4))
    (d,) = await drives()
    assert d.start_estimated and d.start_odometer == 9417 and (d.start_lat, d.start_lon) == CASA


async def test_un_viaje_de_menos_de_un_minuto_y_sin_km_es_ruido_y_se_descarta(coche) -> None:
    # una sola lectura en marcha (sin lectura previa que recuperar) y luego parado: el viaje fantasma de siempre
    await procesar(coche, lectura(54, 9419, DESTINO), T0)
    assert len(await drives()) == 1
    assert await procesar(coche, lectura(0, 9419, DESTINO), T0 + datetime.timedelta(minutes=6)) is None
    assert await drives() == []
    async with async_session_factory() as s:
        assert (await s.execute(select(DrivePosition))).scalars().all() == []                       # y no deja puntos huérfanos


async def test_un_viaje_corto_pero_con_kilometros_se_conserva(coche) -> None:
    await procesar(coche, lectura(30, 9417, CASA), T0)
    await procesar(coche, lectura(30, 9418, DESTINO), T0 + datetime.timedelta(seconds=40))
    await procesar(coche, lectura(0, 9418, DESTINO), T0 + datetime.timedelta(minutes=7))
    (d,) = await drives()
    assert d.distance_km == 1.0 and d.end_time is not None


async def test_el_estado_de_memoria_se_actualiza_solo_en_parado(coche) -> None:
    await procesar(coche, lectura(0, 9417, CASA), T0)
    await procesar(coche, lectura(50, 9418, DESTINO), T0 + datetime.timedelta(minutes=2))
    assert td._LAST_IDLE[coche]["odometer"] == 9417            # la lectura en marcha no pisa el punto de partida


# ───────────────────────── Distancia, fin por alta tensión y reinicio ─────────────────────────
async def test_the_distance_is_the_odometer_step(coche) -> None:
    # CASA → DESTINO is about 0.55 km by GPS; the odometer moved 1 km: the odometer wins, whole kilometres
    await procesar(coche, lectura(30, 9417, CASA), T0)
    await procesar(coche, lectura(30, 9418, DESTINO), T0 + datetime.timedelta(seconds=40))
    await procesar(coche, lectura(0, 9418, DESTINO), T0 + datetime.timedelta(minutes=7))
    (d,) = await drives()
    assert d.distance_km == 1.0


async def test_the_gps_track_never_adds_decimals_to_the_distance(coche) -> None:
    # A track with a fractional GPS length (≈3.4 km) and an odometer that moved 3 km: the drive is 3 km, not 3.4.
    # Those decimals would be counted again by the odometer in the next drive (see docs/trips.md, «Distancia»).
    puntos = [CASA, (40.005, -2.99), (40.015, -2.98), (40.025, -2.97)]
    for i, p in enumerate(puntos):
        await procesar(coche, lectura(40, 9417 + (3 if i == len(puntos) - 1 else 0), p), T0 + datetime.timedelta(seconds=30 * i))
    await procesar(coche, lectura(0, 9420, puntos[-1]), T0 + datetime.timedelta(minutes=8))
    (d,) = await drives()
    assert d.distance_km == 3.0


# ───────────────────────── Fin del viaje ─────────────────────────
async def test_el_viaje_termina_cuando_se_apago_el_coche_y_se_cierra_a_los_5_minutos(coche) -> None:
    await procesar(coche, lectura(0, 9417, CASA, high_voltage_on=False), T0)
    await procesar(coche, lectura(50, 9419, DESTINO, high_voltage_on=True), T0 + datetime.timedelta(minutes=4))
    t_stop = T0 + datetime.timedelta(minutes=6)
    await procesar(coche, lectura(0, 9419, DESTINO, high_voltage_on=True), t_stop)                 # parado con el coche encendido
    t_off = t_stop + datetime.timedelta(minutes=2)
    await procesar(coche, lectura(0, 9419, DESTINO, high_voltage_on=False), t_off)                 # se apaga
    ds = await drives()
    assert ds[0].end_time is None                                                                  # aún no se cierra
    await procesar(coche, lectura(0, 9419, DESTINO, high_voltage_on=False), T0 + datetime.timedelta(minutes=4 + 5, seconds=1))
    (d,) = await drives()
    assert d.end_time == t_off                                                                     # fin = cuando se apagó, no el último movimiento


async def test_si_se_vuelve_a_encender_antes_de_los_5_minutos_el_viaje_sigue(coche) -> None:
    await procesar(coche, lectura(0, 9417, CASA, high_voltage_on=False), T0)
    await procesar(coche, lectura(50, 9418, DESTINO, high_voltage_on=True), T0 + datetime.timedelta(minutes=3))
    await procesar(coche, lectura(0, 9418, DESTINO, high_voltage_on=False), T0 + datetime.timedelta(minutes=4))      # parada con el coche apagado
    await procesar(coche, lectura(0, 9418, DESTINO, high_voltage_on=True), T0 + datetime.timedelta(minutes=5))       # lo enciende otra vez
    await procesar(coche, lectura(45, 9419, CASA, high_voltage_on=True), T0 + datetime.timedelta(minutes=6))         # y sigue
    ds = await drives()
    assert len(ds) == 1 and ds[0].end_time is None
    assert td._HV_OFF_AT.get(VIN) is None


async def test_sin_dato_de_alta_tension_el_fin_es_el_ultimo_movimiento(coche) -> None:
    await procesar(coche, lectura(0, 9417, CASA), T0)
    t = T0 + datetime.timedelta(minutes=4)
    await procesar(coche, lectura(50, 9419, DESTINO), t)
    await procesar(coche, lectura(0, 9419, DESTINO), t + datetime.timedelta(minutes=6))
    (d,) = await drives()
    assert d.end_time == t


# ───────────────────────── Viaje abierto tras un reinicio ─────────────────────────
async def test_un_viaje_abierto_se_retoma_tras_un_reinicio_y_se_cierra(coche) -> None:
    await procesar(coche, lectura(0, 9417, CASA), T0)
    t = T0 + datetime.timedelta(minutes=4)
    await procesar(coche, lectura(50, 9419, DESTINO), t)
    for d in (td._ACTIVE_DRIVES, td._LAST_MOTION_TIME, td._LAST_IDLE, td._HV_OFF_AT):           # reinicio: se pierde la memoria
        d.clear()
    await procesar(coche, lectura(0, 9419, DESTINO), t + datetime.timedelta(minutes=6))
    ds = await drives()
    assert len(ds) == 1 and ds[0].end_time is not None and ds[0].distance_km == 2.0


async def test_un_viaje_abierto_muy_antiguo_no_se_retoma(coche) -> None:
    await procesar(coche, lectura(0, 9417, CASA), T0)
    await procesar(coche, lectura(50, 9419, DESTINO), T0 + datetime.timedelta(minutes=4))
    for d in (td._ACTIVE_DRIVES, td._LAST_MOTION_TIME, td._LAST_IDLE, td._HV_OFF_AT):
        d.clear()
    await procesar(coche, lectura(50, 9440, DESTINO), T0 + datetime.timedelta(hours=5))
    ds = await drives()
    assert len(ds) == 2 and ds[0].end_time is None                                              # el viejo se queda como estaba; empieza uno nuevo


async def test_mientras_hay_un_viaje_abierto_el_poller_lo_sabe(coche) -> None:
    assert TripDetector.has_open_trip(coche) is False
    await procesar(coche, lectura(50, 9419, DESTINO), T0)
    assert TripDetector.has_open_trip(coche) is True
    await procesar(coche, lectura(0, 9425, DESTINO), T0 + datetime.timedelta(minutes=6))
    assert TripDetector.has_open_trip(coche) is False
