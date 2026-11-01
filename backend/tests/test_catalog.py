"""Shared catalogue: a manager proposes a part, the administrator approves or rejects it in the console, everybody sees what is approved."""
from __future__ import annotations

import httpx
import pytest

from app.admin_app import admin_app
from app.api import routes_admin_auth, routes_auth
from app.config import settings
from app.db.models import AppUser, Vehicle, VehiclePart
from app.db.session import async_session_factory
from app.main import app
from app.security import auth, passwords

VIN_A, VIN_B = "LSJA0000000000005", "LSJA0000000000006"
SETUP_CODE = "test-setup-code"
ORIGIN = {"Origin": "http://testserver"}


class _Req:
    client = type("C", (), {"host": "127.0.0.1"})()
    headers = {"user-agent": "t"}


@pytest.fixture(autouse=True)
def _clean(monkeypatch, data_dir):
    monkeypatch.setattr(settings, "SETUP_CODE", SETUP_CODE)
    auth.reset_setup_code()
    for t in (routes_auth.ip_throttle, routes_auth.pair_throttle, routes_admin_auth.ip_throttle, routes_admin_auth.pair_throttle):
        t._fails.clear()
    yield
    auth.reset_setup_code()


async def _app_client(user_id: int):
    async with async_session_factory() as s:
        user = await s.get(AppUser, user_id)
        token = await auth.create_session(s, user, _Req())
    c = httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://testserver", headers=ORIGIN)
    c.cookies.set(auth.COOKIE_NAME, token)
    return c


@pytest.fixture
async def world(db):
    """Manager Ana (2) owns VIN_A with two parts; manager Bea (3) owns VIN_B of the same model; the console admin signs in through setup."""
    h = passwords.hash_password("una-contraseña-larga-1")
    async with async_session_factory() as s:
        s.add_all([AppUser(id=2, role="manager", username="ana", vehicle_limit=-1, password_hash=h),
                   AppUser(id=3, role="manager", username="bea", vehicle_limit=-1, password_hash=h)])
        s.add_all([Vehicle(owner_id=2, vin=VIN_A, name="A", model="S900 PHEV", model_year=2025), Vehicle(owner_id=3, vin=VIN_B, name="B", model="s900 phev")])
        await s.flush()
        oil = VehiclePart(vin=VIN_A, category="engine_oil", name="Aceite", brand="Castrol", reference="Edge 0W-20", spec="0W-20", quantity=4.5)
        bare = VehiclePart(vin=VIN_A, category="air_filter", name="Filtro de aire")      # nothing to share: no brand, reference or spec
        s.add_all([oil, bare])
        await s.commit()
        ids = {"oil": oil.id, "bare": bare.id}
    console = httpx.AsyncClient(transport=httpx.ASGITransport(app=admin_app), base_url="http://testserver", headers=ORIGIN)
    r = await console.post("/api/auth/setup", json={"username": "admin", "password": "una-contraseña-larga-1", "setup_code": SETUP_CODE})
    assert r.status_code == 200, r.text
    ana, bea = await _app_client(2), await _app_client(3)
    yield {"ids": ids, "console": console, "ana": ana, "bea": bea}
    for c in (console, ana, bea):
        await c.aclose()


