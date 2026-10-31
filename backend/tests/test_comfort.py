"""Pestaña Confort: estados que lee del coche y datos que ofrece la API para mostrar qué se envía."""
from __future__ import annotations

import json
import pathlib

from app.core import catalog
from app.core.telemetry import parse_telemetry_data
from tests.paths import FRONTEND

# Push 5A02 real de la integración de HA: trae el estado de clima, lunas, volante y asientos
PUSH = json.loads((pathlib.Path(__file__).parent / "vehicle" / "fixtures" / "mqtt_5a02.json").read_text())


def test_el_estado_de_confort_sale_de_los_campos_del_coche() -> None:
    t = parse_telemetry_data({}, None, PUSH)
    assert t["climate_on"] is True
    assert t["front_windshield_heat"] is False and t["rear_windshield_heat"] is True
    assert t["steer_wheel_heat"] is True
    # delanteros: ON/OFF; traseros: nivel 0-3
    assert t["seat_heating"] == {"driver": True, "passenger": False, "rear_left": 2, "rear_right": 0}
    assert t["seat_ventilation"] == {"driver": False, "passenger": False, "rear_left": 0, "rear_right": 0}


def test_la_ventilacion_de_asientos_no_depende_de_los_neumaticos() -> None:
    """Los campos de aviso de neumáticos no deben mover la ventilación (se comprobó que no se mezclaban)."""
    t = parse_telemetry_data({}, None, {**PUSH, "rFrontTyreCall": "1", "lRearTyreCall": "1", "rRearTyreCall": "1"})
    assert t["seat_ventilation"] == {"driver": False, "passenger": False, "rear_left": 0, "rear_right": 0}
    assert t["tyre_warnings"]["fr"] is True


def test_la_ventilacion_se_lee_de_sus_campos() -> None:
    t = parse_telemetry_data({}, None, {**PUSH, "dSeatVentilateState": "1", "rSeatVentilateState2": "3"})
    assert t["seat_ventilation"]["driver"] is True and t["seat_ventilation"]["rear_right"] == 3


def test_el_push_manda_sobre_la_sonda_en_el_confort() -> None:
    t = parse_telemetry_data({"steerWheelHeating": "0"}, None, {"steerWheelHeating": "1"})
    assert t["steer_wheel_heat"] is True


def test_todas_las_ordenes_de_confort_del_catalogo_tienen_boton() -> None:
    """Si se añade una orden de «Climatización» al catálogo, la pestaña Confort debe ofrecerla."""
    web = FRONTEND / "static" / "js" / "features" / "comfort.js"
    js = web.read_text(encoding="utf-8")
    sin_boton = []
    for key, data in catalog.COMMANDS:
        if data.get("group") != "Climatización":
            continue
        # claves fijas ('clima_on') o construidas con plantilla (asiento_<plaza>_<tipo>[_off])
        # las órdenes de asiento se construyen con plantilla (se comprueban las 4 plazas abajo)
        if f"'{key}'" not in js and not key.startswith("asiento_"):
            sin_boton.append(key)
    assert not sin_boton, f"Órdenes de Climatización sin botón en comfort.js: {sin_boton}"
    for plaza in ("conductor", "pasajero", "tras_izq", "tras_der"):
        assert f"cmd: '{plaza}'" in js


async def test_el_catalogo_expone_lo_que_se_envia_con_cada_comando(db) -> None:
    import httpx

    from app.db.models import AppUser
    from app.db.session import async_session_factory
    from app.main import app
    from app.security import auth, passwords

    async with async_session_factory() as s:
        user = AppUser(id=1, role="manager", username="juan", password_hash=passwords.hash_password("una-contraseña-larga-1"))
        s.add(user)
        await s.commit()

        class Req:
            client = type("C", (), {"host": "127.0.0.1"})()
            headers = {"user-agent": "t"}
        token = await auth.create_session(s, user, Req())
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://testserver") as c:
        c.cookies.set(auth.COOKIE_NAME, token)
        cat = {x["key"]: x for x in (await c.get("/api/commands/catalog")).json()}
    assert cat["clima_on"]["endpoint"] == "airControl"
    assert cat["clima_on"]["body"] == {"airControlType": "1", "airType": "1", "temperature": "21.0", "times": "15"}
    assert cat["alarma_off"]["endpoint"] == "/act/theftAlarm/setSwitch"
    assert cat["asiento_conductor_ventilacion"]["body"] == {"mSeatAiry": "3", "times": "15"}
