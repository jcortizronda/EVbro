"""services/fuse_boxes.py and GET /api/vehicles/{vin}/fuses: the fuse boxes of the models EVbro knows, and the honesty of the table
(it says it has not been checked against a real car, and marks the positions where its source contradicts itself)."""
from __future__ import annotations

import httpx
import pytest

from app.db.models import AppUser, Vehicle
from app.db.session import async_session_factory
from app.main import app
from app.security import auth, passwords
from app.services import fuse_boxes as fb

VIN, OTHER, FOREIGN = "LSJA0000000000101", "LSJA0000000000102", "LSJA0000000000103"
RATINGS = {5, 7.5, 10, 15, 20, 25, 30, 40, 50, 60, 80, 100, 175}


class _Req:
    client = type("C", (), {"host": "127.0.0.1"})()
    headers = {"user-agent": "t"}


def test_every_position_is_there_once_with_a_real_rating_and_a_text_in_the_three_languages() -> None:
    for model, data in fb.BOXES.items():
        assert data["verified"] is False and all(data["source"])                # nothing here has been checked on a real Ebro
        for box in data["boxes"]:
            ids = [row[0] for row in box["fuses"]] + [r[0] for r in box["relays"]]
            assert len(ids) == len(set(ids)), (model, box["key"])
            assert all(box["name"]) and all(box["where"])
            for ident, amps, text, doubt in box["fuses"]:
                assert amps is None or amps in RATINGS, ident
                assert text is None or (len(text) == 3 and all(text)), ident
                assert doubt is None or (len(doubt) == 3 and all(doubt)), ident
            assert all(len(text) == 3 and all(text) for _ident, text in box["relays"])


def test_the_s900_has_its_two_boxes_and_what_makes_it_a_plug_in_hybrid() -> None:
    data = fb.for_model("Ebro s900 phev")                                       # the model as the cloud names it
    cabin, engine = data["boxes"]
    assert (data["model"], cabin["key"], engine["key"]) == ("S900 PHEV", "cabin", "engine")
    by_id = {i["id"]: i for box in data["boxes"] for i in box["items"]}
    assert len(by_id) == 61 + 84
    assert by_id["EF49"]["amps"] == 175 and "DC-DC" in by_id["EF49"]["text"]
    assert "alta tensión" in by_id["EF23"]["text"] and "BMS" in by_id["EF18"]["text"]
    assert by_id["RF42"] == {"id": "RF42", "amps": 40, "kind": "fuse", "text": "Ventilador del habitáculo", "doubt": None}
    assert by_id["RF05"]["kind"] == "spare" and by_id["RF03"]["kind"] == "empty" and by_id["ERLY12"]["kind"] == "relay"
    # where the source's table and drawing disagree, the position says so; a rating in doubt is not given at all
    seen = {"RSB11", "RF07", "RF30", "RF36", "RF40", "RF43"}             # what a real S900 was found to carry instead (its owner, 11-2026)
    assert {i for i, item in by_id.items() if item["doubt"]} == {"EF05", "EF06", "EF11", "EF12", "EF40", "RF16"} | seen
    assert all("Ebro S900 real" in by_id[i]["doubt"] and "un solo coche" in by_id[i]["doubt"] for i in seen)
    assert [by_id[i]["kind"] for i in ("RSB11", "RF30", "RF43")] == ["empty"] * 3 and all(by_id[i]["amps"] is None for i in ("RSB11", "RF30", "RF43"))
    assert [(by_id[i]["amps"], by_id[i]["text"]) for i in ("RF07", "RF36", "RF40")] == [(10, "Función sin confirmar")] * 3
    assert "reserva de 20 A" in by_id["RF07"]["doubt"] and "deja esta posición vacía" in by_id["RF40"]["doubt"]
    assert by_id["EF12"]["amps"] is None and "10 A" in by_id["EF12"]["doubt"] and "20 A" in by_id["EF12"]["doubt"]


def test_the_cabin_box_places_every_fuse_it_has_once_and_inside_the_box() -> None:
    cabin, engine = fb.for_model("S900 PHEV")["boxes"]
    layout = cabin["layout"]
    items = {i["id"]: i for i in cabin["items"]}
    assert set(layout["cells"]) <= set(items)
    # every position with a fuse (or a spare one) has its place; only the positions without a slot and the relays have none
    assert {i for i, item in items.items() if i not in layout["cells"]} == {"RF03", "RF23", "RF24", "RF25", "RRLY01", "RRLY02", "RRLY03"}
    cells = list(layout["cells"].items())
    for ident, (x, y, w, h) in cells:
        assert 0 <= x and 0 <= y and x + w <= 100 and y + h <= 100 and w > 1 and h > 1, ident
    for n, (a, (x, y, w, h)) in enumerate(cells):
        for b, (x2, y2, w2, h2) in cells[n + 1:]:
            assert not (x < x2 + w2 and x2 < x + w and y < y2 + h2 and y2 < y + h), (a, b)      # no two fuses on top of each other
    assert layout["ratio"] > 1 and "9AR" in layout["view"] and {m["label"] for m in layout["marks"]} >= {"C4", "C2", "9AR"}
    # a few neighbours, as in the box: the blower's 40 A sits above the two columns of small fuses, RSB09 beside RSB04
    assert layout["cells"]["RF42"][1] < layout["cells"]["RF27"][1] < layout["cells"]["RF31"][1]
    assert layout["cells"]["RSB09"][0] < layout["cells"]["RSB04"][0] and layout["cells"]["RSB09"][1] == layout["cells"]["RSB04"][1]
    assert items["RF16"]["amps"] == 15 and items["RF16"]["kind"] == "spare" and items["RF16"]["doubt"]


