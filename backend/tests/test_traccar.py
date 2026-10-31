"""Traccar Server link: parsing of Traccar positions, drives built from GPS fixes, the routes (owner only, token never returned)."""
from __future__ import annotations

import datetime

import httpx
import pytest
from sqlalchemy import select

from app.config import settings
from app.db.models import AppUser, DeviceAccess, Drive, DrivePosition, GpsPosition, TraccarLink, Vehicle
from app.db.session import async_session_factory
from app.main import app
from app.security import auth, passwords
from app.services import gps_positions, traccar
from app.services.traccar import Fix, TraccarHub, parse_position

VIN = "LSJA0000000000007"
D = datetime.datetime
ORIGIN = {"Origin": "http://testserver"}


class _Req:
    client = type("C", (), {"host": "127.0.0.1"})()
    headers = {"user-agent": "t"}


@pytest.fixture(autouse=True)
def _state(monkeypatch):
    gps_positions.reset_state()

    async def no_reload(self):
        return None
    monkeypatch.setattr(TraccarHub, "reload", no_reload)

    async def no_geocode(lat, lon):
        return f"{lat:.3f},{lon:.3f}"
    monkeypatch.setattr(gps_positions, "reverse_geocode", no_geocode)
    yield
    gps_positions.reset_state()


async def _client(user_id: int):
    async with async_session_factory() as s:
        user = await s.get(AppUser, user_id)
        token = await auth.create_session(s, user, _Req())
    c = httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://testserver", headers=ORIGIN)
    c.cookies.set(auth.COOKIE_NAME, token)
    return c


@pytest.fixture
async def world(db, tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "DATA_DIR", str(tmp_path))
    h = passwords.hash_password("una-contraseña-larga-1")
    async with async_session_factory() as s:
        s.add_all([AppUser(id=2, role="manager", username="ana", vehicle_limit=-1, password_hash=h),
                   AppUser(id=4, role="member", username="mia", parent_id=2, password_hash=h)])
        s.add(Vehicle(owner_id=2, vin=VIN, name="Manual", is_manual=True))
        await s.flush()
        s.add(DeviceAccess(user_id=4, vin=VIN, level="control"))
        await s.commit()
    owner, member = await _client(2), await _client(4)
    yield {"owner": owner, "member": member}
    await owner.aclose()
    await member.aclose()


def test_traccar_positions_are_parsed_with_knots_attributes_and_sanity_checks() -> None:
    f = parse_position({"id": 9, "deviceId": 3, "fixTime": "2026-10-30T07:00:00.000+00:00", "latitude": 40.4, "longitude": -3.7, "altitude": 650,
                        "speed": 20.0, "course": 90.0, "accuracy": 5.0, "attributes": {"ignition": True, "motion": True, "batteryLevel": 80}})
    assert f.device_id == 3 and f.speed_kmh == 37.0 and f.timestamp == D(2026, 10, 30, 7, 0) and f.ignition is True and f.motion is True
    assert f.battery_pct == 80 and f.traccar_id == 9
    assert parse_position({"deviceId": 3, "latitude": 0, "longitude": 0}) is None and parse_position({"deviceId": 3}) is None
    assert traccar.base_url("traccar.casa", None, False) == "http://traccar.casa:8082/api"
    assert traccar.base_url("https://gps.ejemplo.es/", None, False) == "https://gps.ejemplo.es/api"
    assert traccar.base_url("gps.ejemplo.es", 443, True) == "https://gps.ejemplo.es:443/api"


async def _setup_link(build_trips: bool, stop_minutes: int = 5) -> None:
    async with async_session_factory() as s:
        s.add(TraccarLink(vin=VIN, host="traccar.casa", api_token="t", device_id=3, build_trips=build_trips, stop_minutes=stop_minutes))
        await s.commit()


def _fix(minute: int, lat: float, lon: float, moving: bool, speed: float = 40.0) -> Fix:
    return Fix(device_id=3, timestamp=D(2026, 10, 30, 8, minute), latitude=lat, longitude=lon, speed_kmh=speed if moving else 0.0, motion=moving)


