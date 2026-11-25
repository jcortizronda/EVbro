"""Charges written down by the user (Carga → «Añadir carga» and «Importar»): the ones from before EVbro, or one it missed.
Only the kWh are required; they never feed the figures that need what the car measured."""
from __future__ import annotations

import datetime
import io
import json

import httpx
import pytest
from openpyxl import Workbook, load_workbook
from sqlalchemy import select

from app.db.models import AppUser, ChargingSession, DeviceAccess, Geofence, Vehicle
from app.db.session import async_session_factory
from app.main import app
from app.security import auth, passwords
from app.services import charge_import

VIN, OTHER = "LSJA0000000000071", "LSJA0000000000072"
JOINED = datetime.datetime(2026, 10, 10, 9, 0)


class _Req:
    client = type("C", (), {"host": "127.0.0.1"})()
    headers = {"user-agent": "tests"}


@pytest.fixture
async def world(db, data_dir):
    """juan (manager, owns the car and two geofences), ana (member, may edit), luis (member, may only look), otro (another manager)."""
    h = passwords.hash_password("una-contraseña-larga-1")
    clients = {}
    async with async_session_factory() as s:
        s.add_all([AppUser(id=1, role="manager", username="juan", password_hash=h), AppUser(id=2, role="member", username="ana", parent_id=1, password_hash=h),
                   AppUser(id=3, role="member", username="luis", parent_id=1, password_hash=h), AppUser(id=4, role="manager", username="otro", password_hash=h)])
        s.add_all([Vehicle(owner_id=1, vin=VIN, name="Ebro", created_at=JOINED), Vehicle(owner_id=4, vin=OTHER, name="Otro", created_at=JOINED)])
        await s.flush()
        s.add_all([Geofence(id=1, owner_id=1, name="Casa", latitude=40.0, longitude=-3.0, cost_mode="fixed", cost_per_kwh=0.135),
                   Geofence(id=2, owner_id=1, name="Trabajo", latitude=40.1, longitude=-3.1, cost_mode="free"),
                   Geofence(id=3, owner_id=4, name="Ajena", latitude=41.0, longitude=-4.0),
                   DeviceAccess(user_id=2, vin=VIN, level="edit", granted_by=1), DeviceAccess(user_id=3, vin=VIN, level="view", granted_by=1),
                   # a charge the car reported, on the 5th of November
                   ChargingSession(id=1, vin=VIN, start_time=datetime.datetime(2026, 11, 5, 22, 0), end_time=datetime.datetime(2026, 11, 6, 2, 0),
                                   kwh_added=14.0, start_battery_pct=20, end_battery_pct=90, duration_seconds=14400)])
        await s.commit()
        for uid in (1, 2, 3, 4):
            token = await auth.create_session(s, await s.get(AppUser, uid), _Req())
            c = httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://testserver")
            c.cookies.set(auth.COOKIE_NAME, token)
            clients[uid] = c
    yield clients
    for c in clients.values():
        await c.aclose()


async def charges() -> list:
    async with async_session_factory() as s:
        return list((await s.execute(select(ChargingSession).where(ChargingSession.vin == VIN).order_by(ChargingSession.id))).scalars())


