"""Fuel stations and prices (services/fuel_stations.py, api/routes_fuel_stations.py): the Ministry's list, read near the car, and
the price of a detected refuel. The source is a fake one: tests never download anything."""
from __future__ import annotations

import datetime
import json

import httpx
import pytest
from freezegun import freeze_time
from sqlalchemy import select

from app.config import settings
from app.db.models import AppUser, DeviceAccess, Vehicle
from app.db.session import async_session_factory
from app.main import app
from app.security import auth, passwords
from app.services import fuel_stations as fs
from app.services import refuel_detector as rd
from app.services.refuel_detector import RefuelDetector

VIN, OTHER = "LSJA0000000000091", "LSJA0000000000092"
D = datetime.datetime


def row(ident, name, lat, lon, e5="1,559", e10="", p98="1,699"):
    return {"IDEESS": ident, "Rótulo": name, "Dirección": "CALLE MAYOR, 1", "Localidad": "VILLA DE PRUEBA", "Horario": "L-D: 24H",
            "Latitud": str(lat).replace(".", ","), "Longitud (WGS84)": str(lon).replace(".", ","), "Precio Gasolina 95 E5": e5,
            "Precio Gasolina 95 E10": e10, "Precio Gasolina 98 E5": p98, "Precio Gasoleo A": "1,459"}


PAYLOAD = {"Fecha": "25/11/2026 10:00:00", "ResultadoConsulta": "OK", "ListaEESSPrecio": [
    row("1", "GASOLINERA NORTE", 40.0000, -3.0000, e5="1,559"),
    row("2", "GASOLINERA SUR", 40.0100, -3.0000, e5="1,499"),          # ≈1.1 km north
    row("3", "SOLO E10", 40.0000, -3.0200, e5="", e10="1,529"),        # ≈1.7 km west, no 95 E5
    row("4", "LEJANA", 41.0000, -3.0000),                               # ≈111 km
    row("5", "SIN PRECIOS", 40.0, -3.0, e5="", p98=""),                 # sells no petrol: left out
    {**row("6", "SIN POSICION", 40.0, -3.0), "Latitud": ""},            # no position: left out
]}


class _Req:
    client = type("C", (), {"host": "127.0.0.1"})()
    headers = {"user-agent": "t"}


@pytest.fixture
def source(monkeypatch, tmp_path):
    """The Ministry's service, faked: counts the downloads; `fail` makes the next ones fail."""
    monkeypatch.setattr(settings, "FUEL_PRICES_ENABLED", True)
    monkeypatch.setattr(settings, "DATA_DIR", str(tmp_path))
    fs._reset_for_tests()
    calls = {"n": 0, "fail": False}

    async def download():
        calls["n"] += 1
        if calls["fail"]:
            raise RuntimeError("unreachable")
        return json.loads(json.dumps(PAYLOAD))

    monkeypatch.setattr(fs, "_download", download)
    yield calls
    fs._reset_for_tests()


@pytest.fixture
async def world(db, source):
    """juan (manager, owns the car, parked at the north station), luis (member, may only look), otro (another manager)."""
    h = passwords.hash_password("una-contraseña-larga-1")
    clients = {}
    async with async_session_factory() as s:
        s.add_all([AppUser(id=1, role="manager", username="juan", password_hash=h), AppUser(id=3, role="member", username="luis", parent_id=1, password_hash=h),
                   AppUser(id=4, role="manager", username="otro", password_hash=h)])
        s.add_all([Vehicle(owner_id=1, vin=VIN, name="Ebro", latitude=40.0, longitude=-3.0, fuel_tank_liters=50.0),
                   Vehicle(owner_id=4, vin=OTHER, name="Otro")])
        await s.flush()
        s.add(DeviceAccess(user_id=3, vin=VIN, level="view", granted_by=1))
        await s.commit()
        for uid in (1, 3, 4):
            token = await auth.create_session(s, await s.get(AppUser, uid), _Req())
            c = httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://testserver")
            c.cookies.set(auth.COOKIE_NAME, token)
            clients[uid] = c
    yield clients
    for c in clients.values():
        await c.aclose()


def test_the_ministrys_list_is_read_with_spanish_decimals_and_what_cannot_be_used_is_left_out() -> None:
    stations = fs.parse(PAYLOAD)
    assert [s.id for s in stations] == ["1", "2", "3", "4"]
    north = stations[0]
    assert (north.name, north.lat, north.lon, north.prices) == ("GASOLINERA NORTE", 40.0, -3.0, {"g95e5": 1.559, "g98e5": 1.699})
    assert "g95e5" not in stations[2].prices and stations[2].prices["g95e10"] == 1.529


async def test_the_stations_near_the_car_nearest_first_with_the_price_of_the_chosen_fuel(world, source) -> None:
    r = await world[1].get(f"/api/vehicles/{VIN}/fuel-stations")
    assert r.status_code == 200, r.text
    d = r.json()
    assert d["center"] == {"lat": 40.0, "lon": -3.0, "from": "car"} and d["fuel"] == "g95e5" and d["source_time"] == "25/11/2026 10:00:00"
    assert [(s["name"], s["price"]) for s in d["stations"]] == [("GASOLINERA NORTE", 1.559), ("GASOLINERA SUR", 1.499)]   # «solo E10» sells no E5
    assert (d["cheapest"], d["dearest"]) == (1.499, 1.559) and d["stations"][1]["distance_km"] == pytest.approx(1.11, abs=0.01)
    # another fuel, another point, a smaller radius
    d = (await world[1].get(f"/api/vehicles/{VIN}/fuel-stations", params={"fuel": "g95e10", "lat": 40.0, "lon": -3.02, "radius_km": 1})).json()
    assert d["center"]["from"] == "point" and [s["name"] for s in d["stations"]] == ["SOLO E10"]
    # the list is downloaded once and reused for an hour
    assert source["n"] == 1
    for params in ({"fuel": "gasoil"}, {"lat": 40.0}, {"radius_km": 100}):
        assert (await world[1].get(f"/api/vehicles/{VIN}/fuel-stations", params=params)).status_code == 422, params