async def test_gps_fixes_build_a_drive_that_closes_after_the_configured_stop(world) -> None:
    await _setup_link(build_trips=True, stop_minutes=5)
    async with async_session_factory() as s:
        for f in [_fix(0, 40.000, -3.000, False), _fix(1, 40.001, -3.000, True), _fix(2, 40.010, -3.000, True), _fix(3, 40.020, -3.000, True),
                  _fix(4, 40.020, -3.0001, False), _fix(6, 40.0201, -3.0001, False), _fix(8, 40.0201, -3.0002, False), _fix(10, 40.0201, -3.0002, False)]:
            await gps_positions.handle_fix(s, VIN, f)
        drives = (await s.execute(select(Drive).where(Drive.vin == VIN))).scalars().all()
        assert len(drives) == 1
        d = drives[0]
        assert d.start_time == D(2026, 10, 30, 8, 1) and d.end_time == D(2026, 10, 30, 8, 4)        # ends at the FIRST still fix
        assert 2.0 < d.distance_km < 2.3 and d.duration_seconds == 180 and d.speed_max_kmh == 40.0
        assert d.start_address == "40.001,-3.000" and d.end_address.startswith("40.020")
        points = (await s.execute(select(DrivePosition).where(DrivePosition.drive_id == d.id))).scalars().all()
        assert len(points) >= 6
        stored = (await s.execute(select(GpsPosition).where(GpsPosition.vin == VIN))).scalars().all()
        assert len(stored) == 8
        link = (await s.execute(select(TraccarLink).where(TraccarLink.vin == VIN))).scalar_one()
        assert link.last_position_at == D(2026, 10, 30, 8, 10) and link.last_latitude == 40.0201
        # the same fix again (Traccar re-sends the last one on reconnect) is ignored
        await gps_positions.handle_fix(s, VIN, _fix(10, 40.0201, -3.0002, False))
        assert len((await s.execute(select(GpsPosition).where(GpsPosition.vin == VIN))).scalars().all()) == 8


async def test_a_wobble_while_parked_is_not_a_drive_and_a_long_gap_closes_one(world) -> None:
    await _setup_link(build_trips=True, stop_minutes=5)
    async with async_session_factory() as s:
        for f in [_fix(0, 40.0000, -3.0000, True, 5.0), _fix(1, 40.0003, -3.0000, True, 5.0), _fix(2, 40.0003, -3.0000, False),
                  _fix(8, 40.0003, -3.0000, False)]:
            await gps_positions.handle_fix(s, VIN, f)
        assert (await s.execute(select(Drive).where(Drive.vin == VIN))).scalars().all() == []            # 30 m: discarded
        # a real drive, then silence for 20 minutes, then a new drive elsewhere
        for f in [_fix(10, 41.000, -3.000, True), _fix(12, 41.020, -3.000, True), _fix(35, 41.500, -3.500, True), _fix(37, 41.520, -3.500, True)]:
            await gps_positions.handle_fix(s, VIN, f)
        drives = (await s.execute(select(Drive).where(Drive.vin == VIN).order_by(Drive.start_time))).scalars().all()
        assert len(drives) == 2 and drives[0].end_time == D(2026, 10, 30, 8, 12) and drives[1].end_time is None


async def test_without_build_trips_the_fix_only_enriches_the_open_drive(world) -> None:
    await _setup_link(build_trips=False)
    async with async_session_factory() as s:
        s.add(Drive(vin=VIN, start_time=D(2026, 10, 30, 7, 50), end_time=None))
        await s.commit()
        await gps_positions.handle_fix(s, VIN, _fix(1, 40.1, -3.1, True))
        await gps_positions.handle_fix(s, VIN, _fix(2, 40.2, -3.2, True))
        drives = (await s.execute(select(Drive).where(Drive.vin == VIN))).scalars().all()
        assert len(drives) == 1 and drives[0].end_time is None
        assert len((await s.execute(select(DrivePosition))).scalars().all()) == 2