# ───────────────────────── one by one ─────────────────────────
async def test_only_the_kwh_are_needed_and_the_rest_is_kept_when_given(world) -> None:
    juan = world[1]
    r = await juan.post(f"/api/vehicles/{VIN}/charges", json={"kwh": 22.4, "day": "2026-09-19", "hour": "18:30", "total_cost": 11.2,
                                                           "place": "  Cargador rápido   de la autovía ", "charge_type": "dc", "battery_gain_pct": 90})
    assert r.status_code == 200, r.text
    full = r.json()
    assert (full["day"], full["hour"], full["kwh"], full["total_cost"], full["price_per_kwh"], full["place"]) == (
        "2026-09-19", "18:30", 22.4, 11.2, 0.5, "Cargador rápido de la autovía")
    r = await juan.post(f"/api/vehicles/{VIN}/charges", json={"kwh": 9.6})
    assert r.status_code == 200 and r.json()["date_unknown"] is True and r.json()["day"] is None and r.json()["place"] is None
    _detected, dated, undated = await charges()
    assert dated.manual and dated.start_time == datetime.datetime(2026, 9, 19, 16, 30) and dated.end_time == dated.start_time     # Madrid, summer
    assert (dated.charge_type, dated.battery_gain_pct, dated.cost_mode) == ("dc", 90.0, "manual")
    # the typed kWh are the charger's; what reached the battery is estimated from the % (90 % of 19.27 kWh)
    assert (dated.kwh_used, dated.kwh_added) == (22.4, 17.34)
    # without a day it is filed the day before the car joined EVbro, at midday, and says so
    assert undated.date_unknown and undated.start_time == datetime.datetime(2026, 10, 9, 10, 0) and undated.cost_mode is None
    # both are in the history and in the totals, labelled
    listed = (await juan.get(f"/api/vehicles/{VIN}/charges")).json()
    assert [(c["manual"], c["date_unknown"], c["battery_gain_pct"]) for c in listed] == [(False, False, None), (True, True, None), (True, False, 90.0)]
    # the totals add each charge with the charger's kWh when known, otherwise what the car measured: an estimate of what was paid for
    assert (await juan.get(f"/api/vehicles/{VIN}/charges/summary")).json()["kwh_added"] == round(14.0 + 22.4 + 9.6, 1)


async def test_the_battery_side_and_the_efficiency_come_only_from_the_percentage(world) -> None:
    juan = world[1]
    cid = (await juan.post(f"/api/vehicles/{VIN}/charges", json={"kwh": 20, "battery_gain_pct": 90, "total_cost": 4})).json()["id"]
    detail = (await juan.get(f"/api/charges/{cid}")).json()["charge"]
    assert (detail["kwh_used"], detail["kwh_added"], detail["efficiency_pct"]) == (20.0, 17.34, 86.7)
    assert detail["effective_cost_per_kwh"] == 0.2                          # what was paid, over what the charger delivered
    assert (await juan.get(f"/api/charges/{cid}/manual")).json()["kwh"] == 20.0          # the form gets back what was typed
    # a % no charge could give (30 kWh into a 19 kWh battery at 10 %): nothing is estimated, the charger's figure stays
    for gain in (10, 100):
        r = (await juan.put(f"/api/charges/{cid}", json={"kwh": 30 if gain == 10 else 12, "battery_gain_pct": gain})).json()
        c = (await juan.get(f"/api/charges/{cid}")).json()["charge"]
        assert c["kwh_added"] == c["kwh_used"] and c["efficiency_pct"] is None and r["battery_gain_pct"] == gain
    # no % at all: no estimate either
    await juan.put(f"/api/charges/{cid}", json={"kwh": 15})
    c = (await juan.get(f"/api/charges/{cid}")).json()["charge"]
    assert (c["kwh_added"], c["kwh_used"]) == (15.0, 15.0)


async def test_a_geofence_places_the_charge_and_another_managers_is_refused(world) -> None:
    juan = world[1]
    r = await juan.post(f"/api/vehicles/{VIN}/charges", json={"kwh": 10, "geofence_id": 2, "free": True, "price_per_kwh": 0.3})
    assert r.status_code == 200 and r.json()["free"] is True and r.json()["geofence_id"] == 2 and r.json()["price_per_kwh"] is None
    charge = (await charges())[-1]
    assert (charge.address, charge.latitude, charge.cost_mode, charge.charge_cost) == ("Trabajo", 40.1, "free", 0.0)
    assert (await juan.post(f"/api/vehicles/{VIN}/charges", json={"kwh": 10, "geofence_id": 3})).status_code == 422       # not his
    places = (await juan.get(f"/api/vehicles/{VIN}/charges/places")).json()
    assert [(g["name"], g["price_per_kwh"], g["free"]) for g in places["geofences"]] == [("Casa", 0.135, False), ("Trabajo", None, True)]
    await juan.post(f"/api/vehicles/{VIN}/charges", json={"kwh": 5, "place": "Centro comercial"})
    await juan.post(f"/api/vehicles/{VIN}/charges", json={"kwh": 6, "place": "Centro comercial"})
    assert (await juan.get(f"/api/vehicles/{VIN}/charges/places")).json()["places"] == [{"name": "Centro comercial"}]