async def test_propose_review_and_the_approved_entry_is_visible_to_the_same_model(world) -> None:
    ana, bea, console, ids = world["ana"], world["bea"], world["console"], world["ids"]
    assert (await ana.post(f"/api/parts/{ids['bare']}/propose", json={})).status_code == 422          # nothing worth sharing
    r = await ana.post(f"/api/parts/{ids['oil']}/propose", json={})
    assert r.status_code == 200 and r.json()["status"] == "pending" and r.json()["model"] == "S900 PHEV" and r.json()["years"] == "2025"
    assert r.json()["make"] == "Ebro" and r.json()["spec_label"] == "Viscosidad" and r.json()["qty_label"] == "Litros"
    assert r.json()["fitments"] == [{"id": r.json()["fitments"][0]["id"], "make": "Ebro", "model": "S900 PHEV", "years": "2025", "oem_reference": None}]
    entry_id = r.json()["id"]
    assert (await ana.post(f"/api/parts/{ids['oil']}/propose", json={})).status_code == 409           # already proposed
    assert (await bea.post(f"/api/parts/{ids['oil']}/propose", json={})).status_code == 404           # not her part
    assert [e["id"] for e in (await ana.get("/api/catalog/mine")).json()] == [entry_id]
    assert (await bea.get("/api/catalog?model=S900%20PHEV&category=engine_oil")).json() == []         # pending: not public yet
    part = next(p for p in (await ana.get(f"/api/vehicles/{VIN_A}/parts")).json() if p["id"] == ids["oil"])
    assert part["catalog_id"] == entry_id

    queue = (await console.get("/api/admin/catalog")).json()
    assert [q["id"] for q in queue] == [entry_id] and queue[0]["proposed_by"] == "ana"
    assert (await console.get("/api/admin/catalog?status=nope")).status_code == 422
    ok = (await console.post(f"/api/admin/catalog/{entry_id}/approve")).json()
    assert ok["status"] == "approved"
    assert (await console.post(f"/api/admin/catalog/{entry_id}/approve")).status_code == 409            # reviewed already
    visible = (await bea.get("/api/catalog?model=s900%20phev&category=engine_oil")).json()                # case-insensitive model
    assert len(visible) == 1 and visible[0]["brand"] == "Castrol" and visible[0]["reference"] == "Edge 0W-20"
    assert (await bea.get("/api/catalog?model=S700&category=engine_oil")).json() == []
    assert (await bea.get("/api/catalog?category=nope")).status_code == 422
    export = (await console.get("/api/admin/catalog/export")).json()
    assert export["count"] == 1 and export["entries"][0]["reference"] == "Edge 0W-20" and "status" not in export["entries"][0]


async def test_reject_with_reason_allows_a_corrected_proposal_and_a_duplicate_counts_as_a_confirmation(world) -> None:
    ana, bea, console, ids = world["ana"], world["bea"], world["console"], world["ids"]
    first = (await ana.post(f"/api/parts/{ids['oil']}/propose", json={})).json()["id"]
    rej = (await console.post(f"/api/admin/catalog/{first}/reject", json={"reason": "La referencia no existe"})).json()
    assert rej["status"] == "rejected" and rej["reject_reason"] == "La referencia no existe"
    mine = (await ana.get("/api/catalog/mine")).json()
    assert mine[0]["status"] == "rejected"
    second = (await ana.post(f"/api/parts/{ids['oil']}/propose", json={"years": "2024-2026"})).json()      # corrected: allowed again
    assert second["id"] != first and second["years"] == "2024-2026"
    assert (await console.post(f"/api/admin/catalog/{second['id']}/approve")).json()["status"] == "approved"
    # Bea proposes the same oil for her car: approving it confirms Ana's entry instead of duplicating it
    async with async_session_factory() as s:
        p = VehiclePart(vin=VIN_B, category="engine_oil", name="Aceite", brand="castrol", reference="edge 0w-20", spec="0W-20")
        s.add(p)
        await s.commit()
        pid = p.id
    dup = (await bea.post(f"/api/parts/{pid}/propose", json={})).json()
    out = (await console.post(f"/api/admin/catalog/{dup['id']}/approve")).json()
    assert out["status"] == "rejected" and "confirmación" in out["reject_reason"]
    approved = (await bea.get("/api/catalog?model=S900%20PHEV&category=engine_oil")).json()
    assert len(approved) == 1 and approved[0]["confirmations"] == 1
    bea_part = next(x for x in (await bea.get(f"/api/vehicles/{VIN_B}/parts")).json() if x["id"] == pid)
    assert bea_part["catalog_id"] == approved[0]["id"]                                                  # her part points at the public entry


async def test_the_queue_lives_only_in_the_console(world) -> None:
    ana = world["ana"]
    assert (await ana.get("/api/admin/catalog")).status_code == 404
    anon = httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://testserver")
    assert (await anon.get("/api/catalog")).status_code == 401
    await anon.aclose()


