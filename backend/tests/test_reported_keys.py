"""Each car remembers the raw keys the cloud has ever sent for it, and only what it can report is shown or announced.

Measured on 08-11-2026 with five cars of three models on one server: the S900 never sends `inCarTemperature` nor `rWinHeatingState`,
the S800 never sends a position, only the S700 of 2026 sends `chargingCutOffSOC`.
"""
from __future__ import annotations

import httpx
import pytest
from sqlalchemy import select

from app.db.models import AppUser, Vehicle
from app.db.session import async_session_factory
from app.main import app
from app.security import auth, passwords
from app.services import ha_discovery
from app.services import poller as poller_mod

VIN = "LSJA0000000000041"


@pytest.fixture
async def car(db, monkeypatch):
    async with async_session_factory() as s:
        s.add(Vehicle(owner_id=1, vin=VIN, name="Ebro", model="S900 PHEV"))
        await s.commit()
    poller = poller_mod.VehiclePoller()
    monkeypatch.setattr(poller, "_get_or_create_ctx", lambda vin: object())
    monkeypatch.setattr(poller_mod.VehiclePoller, "_latest_snapshots", {})
    monkeypatch.setattr(poller_mod.VehiclePoller, "_last_charging_by_vin", {}, raising=False)
    return poller


async def keys():
    async with async_session_factory() as s:
        return (await s.execute(select(Vehicle.reported_keys).where(Vehicle.vin == VIN))).scalar_one()


async def test_the_keys_of_every_read_add_up_and_are_never_forgotten(car, monkeypatch) -> None:
    answers = [{"dumpEnergy": "80", "chargeGunState": "0", "lat": "37.0", "lon": "-6.0"},
               {"dumpEnergy": "79", "chargeGunState": "0", "inCarTemperature": "21.0"}]

    def probe(ctx, publish, force=False, on_data=None):
        on_data(answers.pop(0))
        return {"ok": True}

    monkeypatch.setattr(poller_mod, "probe_once", probe)
    await car._poll_vehicle(VIN)
    assert await keys() == ["chargeGunState", "dumpEnergy", "lat", "lon"]
    await car._poll_vehicle(VIN)
    assert await keys() == ["chargeGunState", "dumpEnergy", "inCarTemperature", "lat", "lon"]


async def test_the_vehicle_list_carries_the_keys_and_the_account_state(car, tmp_path, monkeypatch) -> None:
    from app.config import settings
    monkeypatch.setattr(settings, "DATA_DIR", str(tmp_path))

    class _Req:
        client = type("C", (), {"host": "127.0.0.1"})()
        headers = {"user-agent": "t"}

    async with async_session_factory() as s:
        user = AppUser(id=1, role="manager", username="juan", vehicle_limit=-1, password_hash=passwords.hash_password("una-contraseña-larga-1"))
        s.add(user)
        (await s.execute(select(Vehicle).where(Vehicle.vin == VIN))).scalar_one().reported_keys = ["dumpEnergy"]
        await s.commit()
        token = await auth.create_session(s, user, _Req())
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://testserver") as web:
        web.cookies.set(auth.COOKIE_NAME, token)
        (v,) = (await web.get("/api/vehicles")).json()
    assert v["reported_keys"] == ["dumpEnergy"] and v["account_state"] == "disconnected"      # no Ebro account connected by this manager


def test_discovery_leaves_out_what_the_car_never_reports() -> None:
    every = {t for t, _ in ha_discovery.discovery_messages("evbro", "homeassistant", VIN, "Ebro", "Ebro", "S900 PHEV")}
    s900 = {t for t, _ in ha_discovery.discovery_messages("evbro", "homeassistant", VIN, "Ebro", "Ebro", "S900 PHEV",
                                                           reported_keys=["dumpEnergy", "frontHVACState", "sunshadeState"])}
    gone = {t.split("/")[3] for t in every - s900}
    assert gone == {"rt_temp_exterior", "rt_temp_interior", "hood", "rWinHeatingState"}
    assert f"homeassistant/binary_sensor/ebro_{VIN}/sunshadeState/config" in s900
    # a car never read keeps everything (an empty list is «not known yet», not «nothing»)
    assert {t for t, _ in ha_discovery.discovery_messages("evbro", "homeassistant", VIN, "Ebro", "Ebro", "S900 PHEV", reported_keys=[])} == every


async def test_a_read_without_a_position_says_once_what_the_location_endpoint_answered(car, monkeypatch, caplog) -> None:
    import logging

    def probe(ctx, publish, force=False, on_data=None):
        on_data({"dumpEnergy": "70", "chargeGunState": "0"})
        return {"ok": True, "got_realtime": True, "got_location": False, "location_code": "A07900"}

    monkeypatch.setattr(poller_mod, "probe_once", probe)
    monkeypatch.setattr(poller_mod.VehiclePoller, "_last_location_code", {})
    with caplog.at_level(logging.INFO, logger="app.services.poller"):
        await car._poll_vehicle(VIN)
        await car._poll_vehicle(VIN)
    said = [r.message for r in caplog.records if "without a position" in r.message]
    assert len(said) == 1 and "A07900" in said[0] and "reposo" in said[0]