@pytest.mark.parametrize("body", [{}, {"kwh": 0}, {"kwh": 600}, {"total_cost": 3}, {"price_per_kwh": 0.2}, {"kwh": 10, "battery_gain_pct": 140},
                                  {"kwh": 10, "charge_type": "x"}, {"kwh": 10, "day": "2099-01-01"}, {"kwh": 10, "day": "31/02/2026"}])
async def test_what_cannot_be_a_charge_is_refused(world, body) -> None:
    assert (await world[1].post(f"/api/vehicles/{VIN}/charges", json=body)).status_code == 422
    assert len(await charges()) == 1


async def test_a_manual_charge_can_be_corrected_and_deleted_but_one_from_the_car_cannot(world) -> None:
    juan = world[1]
    cid = (await juan.post(f"/api/vehicles/{VIN}/charges", json={"kwh": 10, "day": "2026-08-01", "price_per_kwh": 0.2})).json()["id"]
    assert (await juan.get(f"/api/charges/{cid}/manual")).json()["price_per_kwh"] == 0.2
    r = await juan.put(f"/api/charges/{cid}", json={"kwh": 12.5, "geofence_id": 1, "total_cost": 2.0})
    assert r.status_code == 200 and (r.json()["kwh"], r.json()["day"], r.json()["geofence_id"], r.json()["total_cost"]) == (12.5, None, 1, 2.0)
    for call in (juan.put("/api/charges/1", json={"kwh": 1}), juan.delete("/api/charges/1"), juan.get("/api/charges/1/manual")):
        assert (await call).status_code == 409                                  # the car's own: not rewritten by hand
    assert (await juan.delete(f"/api/charges/{cid}")).status_code == 200
    assert [c.id for c in await charges()] == [1]


async def test_a_member_who_may_edit_adds_charges_and_one_who_may_only_look_does_not(world) -> None:
    assert (await world[2].post(f"/api/vehicles/{VIN}/charges", json={"kwh": 8})).status_code == 200
    assert (await world[3].post(f"/api/vehicles/{VIN}/charges", json={"kwh": 8})).status_code == 403
    assert (await world[3].get(f"/api/vehicles/{VIN}/charges/places")).status_code == 200
    cid = (await charges())[-1].id
    assert (await world[3].delete(f"/api/charges/{cid}")).status_code == 403
    for stranger in (world[4],):                                                # another manager: the car does not exist for it
        assert (await stranger.post(f"/api/vehicles/{VIN}/charges", json={"kwh": 8})).status_code in (403, 404)
        assert (await stranger.delete(f"/api/charges/{cid}")).status_code in (403, 404)


@pytest.mark.parametrize("given, expected", [
    ({"kwh": 20, "price_per_kwh": 0.25}, (20.0, 0.25, 5.0)),         # kWh and price → total
    ({"kwh": 20, "total_cost": 5}, (20.0, 0.25, 5.0)),               # kWh and total → price
    ({"price_per_kwh": 0.25, "total_cost": 5}, (20.0, 0.25, 5.0)),   # price and total → the charger's kWh
])
async def test_any_two_of_kwh_price_and_total_give_the_third(world, given, expected) -> None:
    cid = (await world[1].post(f"/api/vehicles/{VIN}/charges", json=given)).json()["id"]
    c = (await world[1].get(f"/api/charges/{cid}")).json()["charge"]
    assert (c["kwh_used"], c["effective_cost_per_kwh"], c["charge_cost"]) == expected
    assert c["kwh_added"] == c["kwh_used"] and c["efficiency_pct"] is None          # no % given: no battery side, no efficiency