async def test_the_admin_can_complete_or_correct_a_proposal_but_not_a_rejected_one(world) -> None:
    ana, console, ids = world["ana"], world["console"], world["ids"]
    eid = (await ana.post(f"/api/parts/{ids['oil']}/propose", json={})).json()["id"]
    d = (await console.patch(f"/api/admin/catalog/{eid}", json={"years": "2024-2026", "reference": "Edge 0W-20 LL", "notes": "", "quantity": 4.3})).json()
    assert d["years"] == "2024-2026" and d["reference"] == "Edge 0W-20 LL" and d["notes"] is None and d["quantity"] == 4.3 and d["brand"] == "Castrol"
    assert (await console.patch(f"/api/admin/catalog/{eid}", json={"model": " "})).status_code == 422
    assert (await console.patch(f"/api/admin/catalog/{eid}", json={"position": "sideways"})).status_code == 422
    assert (await console.patch(f"/api/admin/catalog/{eid}", json={"category": "tyres", "make": "EBRO"})).json()["category"] == "engine_oil"
    assert (await console.patch("/api/admin/catalog/9999", json={"years": "x"})).status_code == 404
    await console.post(f"/api/admin/catalog/{eid}/reject", json={"reason": "no"})
    assert (await console.patch(f"/api/admin/catalog/{eid}", json={"years": "2025"})).status_code == 409


async def test_a_reference_can_fit_several_models_and_a_confirmation_from_another_model_extends_it(world) -> None:
    ana, bea, console, ids = world["ana"], world["bea"], world["console"], world["ids"]
    eid = (await ana.post(f"/api/parts/{ids['oil']}/propose", json={})).json()["id"]
    d = (await console.patch(f"/api/admin/catalog/{eid}", json={"fitments": [{"make": "Ebro", "model": "S900 PHEV", "years": "2024-2026"},
                                                                              {"make": "Ebro", "model": "S800", "years": None}]})).json()
    assert [f["model"] for f in d["fitments"]] == ["S900 PHEV", "S800"] and d["model"] == "S900 PHEV" and d["years"] == "2024-2026"
    assert (await console.patch(f"/api/admin/catalog/{eid}", json={"fitments": []})).status_code == 422
    await console.post(f"/api/admin/catalog/{eid}/approve")
    assert len((await bea.get("/api/catalog?model=s800&category=engine_oil")).json()) == 1          # visible from the other model
    assert (await bea.get("/api/catalog?model=S700&category=engine_oil")).json() == []
    # an owner of a third model proposes the same reference: approving it adds that model to the entry
    async with async_session_factory() as s:
        s.add(Vehicle(owner_id=3, vin="LSJA0000000000009", name="C", model="S700", brand="Ebro"))
        p = VehiclePart(vin="LSJA0000000000009", category="engine_oil", name="Aceite", brand="Castrol", reference="EDGE 0W-20")
        s.add(p)
        await s.commit()
        pid = p.id
    dup = (await bea.post(f"/api/parts/{pid}/propose", json={})).json()
    assert (await console.post(f"/api/admin/catalog/{dup['id']}/approve")).json()["status"] == "rejected"
    entry = (await bea.get("/api/catalog?model=S700&category=engine_oil")).json()
    assert len(entry) == 1 and entry[0]["id"] == eid and entry[0]["confirmations"] == 1 and [f["model"] for f in entry[0]["fitments"]] == ["S900 PHEV", "S800", "S700"]
    export = (await console.get("/api/admin/catalog/export")).json()
    assert export["format"] == "evbro-catalog/2" and len(export["entries"][0]["fitments"]) == 3


