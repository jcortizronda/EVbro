"""Vehicle parts (car sheet), part categories, search, and the parts recorded with an intervention."""
from __future__ import annotations

import httpx
import pytest

from app.db.models import AppUser, Vehicle
from app.db.session import async_session_factory
from app.main import app
from app.security import auth, passwords

VIN = "LSJA0000000000001"


class _Req:
    client = type("C", (), {"host": "127.0.0.1"})()
    headers = {"user-agent": "t"}


@pytest.fixture
async def web(db):
    async with async_session_factory() as s:
        s.add(Vehicle(owner_id=1, vin=VIN, name="Ebro", model="S900 PHEV", odometer=15000.0))
        user = AppUser(id=1, role="manager", username="juan", password_hash=passwords.hash_password("una-contraseña-larga-1"))
        s.add(user)
        await s.commit()
        token = await auth.create_session(s, user, _Req())
    c = httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://testserver")
    c.cookies.set(auth.COOKIE_NAME, token)
    yield c
    await c.aclose()


async def add(web, **kw):
    body = {"category": "engine_oil", "name": "Aceite 0W-20", "brand": "Castrol", "reference": "15B7A2", "spec": "0W-20", "quantity": 4.2, **kw}
    r = await web.post(f"/api/vehicles/{VIN}/parts", json=body)
    assert r.status_code == 200, r.text
    return r.json()


async def test_categories_are_offered_with_groups_and_positions(web) -> None:
    d = (await web.get("/api/part-categories")).json()
    keys = {c["key"] for c in d["categories"]}
    assert {"engine_oil", "oil_filter", "air_filter", "cabin_filter", "brake_pads", "tyres", "battery_12v", "wiper_blades", "bulbs"} <= keys
    assert {g["key"] for g in d["groups"]} >= {"engine", "filters", "brakes", "tyres", "electrical", "other"}
    assert {p["key"] for p in d["positions"]} == {"front", "rear", "spare"}


async def test_create_list_update_and_delete_a_part(web) -> None:
    p = await add(web)
    assert p["category_label"] == "Aceite de motor" and p["group"] == "engine" and p["quantity"] == 4.2
    r = await web.put(f"/api/parts/{p['id']}", json={"category": "engine_oil", "name": "Aceite 5W-30", "brand": "Shell", "spec": "5W-30"})
    assert r.json()["name"] == "Aceite 5W-30" and r.json()["reference"] is None              # a PUT replaces the sheet entry
    assert [x["id"] for x in (await web.get(f"/api/vehicles/{VIN}/parts")).json()] == [p["id"]]
    assert (await web.delete(f"/api/parts/{p['id']}")).status_code == 200
    assert (await web.delete(f"/api/parts/{p['id']}")).status_code == 404
    assert (await web.put(f"/api/parts/{p['id']}", json={"category": "engine_oil"})).status_code == 404


async def test_blank_name_falls_back_to_the_category_label(web) -> None:
    p = await add(web, category="cabin_filter", name="  ", brand="", reference="")
    assert p["name"] == "Filtro de habitáculo" and p["brand"] is None and p["reference"] is None


async def test_one_part_per_category_and_position_but_bulbs_can_repeat(web) -> None:
    a = await add(web, category="brake_pads", position="front", name="Pastillas", reference="A1")
    b = await add(web, category="brake_pads", position="front", name="Pastillas", reference="A2")       # replaces the front ones
    c = await add(web, category="brake_pads", position="rear", name="Pastillas", reference="R1")
    assert a["id"] == b["id"] and b["reference"] == "A2" and c["id"] != a["id"]
    h7 = await add(web, category="bulbs", name="Faros H7", reference="H7")
    led = await add(web, category="bulbs", name="Piloto LED", reference="W5W")
    assert h7["id"] != led["id"]
    assert len((await web.get(f"/api/vehicles/{VIN}/parts")).json()) == 4