async def test_the_battery_side_comes_from_the_percentage_and_the_price_applies_to_it(world) -> None:
    juan = world[1]
    r = await juan.post(f"/api/vehicles/{VIN}/charges", json={"battery_gain_pct": 70, "price_per_kwh": 0.2})
    assert r.status_code == 200, r.text
    assert (r.json()["kwh"], r.json()["battery_kwh"], r.json()["price_per_kwh"]) == (None, 13.49, 0.2)       # 70 % of 19.27 kWh
    c = (await juan.get(f"/api/charges/{r.json()['id']}")).json()["charge"]
    assert (c["kwh_used"], c["kwh_added"], c["charge_cost"], c["efficiency_pct"]) == (None, 13.49, 2.7, None)
    # the % alone: the energy, without a cost
    c = (await juan.post(f"/api/vehicles/{VIN}/charges", json={"battery_gain_pct": 50})).json()
    assert (c["kwh"], c["battery_kwh"], c["total_cost"], c["price_per_kwh"]) == (None, 9.63, None, None)        # 50 % of 19.27, rounded
    # with the charger's kWh too, the cost goes on them and the % gives the efficiency instead
    c = (await juan.post(f"/api/vehicles/{VIN}/charges", json={"kwh": 16, "battery_gain_pct": 70, "price_per_kwh": 0.2})).json()
    detail = (await juan.get(f"/api/charges/{c['id']}")).json()["charge"]
    assert (detail["kwh_used"], detail["kwh_added"], detail["charge_cost"], detail["efficiency_pct"]) == (16.0, 13.49, 3.2, 84.3)


async def test_a_price_or_a_total_of_zero_is_a_free_charge(world) -> None:
    for body in ({"kwh": 12, "price_per_kwh": 0}, {"kwh": 12, "total_cost": 0}):
        c = (await world[1].post(f"/api/vehicles/{VIN}/charges", json=body)).json()
        assert c["free"] is True and (await world[1].get(f"/api/charges/{c['id']}")).json()["charge"]["charge_cost"] == 0


async def test_the_meter_kwh_of_a_detected_charge_reprice_it_and_count_in_the_totals(world) -> None:
    """The same as a manual charge on the charger's side: the kWh of the meter give the cost (whatever the price came from)
    and the efficiency; the totals count them instead of what the car measured."""
    async with async_session_factory() as s:
        charge = await s.get(ChargingSession, 1)
        charge.geofence_id, charge.cost_mode, charge.effective_cost_per_kwh, charge.charge_cost = 1, "fixed", 0.14, 1.96     # 14 kWh × 0.14
        await s.commit()
    r = await world[1].put("/api/charges/1/cost", json={"kwh_meter": 16})
    assert r.status_code == 200, r.text
    assert (r.json()["charge_cost"], r.json()["effective_cost_per_kwh"], r.json()["efficiency_pct"]) == (2.24, 0.14, 87.5)
    assert (await world[1].get(f"/api/vehicles/{VIN}/charges/summary")).json()["kwh_added"] == 16.0


# ───────────────────────── from a spreadsheet ─────────────────────────
def xlsx(rows) -> bytes:
    book = Workbook()
    for row in rows:
        book.active.append(row)
    buffer = io.BytesIO()
    book.save(buffer)
    return buffer.getvalue()


BOOK = [
    ["Día", "Energía (kWh)", "Importe", "Sitio", "Hora de inicio"],
    [datetime.datetime(2026, 9, 19), 22.4, 11.2, "Cargador rápido", "18:30"],
    ["08/09/2026", "18,8 kWh", "2,54 €", "casa", None],
    [None, 9.6, 0, "Trabajo", None],                               # no day; free
    ["05/11/2026", 14.2, 1.9, "Casa", None],                       # the car already has this one
    ["31/02/2026", 12, 1.62, "Casa", None],                        # no such day
    ["01/07/2026", None, 3, "Casa", None],                         # no kWh
    ["08/09/2026", "18,8", "2,54", "Casa", None],                  # the same as row 3
]


