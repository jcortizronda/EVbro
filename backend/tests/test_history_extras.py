"""GPX export, frequent places, geofence entry/exit history and timetabled pre-conditioning."""
from __future__ import annotations

import datetime
from types import SimpleNamespace
from zoneinfo import ZoneInfo

import httpx
import pytest
from sqlalchemy import select

from app.config import settings
from app.db.models import AppUser, ClimateSchedule, Drive, DrivePosition, Geofence, GeofenceEvent, Vehicle
from app.db.session import async_session_factory
from app.main import app
from app.security import auth, passwords
from app.services import climate_schedule, places
from app.services.poller import VehiclePoller

VIN = "LSJA0000000000003"
MANUAL = "MAN00000000000003"
D = datetime.datetime
TZ = ZoneInfo("Europe/Madrid")


class _Req:
    client = type("C", (), {"host": "127.0.0.1"})()
    headers = {"user-agent": "t"}


@pytest.fixture
async def web(db, tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "DATA_DIR", str(tmp_path))
    async with async_session_factory() as s:
        s.add(Vehicle(owner_id=1, vin=VIN, name="Ebro", odometer=15000.0))
        s.add(Vehicle(owner_id=1, vin=MANUAL, name="Manual", is_manual=True))
        user = AppUser(id=1, role="manager", username="juan", vehicle_limit=-1, password_hash=passwords.hash_password("una-contraseña-larga-1"))
        s.add(user)
        await s.commit()
        token = await auth.create_session(s, user, _Req())
    c = httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://testserver")
    c.cookies.set(auth.COOKIE_NAME, token)
    yield c
    await c.aclose()


async def _drive(s, start, end_lat, end_lon, address, geofence_id=None, points=0):
    d = Drive(vin=VIN, start_time=start, end_time=start + datetime.timedelta(minutes=30), start_address="Casa", end_address=address,
              start_lat=40.0, start_lon=-3.0, end_lat=end_lat, end_lon=end_lon, end_geofence_id=geofence_id, distance_km=12.5)
    s.add(d)
    await s.flush()
    for i in range(points):
        s.add(DrivePosition(drive_id=d.id, timestamp=start + datetime.timedelta(minutes=i), latitude=40.0 + i * 0.001, longitude=-3.0,
                            altitude=650.0, speed_kmh=30.0 + i, battery_pct=80.0 - i))
    return d


# ───────────────────────── GPX ─────────────────────────
async def test_gpx_export_is_a_downloadable_track_with_time_and_extensions(web) -> None:
    async with async_session_factory() as s:
        d = await _drive(s, D(2026, 10, 28, 8, 0), 40.5, -3.5, "Trabajo", points=3)
        empty = await _drive(s, D(2026, 10, 29, 8, 0), 40.5, -3.5, "Trabajo", points=0)
        await s.commit()
        did, eid = d.id, empty.id
    r = await web.get(f"/api/drives/{did}/gpx")
    assert r.status_code == 200 and r.headers["content-type"].startswith("application/gpx+xml")
    assert 'filename="evbro-viaje-' in r.headers["content-disposition"]
    body = r.text
    assert body.count("<trkpt") == 3 and "<time>2026-10-28T08:01:00Z</time>" in body and "<ele>650.0</ele>" in body
    assert "<evbro:speed_kmh>31.0</evbro:speed_kmh>" in body and "Casa → Trabajo" in body
    assert (await web.get(f"/api/drives/{eid}/gpx")).status_code == 404
    assert (await web.get("/api/drives/9999/gpx")).status_code == 404


# ───────────────────────── Lugares frecuentes ─────────────────────────
def test_frequent_places_group_nearby_ends_and_rank_by_visits() -> None:
    def mk(i, lat, lon, addr, gf=None):
        return SimpleNamespace(end_lat=lat, end_lon=lon, end_address=addr, end_geofence_id=gf, end_time=D(2026, 1, i), start_time=D(2026, 1, i))
    drives = [mk(1, 40.4168, -3.7038, "Puerta del Sol 1"), mk(2, 40.41685, -3.70375, "Puerta del Sol 3"), mk(3, 40.4169, -3.7037, None),
              mk(4, 41.0, -3.0, "Lejos"), mk(5, 40.7, -3.9, "Oficina", gf=7), mk(6, 40.7002, -3.9001, "Oficina", gf=7),
              mk(7, None, None, "Sin coordenadas")]
    out = places.frequent_places(drives, limit=5)
    assert [p["visits"] for p in out] == [3, 2]                       # «Lejos» has one visit: not a frequent place
    assert out[0]["address"] == "Puerta del Sol 3" and out[0]["geofence_id"] is None and out[0]["last_visit"].startswith("2026-01-03")
    assert out[1]["geofence_id"] == 7 and abs(out[1]["latitude"] - 40.7001) < 1e-3


