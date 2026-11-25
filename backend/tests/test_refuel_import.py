"""Refuels from a spreadsheet (services/refuel_import.py, Repostajes → «Importar»): the same three steps as the charges."""
from __future__ import annotations

import datetime
import io
import json

import httpx
import pytest
from openpyxl import Workbook, load_workbook
from sqlalchemy import select

from app.db.models import AppUser, DeviceAccess, Refuel, Vehicle
from app.db.session import async_session_factory
from app.main import app
from app.security import auth, passwords
from app.services import refuel_import

VIN, OTHER = "LSJA0000000000081", "LSJA0000000000082"


class _Req:
    client = type("C", (), {"host": "127.0.0.1"})()
    headers = {"user-agent": "tests"}


@pytest.fixture
async def world(db, data_dir):
    """juan (manager, owns the car, which detected a refuel on the 5th of November), luis (member, may only look), otro (another manager)."""
    h = passwords.hash_password("una-contraseña-larga-1")
    clients = {}
    async with async_session_factory() as s:
        s.add_all([AppUser(id=1, role="manager", username="juan", password_hash=h), AppUser(id=3, role="member", username="luis", parent_id=1, password_hash=h),
                   AppUser(id=4, role="manager", username="otro", password_hash=h)])
        s.add_all([Vehicle(owner_id=1, vin=VIN, name="Ebro"), Vehicle(owner_id=4, vin=OTHER, name="Otro")])
        await s.flush()
        s.add_all([DeviceAccess(user_id=3, vin=VIN, level="view", granted_by=1),
                   Refuel(id=1, vin=VIN, timestamp=datetime.datetime(2026, 11, 5, 9, 0), liters=36.0, fuel_before_pct=20, fuel_after_pct=95, detected=True)])
        await s.commit()
        for uid in (1, 3, 4):
            token = await auth.create_session(s, await s.get(AppUser, uid), _Req())
            c = httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://testserver")
            c.cookies.set(auth.COOKIE_NAME, token)
            clients[uid] = c
    yield clients
    for c in clients.values():
        await c.aclose()


async def refuels() -> list:
    async with async_session_factory() as s:
        return list((await s.execute(select(Refuel).where(Refuel.vin == VIN).order_by(Refuel.id))).scalars())


def xlsx(rows) -> bytes:
    book = Workbook()
    for row in rows:
        book.active.append(row)
    buffer = io.BytesIO()
    book.save(buffer)
    return buffer.getvalue()


async def upload(client, path, data: bytes, name="repostajes.xlsx", mapping=None, vin=VIN):
    extra = {"mapping": json.dumps(mapping)} if mapping is not None else {}
    return await client.post(f"/api/vehicles/{vin}/refuels/import/{path}".rstrip("/"), files={"file": (name, data, "application/octet-stream")}, data=extra)


BOOK = [
    ["Fecha", "Litros", "Importe", "Precio por litro", "Km", "Estación", "Lleno", "Hora"],
    [datetime.datetime(2026, 9, 19), 38.4, 61.0, None, 12450, "Gasolinera Norte", "Sí", "09:30"],
    ["08/10/2026", None, "52,40 €", "1,619", "13.210", "Gasolinera Sur", "no", None],      # litres from the total and the price
    ["20/10/2026", 15, None, 1.6, None, None, None, None],                                  # «lleno» left empty: not said
    ["05/11/2026", 34.5, 55, None, None, None, "x", None],                                  # the car already has this one (36 l, estimated)
    ["31/02/2026", 30, 48, None, None, None, None, None],                                   # no such day
    [None, 30, 48, None, None, None, None, None],                                           # no day
    ["01/07/2026", None, 48, None, None, None, None, None],                                 # no litres
    ["20/10/2026", "15", None, "1,6", None, None, None, None],                              # the same as row 4
]


async def test_the_review_says_how_the_columns_were_understood_and_what_each_row_is(world) -> None:
    r = await upload(world[1], "preview", xlsx(BOOK))
    assert r.status_code == 200, r.text
    d = r.json()
    assert d["missing"] == []
    assert d["mapping"] == {"date": 0, "liters": 1, "total": 2, "price": 3, "odometer": 4, "station": 5, "full": 6, "time": 7, "notes": None}
    assert (d["total"], d["counts"]) == (8, {"ok": 3, "duplicate": 2, "error": 3})
    by_line = {row["line"]: row for row in d["rows"]}
    assert [row["status"] for row in d["rows"]][:3] == ["error"] * 3                                  # what will not come in is listed first
    assert (by_line[2]["day"], by_line[2]["hour"], by_line[2]["liters"], by_line[2]["price_per_liter"], by_line[2]["odometer"]) == (
        "2026-09-19", "09:30", 38.4, round(61 / 38.4, 3), 12450)
    assert (by_line[3]["liters"], by_line[3]["total_cost"], by_line[3]["full_tank"], by_line[3]["odometer"]) == (round(52.4 / 1.619, 2), 52.4, False, 13210)
    assert (by_line[4]["total_cost"], by_line[4]["full_tank"]) == (24.0, None)
    assert [by_line[n]["problem"] for n in (5, 6, 7, 8, 9)] == ["already", "bad_date", "no_date", "no_liters", "repeated"]
    assert len(await refuels()) == 1                                                                 # looking stores nothing