async def upload(client, path, data: bytes, name="cargas.xlsx", mapping=None):
    extra = {"mapping": json.dumps(mapping)} if mapping is not None else {}
    return await client.post(f"/api/vehicles/{VIN}/charges/import/{path}".rstrip("/"), files={"file": (name, data, "application/octet-stream")}, data=extra)


async def test_the_review_says_how_the_columns_were_understood_and_what_each_row_is(world) -> None:
    r = await upload(world[1], "preview", xlsx(BOOK))
    assert r.status_code == 200, r.text
    d = r.json()
    assert d["columns"] == BOOK[0] and d["missing"] == []
    assert d["mapping"] == {"date": 0, "kwh": 1, "total": 2, "place": 3, "time": 4, "price": None, "battery": None, "type": None}
    assert (d["total"], d["counts"]) == (7, {"ok": 3, "duplicate": 2, "error": 2})
    by_line = {row["line"]: row for row in d["rows"]}
    assert [row["status"] for row in d["rows"]][:2] == ["error", "error"]                    # what will not come in is listed first
    assert (by_line[2]["day"], by_line[2]["hour"], by_line[2]["kwh"], by_line[2]["total_cost"], by_line[2]["place"]) == ("2026-09-19", "18:30", 22.4, 11.2, "Cargador rápido")
    assert (by_line[3]["kwh"], by_line[3]["total_cost"], by_line[3]["place"]) == (18.8, 2.54, "Casa")          # decimal comma, units, the geofence by name
    assert (by_line[4]["day"], by_line[4]["free"], by_line[4]["place"]) == (None, True, "Trabajo")
    assert (by_line[5]["problem"], by_line[6]["problem"], by_line[7]["problem"], by_line[8]["problem"]) == ("already", "bad_date", "no_energy", "repeated")
    assert len(await charges()) == 1                                                           # looking stores nothing


async def test_importing_stores_the_ready_rows_as_one_batch_that_can_be_undone(world) -> None:
    juan = world[1]
    r = await upload(juan, "", xlsx(BOOK))
    assert r.status_code == 200, r.text
    result = r.json()
    assert (result["imported"], result["kwh"], result["cost"], result["from"], result["to"], result["undated"]) == (3, 50.8, 13.74, "2026-09-08", "2026-09-19", 1)
    assert result["skipped"] == {"duplicate": 2, "error": 2}
    stored = await charges()
    assert len(stored) == 4 and {c.import_batch for c in stored[1:]} == {result["batch"]} and all(c.manual for c in stored[1:])
    assert sorted((c.address, c.geofence_id, c.cost_mode) for c in stored[1:]) == [("Cargador rápido", None, "manual"), ("Casa", 1, "manual"), ("Trabajo", 2, "free")]
    # the same file again brings nothing new (the dated rows are already there)
    again = (await upload(juan, "preview", xlsx(BOOK))).json()
    assert again["counts"]["ok"] == 1                                            # only the one without a day, which cannot be told apart
    # undoing takes the batch away and nothing else
    assert (await juan.delete(f"/api/vehicles/{VIN}/charges/import", params={"batch": result["batch"]})).json()["removed"] == 3
    assert [c.id for c in await charges()] == [1]
    assert (await juan.delete(f"/api/vehicles/{VIN}/charges/import", params={"batch": result["batch"]})).status_code == 404
    assert (await juan.delete(f"/api/vehicles/{VIN}/charges/import", params={"batch": "no-es-un-lote"})).status_code == 404