async def test_routes_are_owner_only_never_return_the_token_and_test_uses_the_client(world, monkeypatch) -> None:
    owner, member = world["owner"], world["member"]
    calls = []

    async def fake_server(self):
        calls.append(("server", self.base, self.token))
        return {"version": "6.2", "attributes": {"title": "Casa"}}

    async def fake_devices(self):
        return [{"id": 3, "name": "Ebro GPS", "uniqueId": "123456", "status": "online", "lastUpdate": "2026-10-30T07:00:00Z"}]
    monkeypatch.setattr(traccar.TraccarClient, "get_server", fake_server)
    monkeypatch.setattr(traccar.TraccarClient, "get_devices", fake_devices)

    assert (await owner.get(f"/api/vehicles/{VIN}/traccar")).json() == {"link": None}
    assert (await member.get(f"/api/vehicles/{VIN}/traccar")).status_code == 403                 # control is not owner
    t = await owner.post(f"/api/vehicles/{VIN}/traccar/test", json={"host": "traccar.casa", "api_token": "secreto"})
    assert t.status_code == 200 and t.json()["server"]["version"] == "6.2" and t.json()["devices"][0]["name"] == "Ebro GPS"
    assert calls == [("server", "http://traccar.casa:8082/api", "secreto")] and "secreto" not in t.text
    assert (await owner.post(f"/api/vehicles/{VIN}/traccar/test", json={"host": "traccar.casa"})).status_code == 422      # no token yet
    r = await owner.put(f"/api/vehicles/{VIN}/traccar", json={"host": "traccar.casa", "api_token": "secreto", "device_id": 3, "device_name": "Ebro GPS",
                                                               "build_trips": True, "stop_minutes": 7})
    assert r.status_code == 200
    link = r.json()["link"]
    assert link["has_token"] is True and "secreto" not in r.text and link["device_id"] == 3 and link["build_trips"] is True and link["stop_minutes"] == 7
    # the stored token works for a later test and survives an update that sends no token
    assert (await owner.post(f"/api/vehicles/{VIN}/traccar/test", json={"host": "traccar.casa"})).status_code == 200
    r = await owner.put(f"/api/vehicles/{VIN}/traccar", json={"host": "traccar.casa", "device_id": 3, "enabled": False})
    assert r.json()["link"]["enabled"] is False and r.json()["link"]["has_token"] is True
    async with async_session_factory() as s:
        assert (await s.execute(select(TraccarLink.api_token).where(TraccarLink.vin == VIN))).scalar_one() == "secreto"
    assert (await member.put(f"/api/vehicles/{VIN}/traccar", json={"host": "x", "api_token": "y", "device_id": 1})).status_code == 403
    assert (await owner.get(f"/api/vehicles/{VIN}/gps/latest")).json()["position"] is None
    assert (await member.get(f"/api/vehicles/{VIN}/gps/latest")).status_code == 200                # the map is for everyone who sees the car
    assert (await owner.delete(f"/api/vehicles/{VIN}/traccar")).json()["status"] == "ok"
    assert (await owner.delete(f"/api/vehicles/{VIN}/traccar")).status_code == 404


async def test_the_hub_routes_fixes_to_the_right_car_and_survives_a_bad_one(world) -> None:
    await _setup_link(build_trips=False)
    hub = TraccarHub()
    await hub._deliver({3: VIN, 4: "OTHER"}, [_fix(1, 40.1, -3.1, True), Fix(device_id=4, timestamp=D(2026, 10, 30, 8, 1), latitude=1, longitude=1),
                                             Fix(device_id=3, timestamp=D(2026, 10, 30, 8, 2), latitude=40.2, longitude=-3.2)])
    async with async_session_factory() as s:
        stored = (await s.execute(select(GpsPosition).order_by(GpsPosition.timestamp))).scalars().all()
    assert [p.vin for p in stored] == [VIN, VIN]
    assert hub.status(VIN) == {"connected": False, "error": None}