async def test_the_maker_reference_travels_with_the_part_and_belongs_to_each_fitment(world) -> None:
    ana, bea, console, ids = world["ana"], world["bea"], world["console"], world["ids"]
    body = {"category": "engine_oil", "name": "Aceite", "brand": "Castrol", "reference": "Edge 0W-20", "oem_reference": " 1012345678 ", "spec": "0W-20"}
    assert (await ana.put(f"/api/parts/{ids['oil']}", json=body)).json()["oem_reference"] == "1012345678"
    found = (await ana.get(f"/api/vehicles/{VIN_A}/parts?q=1012345678")).json()
    assert [p["id"] for p in found] == [ids["oil"]]                                                    # searchable
    d = (await ana.post(f"/api/parts/{ids['oil']}/propose", json={})).json()
    assert d["oem_reference"] == "1012345678" and d["fitments"][0]["oem_reference"] == "1012345678"
    # Ebro's number is not another make's: each fitment has its own
    d = (await console.patch(f"/api/admin/catalog/{d['id']}", json={"fitments": [
        {"make": "Ebro", "model": "S900 PHEV", "years": "2025", "oem_reference": "1012345678"},
        {"make": "Chery", "model": "Tiggo 8", "years": None, "oem_reference": "T8-555"}]})).json()
    assert [f["oem_reference"] for f in d["fitments"]] == ["1012345678", "T8-555"] and d["oem_reference"] == "1012345678"
    await console.post(f"/api/admin/catalog/{d['id']}/approve")
    tiggo = (await bea.get("/api/catalog?model=tiggo%208&category=engine_oil")).json()
    assert len(tiggo) == 1 and next(f for f in tiggo[0]["fitments"] if f["model"] == "Tiggo 8")["oem_reference"] == "T8-555"
    export = (await console.get("/api/admin/catalog/export")).json()
    assert export["format"] == "evbro-catalog/2" and export["entries"][0]["fitments"][1]["oem_reference"] == "T8-555"


async def test_a_part_copied_from_the_catalogue_links_only_to_an_approved_entry_of_its_category(world) -> None:
    ana, bea, console, ids = world["ana"], world["bea"], world["console"], world["ids"]
    eid = (await ana.post(f"/api/parts/{ids['oil']}/propose", json={})).json()["id"]
    oil = {"category": "engine_oil", "name": "Aceite", "brand": "Castrol", "reference": "Edge 0W-20", "spec": "0W-20"}
    assert (await bea.post(f"/api/vehicles/{VIN_B}/parts", json={**oil, "catalog_id": eid})).json()["catalog_id"] is None   # still pending
    await console.post(f"/api/admin/catalog/{eid}/approve")
    assert (await bea.post(f"/api/vehicles/{VIN_B}/parts", json={**oil, "category": "coolant", "catalog_id": eid})).json()["catalog_id"] is None
    part = (await bea.post(f"/api/vehicles/{VIN_B}/parts", json={**oil, "catalog_id": eid})).json()
    assert part["catalog_id"] == eid
    kept = (await bea.put(f"/api/parts/{part['id']}", json={**oil, "quantity": 4.2})).json()                 # editing keeps the link
    assert kept["catalog_id"] == eid and kept["quantity"] == 4.2
    assert (await bea.post(f"/api/vehicles/{VIN_B}/parts", json={**oil, "catalog_id": 0})).status_code == 422


async def test_without_a_reference_the_same_maker_number_for_the_same_model_is_a_confirmation(world) -> None:
    ana, bea, console = world["ana"], world["bea"], world["console"]
    async with async_session_factory() as s:
        a = VehiclePart(vin=VIN_A, category="cabin_filter", name="Filtro de habitáculo", brand="Ebro", oem_reference="AF-1")
        b = VehiclePart(vin=VIN_B, category="cabin_filter", name="Filtro de habitáculo", oem_reference="af-1")
        s.add_all([a, b])
        await s.commit()
        aid, bid = a.id, b.id
    first = (await ana.post(f"/api/parts/{aid}/propose", json={})).json()["id"]                      # only the maker's number is enough
    await console.post(f"/api/admin/catalog/{first}/approve")
    second = (await bea.post(f"/api/parts/{bid}/propose", json={})).json()["id"]
    assert (await console.post(f"/api/admin/catalog/{second}/approve")).json()["status"] == "rejected"
    entry = (await bea.get("/api/catalog?model=S900%20PHEV&category=cabin_filter")).json()
    assert [e["id"] for e in entry] == [first] and entry[0]["confirmations"] == 1