async def test_a_csv_with_other_titles_and_a_corrected_mapping(world) -> None:
    text = "Date;Energy;Price per kWh;Station;Type\n2026-07-01;12,5;0,45;Supercharger;DC fast\n02/07/2026;7.2;0.2;Hotel;\n"
    d = (await upload(world[1], "preview", text.encode("utf-8"), name="charges.csv")).json()
    assert d["mapping"] == {"date": 0, "kwh": 1, "price": 2, "place": 3, "type": 4, "time": None, "total": None, "battery": None}
    assert [(r["day"], r["kwh"], r["price_per_kwh"], r["charge_type"]) for r in sorted(d["rows"], key=lambda r: r["line"])] == [
        ("2026-07-01", 12.5, 0.45, "dc"), ("2026-07-02", 7.2, 0.2, "ac")]
    # the user says the kWh are in another column: nothing is a charge any more, and that cannot be imported
    wrong = {"date": 0, "kwh": 3, "price": None, "place": None, "type": None, "time": None, "total": None, "battery": None}
    d = (await upload(world[1], "preview", text.encode("utf-8"), name="charges.csv", mapping=wrong)).json()
    assert d["counts"] == {"ok": 0, "duplicate": 0, "error": 2}
    assert (await upload(world[1], "", text.encode("utf-8"), name="charges.csv", mapping=wrong)).status_code == 422
    assert (await upload(world[1], "", text.encode("utf-8"), name="charges.csv", mapping={"date": 0})).status_code == 422     # no kWh column chosen
    assert len(await charges()) == 1


@pytest.mark.parametrize("name, data", [("vacio.csv", b""), ("solo-titulos.csv", b"Fecha;kWh\n"), ("viejo.xls", b"\xd0\xcf\x11\xe0 old excel"),
                                        ("roto.xlsx", b"PK\x03\x04 not really a book"), ("grande.csv", b"Fecha;kWh\n" + b"01/01/2026;1\n" * 1001)])
async def test_a_file_that_is_not_a_table_of_charges_is_refused_with_a_reason(world, name, data) -> None:
    r = await upload(world[1], "preview", data, name=name)
    assert r.status_code == 422 and isinstance(r.json()["detail"], str) and len(await charges()) == 1


async def test_the_example_book_is_understood_by_the_import_itself(world) -> None:
    r = await world[1].get(f"/api/vehicles/{VIN}/charges/import/template")
    assert r.status_code == 200 and "spreadsheetml" in r.headers["content-type"] and "evbro-cargas.xlsx" in r.headers["content-disposition"]
    assert [c.value for c in load_workbook(io.BytesIO(r.content)).active[1]] == charge_import.TEMPLATE_TITLES
    d = (await upload(world[1], "preview", r.content)).json()
    assert all(v is not None for v in d["mapping"].values()) and d["counts"] == {"ok": 3, "duplicate": 0, "error": 0}
    assert (await world[3].get(f"/api/vehicles/{VIN}/charges/import/template")).status_code == 200          # looking is allowed
    assert (await upload(world[3], "preview", r.content)).status_code == 403                                 # importing is editing


def test_the_values_of_a_sheet_are_read_however_they_were_typed() -> None:
    assert [charge_import.number(v) for v in ("22,4", "22.4 kWh", "11,20 €", 7, "1.234,5", "", None, "n/d")] == [22.4, 22.4, 11.2, 7.0, 1234.5, None, None, None]
    assert charge_import.day_and_hour("19/09/2026 18:30") == (datetime.date(2026, 9, 19), datetime.time(18, 30), True)
    assert charge_import.day_and_hour("2026-09-19T07:05:00")[:2] == (datetime.date(2026, 9, 19), datetime.time(7, 5))
    assert charge_import.day_and_hour("19-9-26")[0] == datetime.date(2026, 9, 19) and charge_import.day_and_hour("ayer")[2] is False
    assert [charge_import.kind_of(v) for v in ("DC", "Rápida", "CCS", "AC", "", None, "Tipo 2")] == ["dc", "dc", "dc", "ac", "ac", "ac", "ac"]
    assert charge_import.guess_mapping(["Precio por kWh", "kWh", "Total (€)"]) == {
        "price": 0, "kwh": 1, "total": 2, "date": None, "time": None, "place": None, "battery": None, "type": None}