def test_the_engine_bay_box_places_every_fuse_and_relay_as_the_manuals_drawing_does() -> None:
    engine = fb.for_model("S900 PHEV")["boxes"][1]
    layout = engine["layout"]
    assert set(layout["cells"]) == {i["id"] for i in engine["items"]} and len(layout["cells"]) == 84       # relays are on this face too
    cells = list(layout["cells"].items())
    for ident, (x, y, w, h) in cells:
        assert 0 <= x and 0 <= y and x + w <= 100 and y + h <= 100 and w > 1 and h > 1, ident
    for n, (a, (x, y, w, h)) in enumerate(cells):
        for b, (x2, y2, w2, h2) in cells[n + 1:]:
            assert not (x < x2 + w2 and x2 < x + w and y < y2 + h2 and y2 < y + h), (a, b)
    at = layout["cells"]
    assert layout["ratio"] < 1 and "ERLY01" in layout["view"] and "B+" in {m["label"] for m in layout["marks"]}
    # two columns of eighteen small fuses side by side (EF19-36 left of EF01-18), the big relays beyond them, the heavy fuses opposite
    assert at["EF54"][0] < at["EF19"][0] < at["EF01"][0] < at["ERLY01"][0]
    assert all(at[f"EF{n:02d}"][1] < at[f"EF{n + 1:02d}"][1] for n in list(range(1, 18)) + list(range(19, 36)))
    assert at["EF19"][1] == at["EF01"][1] and at["EF36"][1] == at["EF18"][1]
    assert next(i for i in engine["items"] if i["id"] == "ERLY09")["text"] == "Bomba de aceite eléctrica (EOP)"


def test_other_languages_and_models_nothing_is_known_about() -> None:
    assert fb.for_model("S900 PHEV", "en")["boxes"][1]["name"] == "Engine bay"
    assert fb.for_model("S900 PHEV", "pt")["boxes"][0]["items"][0]["text"] == "Porta da bagageira elétrica"
    assert fb.for_model("S900 PHEV", "xx")["boxes"][0]["name"] == "Habitáculo"      # an unknown language: Spanish
    assert fb.has("S900 PHEV") and not fb.has("S800 PHEV") and not fb.has(None)
    assert fb.for_model("S700 PHEV") is None and fb.for_model("Otro coche") is None


async def test_the_route_answers_for_the_cars_model_only(db, data_dir) -> None:
    async with async_session_factory() as s:
        user = AppUser(id=1, role="manager", username="juan", vehicle_limit=-1, password_hash=passwords.hash_password("una-contraseña-larga-1"))
        other = AppUser(id=2, role="manager", username="otro", password_hash=passwords.hash_password("una-contraseña-larga-2"))
        s.add_all([user, other, Vehicle(owner_id=1, vin=VIN, name="Grande", brand="Ebro", model="S900 PHEV"),
                   Vehicle(owner_id=1, vin=OTHER, name="Otra marca", brand="Otra", model="S900 PHEV"),
                   Vehicle(owner_id=2, vin=FOREIGN, name="Ajeno", brand="Ebro", model="S900 PHEV")])
        await s.commit()
        token = await auth.create_session(s, user, _Req())
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://testserver") as c:
        assert (await c.get(f"/api/vehicles/{VIN}/fuses")).status_code == 401
        c.cookies.set(auth.COOKIE_NAME, token)
        r = await c.get(f"/api/vehicles/{VIN}/fuses?lang=en")
        assert r.status_code == 200 and r.json()["verified"] is False and r.json()["boxes"][0]["name"] == "Cabin"
        assert (await c.get(f"/api/vehicles/{OTHER}/fuses")).status_code == 404            # another make with the same model name
        assert (await c.get(f"/api/vehicles/{FOREIGN}/fuses")).status_code == 404          # somebody else's car
        specs = (await c.get("/api/vehicles/specs")).json()
        assert specs["S900 PHEV"]["fuses"] is True and specs["S800 PHEV"]["fuses"] is False


@pytest.mark.parametrize("ident", ["SB03", "SB10"])
def test_the_labels_that_look_like_a_petrol_car_are_kept_as_the_source_gives_them(ident) -> None:
    """The hybrid's own manual labels a «START» and a «DCT» position: they are shown as labelled, not explained away."""
    item = next(i for i in fb.for_model("S900 PHEV")["boxes"][1]["items"] if i["id"] == ident)
    assert item["kind"] == "fuse" and item["amps"] == 30
