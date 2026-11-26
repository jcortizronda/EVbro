"""Public charging points (services/charge_stations.py, api/routes_charge_stations.py): the official DATEX II list, read near the
car. The source is a fake one with made-up sites: tests never download anything."""
from __future__ import annotations

import datetime
import io

import httpx
import pytest
from freezegun import freeze_time

from app.config import settings
from app.db.models import AppUser, Vehicle
from app.db.session import async_session_factory
from app.main import app
from app.security import auth, passwords
from app.services import charge_stations as cs

VIN, OTHER = "LSJA0000000000095", "LSJA0000000000096"
D = datetime.datetime
NS = ('xmlns:d2="http://datex2.eu/schema/3/d2Payload" xmlns:com="http://datex2.eu/schema/3/common" '
      'xmlns:loc="http://datex2.eu/schema/3/locationReferencing" xmlns:egi="http://datex2.eu/schema/3/energyInfrastructure" '
      'xmlns:fac="http://datex2.eu/schema/3/facilities" xmlns:locx="http://datex2.eu/schema/3/locationExtension" '
      'xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance"')


def connector(kind: str, watts, mode: str = "mode3AC3p") -> str:
    return (f"<egi:refillPoint><egi:connector><egi:connectorType>{kind}</egi:connectorType><egi:chargingMode>{mode}</egi:chargingMode>"
            f"<egi:maxPowerAtSocket>{watts}</egi:maxPowerAtSocket></egi:connector></egi:refillPoint>")


def site(ident, name, lat, lon, connectors, hours="", pay=("apps", "rfid"), operator="Recargas de Prueba SL") -> str:
    label = f"<fac:label>{hours}</fac:label>" if hours else ""
    lines = "".join(f'<locx:addressLine order="{i}"><locx:text><com:values><com:value lang="es">{text}</com:value></com:values></locx:text></locx:addressLine>'
                    for i, text in enumerate(("Dirección: Calle Mayor 1", "Municipio: Villa de Prueba", "Provincia: Prueba"), 1))
    return f"""<egi:energyInfrastructureSite id="{ident}" version="">
      <fac:name><com:values><com:value lang="es">{name}</com:value></com:values></fac:name>
      <fac:operatingHours id="Horario habitual" version="">{label}</fac:operatingHours>
      <fac:locationReference xsi:type="loc:PointLocation">
        <loc:_locationReferenceExtension><loc:facilityLocation><locx:address><locx:postcode>28001</locx:postcode>{lines}</locx:address></loc:facilityLocation></loc:_locationReferenceExtension>
        <loc:coordinatesForDisplay><loc:latitude>{lat}</loc:latitude><loc:longitude>{lon}</loc:longitude></loc:coordinatesForDisplay>
      </fac:locationReference>
      <fac:operator id="ES*000" version=""><fac:name><com:values><com:value lang="es">{operator}</com:value></com:values></fac:name></fac:operator>
      <egi:energyInfrastructureStation id="{ident}_1" version="">
        {"".join(f"<egi:authenticationAndIdentificationMethods>{p}</egi:authenticationAndIdentificationMethods>" for p in pay)}
        {"".join(connectors)}
      </egi:energyInfrastructureStation>
    </egi:energyInfrastructureSite>"""


ALWAYS = " ".join(f"{d} (00:00 - 23:59)" for d in ("Miercoles", "Lunes", "Sabado", "Martes", "Domingo", "Viernes", "Jueves"))
SHOP = "Jueves (08:30 - 17:30) Lunes (08:30 - 17:30) Martes (08:30 - 17:30) Miercoles (08:30 - 17:30) Viernes (08:30 - 17:30) Sabado (10:00 - 14:00)"
XML = f"""<?xml version="1.0" encoding="UTF-8"?><d2:payload {NS} lang="es">
  <com:publicationTime>2026-11-26T10:16:36.210+01:00</com:publicationTime>
  <egi:energyInfrastructureTable id="ELECTROLINERAS" version="20261126">
    {site("1", "Centro Comercial Norte", 40.0000, -3.0000, [connector("iec62196T2COMBO", "150000.0", "mode4DC")] * 2 + [connector("iec62196T2", "22000.0")] * 3, ALWAYS, ("apps", "creditCard"))}
    {site("2", "Aparcamiento Sur", 40.0100, -3.0000, [connector("iec62196T2", "7400.0"), connector("iec62196T2", "22")], SHOP)}
    {site("3", "Solo CHAdeMO", 40.0000, -3.0200, [connector("chademo", "50000.0", "mode4DC")])}
    {site("4", "Lejano", 41.0000, -3.0000, [connector("iec62196T2COMBO", "50000.0", "mode4DC")])}
    {site("5", "Sin potencia", 40.0, -3.0, [connector("iec62196T2", "")])}
    {site("6", "Sin posición", 0, 0, [connector("iec62196T2", "22000.0")])}
  </egi:energyInfrastructureTable>
</d2:payload>"""


