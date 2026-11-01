"""Confort tab: states read from the car and the data the API offers to show what is sent."""
from __future__ import annotations

import json
import pathlib

from app.core import catalog
from app.core.telemetry import parse_telemetry_data
from tests.paths import FRONTEND

# Real 5A02 push from the HA integration: carries the climate, windscreen, steering wheel and seat state
PUSH = json.loads((pathlib.Path(__file__).parent / "vehicle" / "fixtures" / "mqtt_5a02.json").read_text())


def test_the_comfort_state_comes_from_the_car_fields() -> None:
    t = parse_telemetry_data({}, None, PUSH)
    assert t["climate_on"] is True
    assert t["front_windshield_heat"] is False and t["rear_windshield_heat"] is True
    assert t["steer_wheel_heat"] is True
    # front: ON/OFF; rear: level 0-3
    assert t["seat_heating"] == {"driver": True, "passenger": False, "rear_left": 2, "rear_right": 0}
    assert t["seat_ventilation"] == {"driver": False, "passenger": False, "rear_left": 0, "rear_right": 0}


def test_seat_ventilation_does_not_depend_on_the_tyres() -> None:
    """The tyre warning fields must not move the ventilation (checked that they did not get mixed up)."""
    t = parse_telemetry_data({}, None, {**PUSH, "rFrontTyreCall": "1", "lRearTyreCall": "1", "rRearTyreCall": "1"})
    assert t["seat_ventilation"] == {"driver": False, "passenger": False, "rear_left": 0, "rear_right": 0}
    assert t["tyre_warnings"]["fr"] is True


def test_ventilation_is_read_from_its_fields() -> None:
    t = parse_telemetry_data({}, None, {**PUSH, "dSeatVentilateState": "1", "rSeatVentilateState2": "3"})
    assert t["seat_ventilation"]["driver"] is True and t["seat_ventilation"]["rear_right"] == 3


def test_the_push_wins_over_the_probe_for_comfort() -> None:
    t = parse_telemetry_data({"steerWheelHeating": "0"}, None, {"steerWheelHeating": "1"})
    assert t["steer_wheel_heat"] is True


def test_every_comfort_command_in_the_catalogue_has_a_button() -> None:
    """If a «Climatización» command is added to the catalogue, the Confort tab must offer it."""
    web = FRONTEND / "static" / "js" / "features" / "comfort.js"
    js = web.read_text(encoding="utf-8")
    without_button = []
    for key, data in catalog.COMMANDS:
        if data.get("group") != "Climatización":
            continue
        # fixed keys ('clima_on') or built from a template (asiento_<seat>_<type>[_off])
        # seat commands are built from a template (the 4 seats are checked below)
        if f"'{key}'" not in js and not key.startswith("asiento_"):
            without_button.append(key)
    assert not without_button, f"Climatización commands without a button in comfort.js: {without_button}"
    for seat in ("conductor", "pasajero", "tras_izq", "tras_der"):
        assert f"cmd: '{seat}'" in js


async def test_the_catalogue_exposes_what_each_command_sends(db) -> None:
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