def test_frequent_places_one_place_per_geofence_and_no_borrowed_geofence_names() -> None:
    """Seen 2026-11-02: a geofence spanning two cells, and a drive whose stale end point (2 km away) was renamed «Casa» by hand."""
    home = SimpleNamespace(id=1, name="Casa", latitude=40.0, longitude=-3.0, radius_meters=150)

    def mk(i, lat, lon, addr, gf=None):
        return SimpleNamespace(end_lat=lat, end_lon=lon, end_address=addr, end_geofence_id=gf, end_time=D(2026, 10, i), start_time=D(2026, 10, i))
    drives = [mk(1, 40.0001, -3.0, "Casa", gf=1), mk(2, 39.9996, -2.9995, "Calle Mayor 3"),     # inside, before the geofence
              mk(3, 40.0005, -3.0004, "Casa", gf=1),                                                     # inside, another cell
              mk(4, 39.985, -3.0234, "Pasarela del Río"), mk(5, 39.985, -3.0233, "Casa")]     # renamed by hand, 2 km away
    out = places.frequent_places(drives, geofences=[home])
    assert [(p["address"], p["visits"], p["geofence_id"]) for p in out] == [("Casa", 3, 1), ("Pasarela del Río", 2, None)]


async def test_places_route_only_counts_closed_drives(web) -> None:
    async with async_session_factory() as s:
        for day in (1, 2):
            await _drive(s, D(2026, 10, day, 8, 0), 40.5, -3.5, "Trabajo")
        s.add(Drive(vin=VIN, start_time=D(2026, 10, 3, 8), end_time=None, end_lat=40.5, end_lon=-3.5))   # still open
        await s.commit()
    data = (await web.get(f"/api/vehicles/{VIN}/places")).json()
    assert len(data["places"]) == 1 and data["places"][0]["visits"] == 2 and data["places"][0]["address"] == "Trabajo"


async def test_places_route_names_a_place_after_the_owners_geofence(web) -> None:
    async with async_session_factory() as s:
        s.add(Geofence(id=7, owner_id=1, name="Casa", latitude=40.5, longitude=-3.5, radius_meters=150))
        for day in (1, 2):
            await _drive(s, D(2026, 10, day, 8, 0), 40.5, -3.5, "Calle Mayor 3", geofence_id=7)
        await s.commit()
    r = await web.get(f"/api/vehicles/{VIN}/places")
    assert r.status_code == 200 and r.json()["places"][0]["address"] == "Casa"


# ───────────────────────── Arrivals and departures ─────────────────────────
async def test_the_poller_records_every_geofence_transition_and_notifies_per_switch(web, monkeypatch) -> None:
    from app.services import poller as poller_mod
    sent = []
    monkeypatch.setattr(poller_mod.NotificationService, "notify_geofence_enter", classmethod(lambda cls, *a, **k: sent.append(("enter", a[1]))))
    monkeypatch.setattr(poller_mod.NotificationService, "notify_geofence_exit", classmethod(lambda cls, *a, **k: sent.append(("exit", a[1]))))

    async def _noop(*a, **k):
        return None
    monkeypatch.setattr(poller_mod.NotificationService, "notify_geofence_enter", classmethod(lambda cls, *a, **k: (sent.append(("enter", a[1])), _noop())[1]))
    monkeypatch.setattr(poller_mod.NotificationService, "notify_geofence_exit", classmethod(lambda cls, *a, **k: (sent.append(("exit", a[1])), _noop())[1]))
    async with async_session_factory() as s:
        home = Geofence(owner_id=1, name="Casa", latitude=40.0, longitude=-3.0, radius_meters=100, notify_enter=True, notify_exit=False)
        work = Geofence(owner_id=1, name="Trabajo", latitude=40.5, longitude=-3.5, radius_meters=100, notify_enter=False, notify_exit=True)
        s.add_all([home, work])
        await s.commit()
        vehicle = (await s.execute(select(Vehicle).where(Vehicle.vin == VIN))).scalar_one()
        p = VehiclePoller.get_instance()
        await p._geofence_transition(s, vehicle, work, home.id, 40.5, -3.5, 55.0, 15010.0)    # Casa → Trabajo
        await p._geofence_transition(s, vehicle, None, work.id, 40.6, -3.6, 50.0, 15020.0)    # Trabajo → nowhere
        rows = (await s.execute(select(GeofenceEvent).where(GeofenceEvent.vin == VIN).order_by(GeofenceEvent.id))).scalars().all()
    assert [(e.kind, e.geofence_name) for e in rows] == [("exit", "Casa"), ("enter", "Trabajo"), ("exit", "Trabajo")]
    assert rows[1].battery_pct == 55.0 and rows[1].odometer_km == 15010.0
    assert sent == [("exit", "Trabajo")]                                # Casa does not want exits, Trabajo does not want entries
    events = (await web.get(f"/api/geofences/events/{VIN}")).json()
    assert [e["kind"] for e in events] == ["exit", "enter", "exit"] and events[0]["geofence_name"] == "Trabajo"