async def test_tyres_keep_their_season_on_the_sheet_but_not_the_dot_and_bad_values_are_rejected(web) -> None:
    t = await add(web, category="tyres", position="front", name="Michelin Primacy", spec="235/55 R19", attrs={"season": "summer", "dot": "2324", "junk": "x"})
    assert t["attrs"] == {"season": "summer"}                 # unknown keys are dropped; the DOT is one tyre's, the sheet keeps the consumable
    for attrs in ({"dot": "23"}, {"dot": "abcd"}, {"season": "monsoon"}):
        r = await web.post(f"/api/vehicles/{VIN}/parts", json={"category": "tyres", "position": "rear", "attrs": attrs})
        assert r.status_code == 422, attrs
    assert (await web.post(f"/api/vehicles/{VIN}/parts", json={"category": "engine_oil", "attrs": {"dot": "2324"}})).json()["attrs"] == {}


async def test_validation_of_category_position_and_lengths(web) -> None:
    for body in ({"category": "magic"}, {"category": "engine_oil", "position": "front"}, {"category": "tyres", "position": "left"},
                 {"category": "tyres", "name": "x" * 151}, {"category": "tyres", "quantity": -1}):
        assert (await web.post(f"/api/vehicles/{VIN}/parts", json=body)).status_code == 422, body
    assert (await web.post("/api/vehicles/NOPE/parts", json={"category": "tyres"})).status_code == 404


async def test_search_finds_by_name_brand_reference_spec_and_category(web) -> None:
    await add(web)
    await add(web, category="oil_filter", name="Filtro aceite", brand="Mann", reference="HU7008Z", spec=None)
    await add(web, category="tyres", position="front", name="Primacy 4", brand="Michelin", spec="235/55 R19")

    async def ids(q):
        return {p["category"] for p in (await web.get(f"/api/vehicles/{VIN}/parts", params={"q": q})).json()}

    assert await ids("castrol") == {"engine_oil"}
    assert await ids("hu7008") == {"oil_filter"}
    assert await ids("235/55") == {"tyres"}
    assert await ids("neumáticos") == {"tyres"}                  # category label
    assert await ids("delantero") == {"tyres"}                   # position label
    assert await ids("0w-20") == {"engine_oil"}
    assert await ids("zzz") == set()
    assert {p["category"] for p in (await web.get(f"/api/vehicles/{VIN}/parts", params={"category": "oil_filter"})).json()} == {"oil_filter"}


# ───────────────────────── interventions ─────────────────────────
TYRE = {"category": "tyres", "position": "front", "name": "Michelin Primacy 4", "brand": "Michelin", "spec": "235/55 R19", "quantity": 2,
        "attrs": {"season": "summer", "dot": "1224"}}


async def test_an_event_keeps_its_parts_and_updates_the_car_sheet(web) -> None:
    r = await web.post(f"/api/vehicles/{VIN}/maintenance/events", json={
        "title": "Cambio de neumáticos delanteros", "category": "Neumáticos", "cost": 280, "service_date": "2026-09-10T00:00:00",
        "odometer": 14800, "parts": [TYRE]})
    assert r.status_code == 200, r.text
    log_id = r.json()["log_id"]
    logs = (await web.get(f"/api/vehicles/{VIN}/maintenance/logs")).json()
    assert logs[0]["parts_used"][0]["brand"] == "Michelin" and logs[0]["parts_used"][0]["attrs"]["dot"] == "1224"
    (part,) = (await web.get(f"/api/vehicles/{VIN}/parts")).json()
    assert part["category"] == "tyres" and part["installed_at"].startswith("2026-09-10") and part["installed_odometer"] == 14800
    assert part["source_log_id"] == log_id
    assert part["attrs"] == {"season": "summer"}                                              # the DOT stays in the intervention


async def test_the_sheet_is_not_touched_when_update_parts_is_false(web) -> None:
    r = await web.post(f"/api/vehicles/{VIN}/maintenance/events", json={"title": "Tyres", "parts": [TYRE], "update_parts": False})
    assert r.status_code == 200
    assert (await web.get(f"/api/vehicles/{VIN}/parts")).json() == []
    assert (await web.get(f"/api/vehicles/{VIN}/maintenance/logs")).json()[0]["parts_used"][0]["category"] == "tyres"   # history keeps them