class _Req:
    client = type("C", (), {"host": "127.0.0.1"})()
    headers = {"user-agent": "t"}


@pytest.fixture
def source(monkeypatch, tmp_path):
    """The access point, faked: counts the downloads; `fail` makes the next ones fail."""
    monkeypatch.setattr(settings, "CHARGE_STATIONS_ENABLED", True)
    monkeypatch.setattr(settings, "DATA_DIR", str(tmp_path))
    cs._reset_for_tests()
    calls = {"n": 0, "fail": False}

    async def download(path):
        calls["n"] += 1
        if calls["fail"]:
            raise RuntimeError("unreachable")
        with open(path, "w", encoding="utf-8") as f:
            f.write(XML)

    monkeypatch.setattr(cs, "_download", download)
    yield calls
    cs._reset_for_tests()


@pytest.fixture
async def world(db, source):
    """juan (manager, owns the car, parked at the north site) and otro (another manager)."""
    h = passwords.hash_password("una-contraseña-larga-1")
    clients = {}
    async with async_session_factory() as s:
        users = [AppUser(id=1, role="manager", username="juan", password_hash=h), AppUser(id=4, role="manager", username="otro", password_hash=h)]
        s.add_all(users)
        s.add_all([Vehicle(owner_id=1, vin=VIN, name="Ebro", latitude=40.0, longitude=-3.0), Vehicle(owner_id=4, vin=OTHER, name="Otro")])
        await s.commit()
        for u in users:
            c = httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://testserver")
            c.cookies.set(auth.COOKIE_NAME, await auth.create_session(s, u, _Req()))
            clients[u.username] = c
    yield clients
    for c in clients.values():
        await c.aclose()


# ───────────────────────── reading the file ─────────────────────────
def test_the_file_becomes_sites_with_their_connectors_and_what_is_unusable_is_left_out() -> None:
    stations, published = cs.parse(io.BytesIO(XML.encode()))
    assert published == "2026-11-26T10:16:36.210+01:00"
    assert [s.id for s in stations] == ["1", "2", "3", "4"]                    # no power or no position: left out
    north = stations[0]
    assert (north.name, north.operator, north.address, north.town) == ("Centro Comercial Norte", "Recargas de Prueba SL", "Calle Mayor 1", "Villa de Prueba")
    assert north.plugs == [["ccs2", 150.0, 2], ["type2", 22.0, 3]]             # the most powerful first, equal connectors counted
    assert north.pay == ["apps", "creditCard"] and north.hours == "24 h" and (north.lat, north.lon) == (40.0, -3.0)
    assert stations[1].plugs == [["type2", 22.0, 1], ["type2", 7.4, 1]]        # «22» can only be kW; «7400.0» is watts
    assert stations[2].plugs == [["chademo", 50.0, 1]]


def test_opening_hours_are_put_in_order_and_shortened() -> None:
    assert cs.tidy_hours(ALWAYS) == "24 h"
    assert cs.tidy_hours(SHOP) == "L-V 08:30-17:30 · S 10:00-14:00"
    assert cs.tidy_hours("Lunes (00:00 - 23:59) Martes (09:00 - 21:00)") == "L 24 h · M 09:00-21:00"
    assert cs.tidy_hours("") == "" and cs.tidy_hours("Consultar en la web") == "Consultar en la web"       # anything else: as it comes


