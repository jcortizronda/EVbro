"""Each car remembers the commands the Ebro cloud refused with A00084 («no permission»): the controls say so and the Home
Assistant discovery leaves them out; an accepted send clears the memory.

08-11-2026: an S900 answered A00084 to the rear window heater five times in a row; the owner had no way to know it was the car.
"""
from __future__ import annotations

import httpx
from sqlalchemy import select

from app.db.models import AppUser, Vehicle
from app.db.session import async_session_factory
from app.main import app
from app.security import auth, passwords
from app.services import ha_discovery
from app.services.poller import VehiclePoller

VIN = "LSJA0000000000061"


async def refused() -> list:
    async with async_session_factory() as s:
        return (await s.execute(select(Vehicle.rejected_commands).where(Vehicle.vin == VIN))).scalar_one() or []


async def test_a00084_is_remembered_per_car_and_an_accepted_send_forgets_it(db) -> None:
    async with async_session_factory() as s:
        s.add(Vehicle(owner_id=1, vin=VIN, name="Ebro", model="S900 PHEV"))
        await s.commit()
    await VehiclePoller._remember_rejection(VIN, "defrost_luneta", False, "A00084")
    await VehiclePoller._remember_rejection(VIN, "bloquear", False, "A00082")             # busy: not a refusal
    await VehiclePoller._remember_rejection(VIN, "clima_on", False, None)                 # a PIN problem: not a refusal
    assert await refused() == ["defrost_luneta"]
    await VehiclePoller._remember_rejection(VIN, "defrost_luneta", False, "A00084")       # again: once
    await VehiclePoller._remember_rejection(VIN, "volante_caliente", False, "A00084")
    assert await refused() == ["defrost_luneta", "volante_caliente"]
    await VehiclePoller._remember_rejection(VIN, "defrost_luneta", True, None)
    assert await refused() == ["volante_caliente"]
    await VehiclePoller._remember_rejection("LSJA0000000000000", "x", False, "A00084")   # an unknown car: nothing happens


async def test_the_vehicle_list_carries_the_refused_commands(db, tmp_path, monkeypatch) -> None:
    from app.config import settings
    monkeypatch.setattr(settings, "DATA_DIR", str(tmp_path))

    class _Req:
        client = type("C", (), {"host": "127.0.0.1"})()
        headers = {"user-agent": "t"}

    async with async_session_factory() as s:
        user = AppUser(id=1, role="manager", username="juan", vehicle_limit=-1, password_hash=passwords.hash_password("una-contraseña-larga-1"))
        s.add(user)
        s.add(Vehicle(owner_id=1, vin=VIN, name="Ebro", model="S900 PHEV", rejected_commands=["defrost_luneta"]))
        await s.commit()
        token = await auth.create_session(s, user, _Req())
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://testserver") as web:
        web.cookies.set(auth.COOKIE_NAME, token)
        (v,) = (await web.get("/api/vehicles")).json()
    assert v["rejected_commands"] == ["defrost_luneta"]


def test_discovery_offers_no_switch_for_a_command_the_car_refuses() -> None:
    every = {t for t, _ in ha_discovery.discovery_messages("evbro", "homeassistant", VIN, "Ebro", "Ebro", "S900 PHEV")}
    s900 = {t for t, _ in ha_discovery.discovery_messages("evbro", "homeassistant", VIN, "Ebro", "Ebro", "S900 PHEV",
                                                           rejected_commands=["defrost_luneta"])}
    assert {t.split("/")[3] for t in every - s900} == {"rWinHeatingState"}
    assert f"homeassistant/switch/ebro_{VIN}/frontWindshieldHeat/config" in s900