async def test_a_new_intervention_replaces_the_part_on_the_sheet_but_not_in_the_history(web) -> None:
    for brand, date in (("Michelin", "2024-05-01"), ("Pirelli", "2026-09-10")):
        await web.post(f"/api/vehicles/{VIN}/maintenance/events", json={"title": "Tyres", "service_date": f"{date}T00:00:00", "parts": [{**TYRE, "brand": brand}]})
    (part,) = (await web.get(f"/api/vehicles/{VIN}/parts")).json()
    assert part["brand"] == "Pirelli"
    brands = {p["parts_used"][0]["brand"] for p in (await web.get(f"/api/vehicles/{VIN}/maintenance/logs")).json()}
    assert brands == {"Michelin", "Pirelli"}


async def test_bad_parts_reject_the_whole_intervention(web) -> None:
    r = await web.post(f"/api/vehicles/{VIN}/maintenance/events", json={"title": "Tyres", "parts": [{**TYRE, "attrs": {"dot": "xx"}}]})
    assert r.status_code == 422
    assert (await web.get(f"/api/vehicles/{VIN}/maintenance/logs")).json() == []


async def test_official_revision_and_custom_tasks_accept_parts(web) -> None:
    oil = {"category": "engine_oil", "name": "Aceite 0W-20", "brand": "Castrol", "spec": "0W-20", "quantity": 4.2}
    r = await web.post(f"/api/vehicles/{VIN}/maintenance/official/complete", json={"step_km": 15000, "cost": 200, "parts": [oil, {"category": "oil_filter", "reference": "HU7008Z"}]})
    assert r.status_code == 200, r.text
    assert {p["category"] for p in (await web.get(f"/api/vehicles/{VIN}/parts")).json()} == {"engine_oil", "oil_filter"}
    item = (await web.post(f"/api/vehicles/{VIN}/maintenance", json={"name": "Brake service", "interval_km": 20000})).json()
    r = await web.post(f"/api/maintenance/{item['id']}/complete", json={"cost": 90, "parts": [{"category": "brake_pads", "position": "rear", "reference": "R9"}]})
    assert r.status_code == 200, r.text
    assert any(p["category"] == "brake_pads" and p["position"] == "rear" for p in (await web.get(f"/api/vehicles/{VIN}/parts")).json())


async def test_parts_routes_require_a_session(db) -> None:
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://testserver") as c:
        for url in (f"/api/vehicles/{VIN}/parts", "/api/part-categories"):
            assert (await c.get(url)).status_code == 401


async def test_an_intervention_keeps_the_maker_reference_but_never_links_to_the_catalogue(web) -> None:
    oil = {"category": "engine_oil", "brand": "Castrol", "reference": "Edge 0W-20", "oem_reference": "1012345678", "catalog_id": 1}
    r = await web.post(f"/api/vehicles/{VIN}/maintenance/events", json={"title": "Aceite", "parts": [oil]})
    assert r.status_code == 200, r.text
    logs = (await web.get(f"/api/vehicles/{VIN}/maintenance/logs")).json()
    assert logs[0]["parts_used"][0]["oem_reference"] == "1012345678" and "catalog_id" not in logs[0]["parts_used"][0]
    (part,) = (await web.get(f"/api/vehicles/{VIN}/parts")).json()
    assert part["oem_reference"] == "1012345678" and part["catalog_id"] is None


async def test_replacing_a_part_with_a_different_one_drops_its_catalogue_link(web) -> None:
    from app.db.models import VehiclePart
    oil = {"category": "engine_oil", "brand": "Repsol", "reference": "RP-0W20"}
    pid = (await web.post(f"/api/vehicles/{VIN}/parts", json=oil)).json()["id"]
    async with async_session_factory() as s:
        (await s.get(VehiclePart, pid)).catalog_id = 77                       # as if it had been proposed
        await s.commit()
    same = (await web.post(f"/api/vehicles/{VIN}/parts", json={**oil, "brand": "repsol", "quantity": 4})).json()
    assert same["id"] == pid and same["catalog_id"] == 77                     # the same part, more data: still linked
    other = (await web.post(f"/api/vehicles/{VIN}/parts", json={"category": "engine_oil", "brand": "Castrol", "reference": "Edge"})).json()
    assert other["id"] == pid and other["catalog_id"] is None                 # another oil replaces it: the link goes