def test_near_a_point_only_what_the_car_can_use_nearest_first() -> None:
    stations, _published = cs.parse(io.BytesIO(XML.encode()))
    found = cs.nearby(stations, 40.0, -3.0, 5)
    assert [s["id"] for s in found] == ["1", "2"]                              # CHAdeMO is of no use to the car; the far one is far
    assert found[0]["distance_km"] == 0 and found[0]["max_kw"] == 150 and found[0]["fast"] is True
    assert found[1]["fast"] is False and 1.0 < found[1]["distance_km"] < 1.2
    assert [s["id"] for s in cs.nearby(stations, 40.0, -3.0, 5, "dc")] == ["1"]
    assert cs.nearby(stations, 40.0, -3.0, 5, "dc")[0]["plugs"] == [["ccs2", 150.0, 2]]          # only the connectors asked for
    assert [s["id"] for s in cs.nearby(stations, 40.0, -3.0, 5, "ac", min_kw=11)] == ["1", "2"]
    assert [s["plugs"] for s in cs.nearby(stations, 40.0, -3.0, 5, "ac", min_kw=11)][1] == [["type2", 22.0, 1]]
    assert [s["id"] for s in cs.nearby(stations, 40.0, -3.0, 150, "dc", min_kw=100)] == ["1"]
    assert [s["id"] for s in cs.nearby(stations, 40.0, -3.0, 150, limit=1)] == ["1"]


# ───────────────────────── downloading ─────────────────────────
async def test_the_list_is_downloaded_once_a_day_kept_on_disk_and_survives_a_failed_download(source, tmp_path) -> None:
    with freeze_time("2026-11-26 10:00:00") as clock:
        assert len(await cs.stations()) == 4 and source["n"] == 1
        assert not (tmp_path / cs.DOWNLOAD_FILE).exists() and (tmp_path / cs.CACHE_FILE).exists()      # the big file does not stay
        clock.tick(datetime.timedelta(hours=23))
        await cs.stations()
        assert source["n"] == 1                                                # still today's
        cs._reset_for_tests()                                                  # a restart: read from disk, not downloaded
        assert len(await cs.stations()) == 4 and source["n"] == 1
        assert cs.info()["source_time"] == "2026-11-26T10:16:36.210+01:00"
        clock.tick(datetime.timedelta(hours=2))
        source["fail"] = True
        assert len(await cs.stations()) == 4 and source["n"] == 2              # the download failed: the last list stays in use


async def test_without_any_list_and_without_the_source_there_is_nothing_to_show(source) -> None:
    source["fail"] = True
    with pytest.raises(cs.Unavailable):
        await cs.stations()


# ───────────────────────── the route ─────────────────────────
async def test_the_chargers_near_the_car_or_near_a_point(world) -> None:
    juan = world["juan"]
    r = await juan.get(f"/api/vehicles/{VIN}/charge-stations")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["center"] == {"lat": 40.0, "lon": -3.0, "from": "car"} and body["kind"] == "all" and body["radius_km"] == 10
    assert [s["name"] for s in body["stations"]] == ["Centro Comercial Norte", "Aparcamiento Sur"]
    assert body["stations"][0]["plugs"] == [["ccs2", 150.0, 2], ["type2", 22.0, 3]] and body["source_time"].startswith("2026-11-26")
    far = (await juan.get(f"/api/vehicles/{VIN}/charge-stations?lat=41.0&lon=-3.0&kind=dc")).json()
    assert far["center"]["from"] == "point" and [s["name"] for s in far["stations"]] == ["Lejano"]
    assert (await juan.get(f"/api/vehicles/{VIN}/charge-stations?kind=dc&min_kw=200")).json()["stations"] == []


async def test_bad_requests_other_peoples_cars_and_a_car_without_a_position(world) -> None:
    juan = world["juan"]
    assert (await juan.get(f"/api/vehicles/{VIN}/charge-stations?kind=hydrogen")).status_code == 422
    assert (await juan.get(f"/api/vehicles/{VIN}/charge-stations?lat=40.0")).status_code == 422
    assert (await juan.get(f"/api/vehicles/{OTHER}/charge-stations")).status_code == 404         # not yours: it does not exist for you
    r = await world["otro"].get(f"/api/vehicles/{OTHER}/charge-stations")
    assert r.status_code == 409 and "posición" in r.json()["detail"]
    assert (await httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://testserver").get(f"/api/vehicles/{VIN}/charge-stations")).status_code == 401


async def test_switched_off_or_unreachable(world, source, monkeypatch) -> None:
    juan = world["juan"]
    source["fail"] = True
    r = await juan.get(f"/api/vehicles/{VIN}/charge-stations")
    assert r.status_code == 502 and "lista oficial" in r.json()["detail"]
    monkeypatch.setattr(settings, "CHARGE_STATIONS_ENABLED", False)
    r = await juan.get(f"/api/vehicles/{VIN}/charge-stations")
    assert r.status_code == 409 and source["n"] == 1                           # switched off: nothing is even tried
