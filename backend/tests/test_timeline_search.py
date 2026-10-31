"""Timeline of a car (all kinds merged, paged) and the global search, limited to the cars the account can see."""
from __future__ import annotations

import datetime

import httpx
import pytest

from app.db.models import (
    AppUser,
    ChargingSession,
    DeviceAccess,
    Drive,
    Expense,
    GeofenceEvent,
    MaintenanceItem,
    MaintenanceLog,
    Refuel,
    Vehicle,
    VehicleDocument,
    VehiclePart,
)
from app.db.session import async_session_factory
from app.main import app
from app.security import auth, passwords

VIN = "LSJA0000000000031"
OTHER = "LSJA0000000000032"
D = datetime.datetime
ORIGIN = {"Origin": "http://testserver"}


class _Req:
    client = type("C", (), {"host": "127.0.0.1"})()
    headers = {"user-agent": "t"}


async def _client(user_id: int):
    async with async_session_factory() as s:
        user = await s.get(AppUser, user_id)
        token = await auth.create_session(s, user, _Req())
    c = httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://testserver", headers=ORIGIN)
    c.cookies.set(auth.COOKIE_NAME, token)
    return c


@pytest.fixture
async def world(db):
    """Ana (2) owns VIN with a full history; Bea (3) owns OTHER with one part; Mia (4) is Ana's member with «view» on VIN."""
    h = passwords.hash_password("una-contraseña-larga-1")
    async with async_session_factory() as s:
        s.add_all([AppUser(id=2, role="manager", username="ana", vehicle_limit=-1, password_hash=h),
                   AppUser(id=3, role="manager", username="bea", vehicle_limit=-1, password_hash=h),
                   AppUser(id=4, role="member", username="mia", parent_id=2, password_hash=h),
                   Vehicle(owner_id=2, vin=VIN, name="Mi Ebro"), Vehicle(owner_id=3, vin=OTHER, name="El de Bea"),
                   DeviceAccess(user_id=4, vin=VIN, level="view")])
        item = MaintenanceItem(vin=VIN, name="Aceite", category="Motor")
        s.add(item)
        await s.flush()
        s.add_all([
            Drive(vin=VIN, start_time=D(2026, 10, 5, 8), end_time=D(2026, 10, 5, 9), start_address="Casa", end_address="Trabajo", distance_km=25.5),
            ChargingSession(vin=VIN, start_time=D(2026, 10, 4, 20), end_time=D(2026, 10, 4, 23), address="Garaje", kwh_added=12.3, charge_cost=1.8,
                            start_battery_pct=30, end_battery_pct=95),
            Refuel(vin=VIN, timestamp=D(2026, 10, 3), liters=40, total_cost=60, station="Repsol Alcobendas"),
            MaintenanceLog(item_id=item.id, service_date=D(2026, 10, 2), odometer=14000, cost=145, workshop="Taller Pepe", title="Cambio de aceite"),
            Expense(vin=VIN, date=D(2026, 10, 1), category="tax", title="IVTM", amount=85.5),
            VehicleDocument(vin=VIN, kind="other", title="Garantía", filename="x.pdf", original_name="garantia.pdf", content_type="application/pdf",
                            size=1, uploaded_at=D(2026, 9, 30)),
            GeofenceEvent(vin=VIN, geofence_id=1, geofence_name="Casa", kind="enter", timestamp=D(2026, 9, 29, 19), battery_pct=55),
            VehiclePart(vin=VIN, category="engine_oil", name="Aceite motor", brand="Castrol", reference="EDGE-0W20", spec="0W-20"),
            VehiclePart(vin=OTHER, category="engine_oil", name="Aceite motor", brand="Repsol", reference="ELITE-5W30", spec="5W-30"),
        ])
        await s.commit()
    ana, bea, mia = await _client(2), await _client(3), await _client(4)
    yield {"ana": ana, "bea": bea, "mia": mia}
    for c in (ana, bea, mia):
        await c.aclose()