async def test_importing_stores_the_ready_rows_as_one_batch_that_can_be_undone(world) -> None:
    juan = world[1]
    r = await upload(juan, "", xlsx(BOOK))
    assert r.status_code == 200, r.text
    result = r.json()
    assert (result["imported"], result["from"], result["to"], result["unpriced"]) == (3, "2026-09-19", "2026-10-20", 0)
    assert result["cost"] == round(61 + 52.4 + 24, 2) and result["skipped"] == {"duplicate": 2, "error": 3}
    stored = await refuels()
    assert len(stored) == 4 and {x.import_batch for x in stored[1:]} == {result["batch"]} and not any(x.detected for x in stored[1:])
    assert [(x.station, x.full_tank) for x in stored[1:]] == [("Gasolinera Norte", True), ("Gasolinera Sur", False), (None, None)]
    # the same file again brings nothing new
    assert (await upload(juan, "preview", xlsx(BOOK))).json()["counts"]["ok"] == 0
    # they count in the refuels page like the ones added by hand
    listed = (await juan.get(f"/api/vehicles/{VIN}/refuels")).json()
    assert len(listed) == 4 and sum(1 for x in listed if not x["detected"]) == 3
    # undoing takes the batch away and nothing else (not the detected one)
    assert (await juan.delete(f"/api/vehicles/{VIN}/refuels/import", params={"batch": result["batch"]})).json()["removed"] == 3
    assert [x.id for x in await refuels()] == [1]
    assert (await juan.delete(f"/api/vehicles/{VIN}/refuels/import", params={"batch": result["batch"]})).status_code == 404
    assert (await juan.delete(f"/api/vehicles/{VIN}/refuels/import", params={"batch": "no-es-un-lote"})).status_code == 404


async def test_without_a_full_column_every_refuel_is_a_fill_up_and_the_mapping_can_be_corrected(world) -> None:
    text = "Date;Litres;Total;Station\n2026-07-01;40,2;64,3;North\n02/07/2026;12;19,2;South\n"
    d = (await upload(world[1], "preview", text.encode("utf-8"), name="refuels.csv")).json()
    assert d["mapping"] == {"date": 0, "liters": 1, "total": 2, "station": 3, "price": None, "odometer": None, "full": None, "time": None, "notes": None}
    assert [(r["day"], r["liters"], r["full_tank"]) for r in sorted(d["rows"], key=lambda r: r["line"])] == [("2026-07-01", 40.2, True), ("2026-07-02", 12.0, True)]
    # the user says the litres are in another column: nothing is a refuel any more, and that cannot be imported
    wrong = {"date": 0, "liters": 3}
    d = (await upload(world[1], "preview", text.encode("utf-8"), name="refuels.csv", mapping=wrong)).json()
    assert d["counts"] == {"ok": 0, "duplicate": 0, "error": 2}
    assert (await upload(world[1], "", text.encode("utf-8"), name="refuels.csv", mapping=wrong)).status_code == 422
    assert (await upload(world[1], "", text.encode("utf-8"), name="refuels.csv", mapping={"liters": 1})).status_code == 422    # no date column chosen
    assert len(await refuels()) == 1


@pytest.mark.parametrize("name, data", [("vacio.csv", b""), ("solo-titulos.csv", b"Fecha;Litros\n"), ("viejo.xls", b"\xd0\xcf\x11\xe0 old excel"),
                                        ("grande.csv", b"Fecha;Litros\n" + b"01/01/2026;1\n" * 1001)])
async def test_a_file_that_is_not_a_table_of_refuels_is_refused_with_a_reason(world, name, data) -> None:
    r = await upload(world[1], "preview", data, name=name)
    assert r.status_code == 422 and isinstance(r.json()["detail"], str) and len(await refuels()) == 1
    if name == "grande.csv":
        assert "repostajes" in r.json()["detail"]


async def test_the_example_book_is_understood_by_the_import_itself_and_only_editors_import(world) -> None:
    r = await world[1].get(f"/api/vehicles/{VIN}/refuels/import/template")
    assert r.status_code == 200 and "spreadsheetml" in r.headers["content-type"] and "evbro-repostajes.xlsx" in r.headers["content-disposition"]
    assert [c.value for c in load_workbook(io.BytesIO(r.content)).active[1]] == refuel_import.TEMPLATE_TITLES
    d = (await upload(world[1], "preview", r.content)).json()
    assert all(v is not None for v in d["mapping"].values()) and d["counts"] == {"ok": 3, "duplicate": 0, "error": 0}
    assert (await world[3].get(f"/api/vehicles/{VIN}/refuels/import/template")).status_code == 200          # looking is allowed
    assert (await upload(world[3], "preview", r.content)).status_code == 403                                 # importing is editing
    assert (await upload(world[4], "preview", r.content)).status_code in (403, 404)                          # another manager's car
    assert (await world[4].delete(f"/api/vehicles/{VIN}/refuels/import", params={"batch": "0" * 16})).status_code in (403, 404)


@pytest.mark.parametrize("given, expected", [
    ((40, None, 60), (40, 1.5, 60)), ((40, 1.5, None), (40, 1.5, 60)), ((None, 1.5, 60), (40, 1.5, 60)),
    ((40, 1.4, 60), (40, 1.5, 60)),                                       # the three: the total wins
    ((40, None, None), (40, None, None)), ((None, None, 60), (None, None, 60)),
])
def test_any_two_of_litres_price_and_total_give_the_third(given, expected) -> None:
    d = refuel_import.settle(refuel_import.RefuelData(day=None, hour=None, liters=given[0], price_per_liter=given[1], total_cost=given[2]))
    assert (d.liters, d.price_per_liter, d.total_cost) == expected


def test_the_full_tank_cell_is_read_in_any_language() -> None:
    assert [refuel_import.full_of(v) for v in ("Sí", "si", "X", 1, "yes", "Sim", "lleno", True)] == [True] * 8
    assert [refuel_import.full_of(v) for v in ("No", "n", 0, "não", False)] == [False] * 5
    assert [refuel_import.full_of(v) for v in (None, "", "  ", "quizá")] == [None] * 4