# ───────────────────────── Scheduled climate ─────────────────────────
def _sched(**kw):
    base = dict(enabled=True, days="0,1,2,3,4", time_local="07:30", last_run_on=None)
    base.update(kw)
    return SimpleNamespace(**base)


def test_a_schedule_is_due_within_its_grace_window_once_a_day_on_its_days() -> None:
    monday_0730 = D(2026, 11, 2, 7, 30, tzinfo=TZ)                          # 2026-11-02 is a Monday
    assert climate_schedule.is_due(_sched(), monday_0730)
    assert climate_schedule.is_due(_sched(), monday_0730 + datetime.timedelta(minutes=9))
    assert not climate_schedule.is_due(_sched(), monday_0730 + datetime.timedelta(minutes=10))
    assert not climate_schedule.is_due(_sched(), monday_0730 - datetime.timedelta(seconds=1))
    assert not climate_schedule.is_due(_sched(days="5,6"), monday_0730)
    assert not climate_schedule.is_due(_sched(enabled=False), monday_0730)
    assert not climate_schedule.is_due(_sched(last_run_on=monday_0730.date()), monday_0730)
    assert not climate_schedule.is_due(_sched(time_local="nope"), monday_0730)
    assert climate_schedule.parse_days("4,x,9,0,0") == [0, 4]


async def test_run_due_sends_once_marks_the_day_and_skips_manual_cars(web) -> None:
    calls = []

    async def send(vin, temperature):
        calls.append((vin, temperature))
        return {"success": True, "message": "Aceptado"}
    async with async_session_factory() as s:
        s.add(ClimateSchedule(vin=VIN, time_local="07:30", days="0,1,2,3,4,5,6", temperature=21.0))
        s.add(ClimateSchedule(vin=MANUAL, time_local="07:30", days="0,1,2,3,4,5,6", temperature=21.0))
        s.add(ClimateSchedule(vin=VIN, time_local="18:00", days="0,1,2,3,4,5,6", temperature=24.0))
        await s.commit()
        now = D(2026, 11, 2, 7, 31, tzinfo=TZ)
        assert await climate_schedule.run_due(s, send, now) == 1
        assert await climate_schedule.run_due(s, send, now + datetime.timedelta(minutes=2)) == 0     # already ran today
        assert calls == [(VIN, 21.0)]
        row = (await s.execute(select(ClimateSchedule).where(ClimateSchedule.vin == VIN, ClimateSchedule.time_local == "07:30"))).scalar_one()
        assert row.last_run_on == now.date() and row.last_result.startswith("OK")


async def test_schedule_routes_validate_and_need_control_and_a_connected_car(web) -> None:
    r = await web.post(f"/api/vehicles/{VIN}/climate-schedules", json={"time_local": "07:30", "days": [0, 1, 2, 3, 4], "temperature": 22})
    assert r.status_code == 200 and r.json()["days"] == [0, 1, 2, 3, 4] and r.json()["enabled"] is True
    sid = r.json()["id"]
    for bad in ({"time_local": "7:30"}, {"time_local": "25:00"}, {"time_local": "07:30", "days": []}, {"time_local": "07:30", "temperature": 40}):
        assert (await web.post(f"/api/vehicles/{VIN}/climate-schedules", json=bad)).status_code == 422, bad
    assert (await web.post(f"/api/vehicles/{MANUAL}/climate-schedules", json={"time_local": "07:30"})).status_code == 409
    d = (await web.put(f"/api/climate-schedules/{sid}", json={"enabled": False, "days": [5, 6], "label": "Finde"})).json()
    assert d["enabled"] is False and d["days"] == [5, 6] and d["label"] == "Finde"
    assert len((await web.get(f"/api/vehicles/{VIN}/climate-schedules")).json()) == 1
    assert (await web.delete(f"/api/climate-schedules/{sid}")).json()["status"] == "ok"
    assert (await web.put("/api/climate-schedules/9999", json={"enabled": True})).status_code == 404