async def test_timeline_merges_every_kind_newest_first_filters_and_pages(world) -> None:
    ana = world["ana"]
    r = await ana.get(f"/api/vehicles/{VIN}/timeline")
    assert r.status_code == 200
    body = r.json()
    kinds = [e["kind"] for e in body["events"]]
    assert kinds == ["drive", "charge", "refuel", "maintenance", "expense", "document", "geofence"] and body["has_more"] is False
    assert body["events"][0]["title"] == "Casa → Trabajo" and body["events"][0]["tab"] == "drives" and body["events"][0]["amount"] == 25.5
    assert body["events"][3]["subtitle"] == "Taller Pepe · 14.000 km" and body["events"][3]["amount"] == 145
    assert body["events"][4]["subtitle"] == "Impuestos"                       # the category in Spanish, never the key
    assert body["events"][6]["title"] == "Entra en Casa"
    # narrowed to two kinds
    r = await ana.get(f"/api/vehicles/{VIN}/timeline", params={"kinds": "refuel,expense"})
    assert [e["kind"] for e in r.json()["events"]] == ["refuel", "expense"]
    # paged: two at a time, then older than the last one shown
    r = await ana.get(f"/api/vehicles/{VIN}/timeline", params={"limit": 2})
    page = r.json()
    assert [e["kind"] for e in page["events"]] == ["drive", "charge"] and page["has_more"] is True
    r = await ana.get(f"/api/vehicles/{VIN}/timeline", params={"limit": 2, "before": page["events"][-1]["timestamp"]})
    assert [e["kind"] for e in r.json()["events"]] == ["refuel", "maintenance"]
    assert (await ana.get(f"/api/vehicles/{VIN}/timeline", params={"kinds": "nope"})).status_code == 422
    # access: Mia (view) sees it, Bea does not even learn the car exists
    assert (await world["mia"].get(f"/api/vehicles/{VIN}/timeline")).status_code == 200
    assert (await world["bea"].get(f"/api/vehicles/{VIN}/timeline")).status_code == 404


async def test_search_finds_parts_workshops_and_more_only_in_visible_cars(world) -> None:
    ana, bea, mia = world["ana"], world["bea"], world["mia"]
    hits = (await ana.get("/api/search", params={"q": "aceite"})).json()["hits"]
    assert {(h["kind"], h["title"]) for h in hits} == {("part", "Aceite motor"), ("maintenance", "Cambio de aceite")}
    assert all(h["vin"] == VIN and h["car"] == "Mi Ebro" for h in hits)
    hits = (await ana.get("/api/search", params={"q": "pepe"})).json()["hits"]
    assert len(hits) == 1 and hits[0]["kind"] == "maintenance" and hits[0]["subtitle"] == "Taller Pepe · 145,00 €" and hits[0]["tab"] == "maintenance"
    assert [h["kind"] for h in (await ana.get("/api/search", params={"q": "EDGE-0W"})).json()["hits"]] == ["part"]
    assert [h["kind"] for h in (await ana.get("/api/search", params={"q": "garant"})).json()["hits"]] == ["document"]
    assert [h["kind"] for h in (await ana.get("/api/search", params={"q": "repsol"})).json()["hits"]] == ["refuel"]     # Bea's Repsol part is not hers
    assert [h["kind"] for h in (await ana.get("/api/search", params={"q": "trabajo"})).json()["hits"]] == ["drive"]
    assert (await ana.get("/api/search", params={"q": "a"})).json()["hits"] == []                                      # too short
    # Bea only reaches her own car; Mia, Ana's member with «view», reaches Ana's car
    assert [h["vin"] for h in (await bea.get("/api/search", params={"q": "aceite"})).json()["hits"]] == [OTHER]
    assert {h["vin"] for h in (await mia.get("/api/search", params={"q": "aceite"})).json()["hits"]} == {VIN}