async def test_who_may_look_and_when_there_is_nothing_to_show(world, source, monkeypatch) -> None:
    assert (await world[3].get(f"/api/vehicles/{VIN}/fuel-stations")).status_code == 200            # a member who may look
    assert (await world[4].get(f"/api/vehicles/{VIN}/fuel-stations")).status_code in (403, 404)     # another manager's car
    r = await world[4].get(f"/api/vehicles/{OTHER}/fuel-stations")                                  # a car without a position yet
    assert r.status_code == 409 and "posición" in r.json()["detail"]
    assert (await world[4].get(f"/api/vehicles/{OTHER}/fuel-stations", params={"lat": 40.0, "lon": -3.0})).status_code == 200
    monkeypatch.setattr(settings, "FUEL_PRICES_ENABLED", False)
    assert (await world[1].get(f"/api/vehicles/{VIN}/fuel-stations")).status_code == 409
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://testserver") as anon:
        assert (await anon.get(f"/api/vehicles/{VIN}/fuel-stations")).status_code == 401


async def test_a_failed_download_keeps_the_last_list_and_with_none_it_says_so(world, source) -> None:
    with freeze_time(D(2026, 11, 25, 10, 0)):
        assert (await world[1].get(f"/api/vehicles/{VIN}/fuel-stations")).status_code == 200
    source["fail"] = True
    with freeze_time(D(2026, 11, 25, 12, 0)):                                       # stale: tried again, fails, the last list is used
        r = await world[1].get(f"/api/vehicles/{VIN}/fuel-stations")
    assert r.status_code == 200 and len(r.json()["stations"]) == 2 and source["n"] == 2
    # a restart reads the saved list instead of downloading it
    fs._reset_for_tests()
    with freeze_time(D(2026, 11, 25, 10, 30)):
        assert (await world[1].get(f"/api/vehicles/{VIN}/fuel-stations")).status_code == 200
    assert source["n"] == 2
    # nothing saved and the source down: 502, never 401
    fs._reset_for_tests()
    (fs.os.remove(fs._cache_path()))
    r = await world[1].get(f"/api/vehicles/{VIN}/fuel-stations")
    assert r.status_code == 502 and "Ministerio" in r.json()["detail"]


# ───────────────────────── a detected refuel takes the station and its price ─────────────────────────
@pytest.fixture(autouse=True)
def _clean_refuels(monkeypatch):
    rd._FUEL_LOW.clear()
    rd._LAST_REFUEL_AT.clear()
    rd._PREV_STOP.clear()

    async def address(lat, lon):
        return "Calle Mayor, 1"

    monkeypatch.setattr(rd, "reverse_geocode", address)
    yield
    rd._FUEL_LOW.clear()
    rd._LAST_REFUEL_AT.clear()
    rd._PREV_STOP.clear()


async def refuel_at(lat, lon):
    t = D(2026, 11, 25, 10, 0)
    out = None
    for minutes, fuel in ((0, 20.0), (30, 90.0)):
        with freeze_time(t + datetime.timedelta(minutes=minutes)):
            async with async_session_factory() as s:
                v = (await s.execute(select(Vehicle).where(Vehicle.vin == VIN))).scalar_one()
                out = await RefuelDetector.process_snapshot(s, v, {"fuel_level": fuel, "speed_kmh": 0.0, "state": "parked", "odometer_km": 15000,
                                                                   "latitude": lat, "longitude": lon})
                await s.commit()
    return out


async def test_a_refuel_at_a_known_station_takes_its_name_its_95_price_and_so_its_cost(world) -> None:
    r = await refuel_at(40.0005, -3.0005)                                          # ≈70 m from the north station
    assert r is not None and r.liters == 35.0                                       # 70 % of 50 L
    assert (r.station, r.price_per_liter, r.total_cost) == ("GASOLINERA NORTE", 1.559, round(35.0 * 1.559, 2))


async def test_a_station_without_95_e5_prices_it_with_its_95_e10(world) -> None:
    r = await refuel_at(40.0, -3.0200)
    assert (r.station, r.price_per_liter) == ("SOLO E10", 1.529)


async def test_a_refuel_away_from_any_station_or_without_the_list_has_no_price(world, source) -> None:
    r = await refuel_at(40.05, -3.05)                                              # kilometres from every station
    assert (r.station, r.price_per_liter, r.total_cost) == (None, None, None)


async def test_with_the_prices_off_a_refuel_is_still_recorded_without_a_price(world, source, monkeypatch) -> None:
    monkeypatch.setattr(settings, "FUEL_PRICES_ENABLED", False)
    r = await refuel_at(40.0, -3.0)
    assert r is not None and r.price_per_liter is None and source["n"] == 0
