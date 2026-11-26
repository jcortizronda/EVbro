"""EVbro's maintenance recommendations (services/maintenance_advice.py): when each comes due, what the owner's three answers do,
the routes and the notice the poller sends."""
from __future__ import annotations

import datetime

import httpx
import pytest
from freezegun import freeze_time
from sqlalchemy import select

from app.db.models import (
    AlertState,
    AppUser,
    DeviceAccess,
    Drive,
    MaintenanceItem,
    MaintenancePlan,
    Refuel,
    Vehicle,
    VehiclePart,
)
from app.db.session import async_session_factory
from app.main import app
from app.security import auth, passwords
from app.services import maintenance_advice as ma
from app.services.maintenance_advice import Facts, Part, State
from app.services.notification_service import NotificationService
from app.services.poller import VehiclePoller

VIN, OTHER = "LSJA0000000000071", "LSJA0000000000072"
D = datetime.datetime
DELIVERY = D(2025, 3, 1)
NOW = D(2026, 1, 10)                       # 10 months after the delivery


def by_key(items):
    return {a["key"]: a for a in items}


def facts(**kw) -> Facts:
    return Facts(**{"odometer": 9000.0, "start_date": DELIVERY, **kw})


# ───────────────────────── when each one comes due ─────────────────────────
def test_a_young_car_with_few_km_has_everything_in_time_and_no_fuel_advice_without_a_refuel() -> None:
    a = by_key(ma.evaluate(facts(), {}, NOW))
    assert set(a) == {"brakes", "tyre_rotation", "tyre_age", "battery_12v", "wipers"}       # no refuel on record: no «gasolina envejecida»
    assert {x["status"] for x in a.values()} == {"ok"}
    assert (a["brakes"]["due_odometer"], a["brakes"]["remaining_km"], a["brakes"]["remaining_days"]) == (15000, 6000, 50)
    assert a["tyre_rotation"]["remaining_km"] == 1000 and a["tyre_rotation"]["due_date"] is None    # km only
    assert a["tyre_age"]["due_date"] == D(2030, 3, 1) and a["battery_12v"]["due_date"] == D(2028, 3, 1)
    assert a["wipers"]["due_date"] == D(2026, 3, 1) and a["wipers"]["due_odometer"] is None         # months only


def test_brakes_and_rotation_come_due_by_km_and_brakes_and_wipers_by_date() -> None:
    a = by_key(ma.evaluate(facts(odometer=10200.0), {}, NOW))
    assert a["tyre_rotation"]["status"] == "due" and a["tyre_rotation"]["remaining_km"] == -200
    assert a["brakes"]["status"] == "ok"                                                    # 10,200 km and 10 months: not yet
    a = by_key(ma.evaluate(facts(odometer=4000.0), {}, D(2026, 3, 2)))                      # 12 months and a day, few km
    assert (a["brakes"]["status"], a["wipers"]["status"], a["tyre_rotation"]["status"]) == ("due", "due", "ok")
    # without a delivery date only the km are watched, and what counts only months is left out
    a = by_key(ma.evaluate(facts(start_date=None, odometer=16000.0), {}, NOW))
    assert set(a) == {"brakes", "tyre_rotation"} and a["brakes"]["status"] == "due" and a["brakes"]["due_date"] is None


def test_checked_counts_again_from_that_day_and_those_km() -> None:
    """The owner's question (25-11-2026): told at 10,000, answered at 10,400 → the next one is at 20,400, not at 20,000."""
    states = {"tyre_rotation": State(checked_at=D(2026, 1, 5), checked_odometer=10400.0)}
    a = by_key(ma.evaluate(facts(odometer=10500.0), states, NOW))["tyre_rotation"]
    assert (a["status"], a["due_odometer"], a["base_kind"], a["remaining_km"]) == ("ok", 20400, "check", 9900)
    assert by_key(ma.evaluate(facts(odometer=20400.0), states, NOW))["tyre_rotation"]["status"] == "due"
    # the dated ones: wipers checked today are due in twelve months
    w = by_key(ma.evaluate(facts(), {"wipers": State(checked_at=NOW, checked_odometer=9000.0)}, NOW))["wipers"]
    assert w["due_date"] == D(2027, 1, 10) and w["base_kind"] == "check"


def test_new_parts_on_the_car_sheet_also_restart_the_count() -> None:
    parts = [Part("brake_pads", "front", D(2025, 12, 1), 8000.0), Part("tyres", "front", D(2025, 11, 1), 7000.0), Part("tyres", "spare", D(2020, 1, 1), 0.0),
             Part("wiper_blades", "front", D(2025, 10, 1)), Part("battery_12v", None, D(2025, 9, 1))]
    a = by_key(ma.evaluate(facts(odometer=16000.0, parts=parts), {}, D(2026, 5, 1)))
    assert (a["brakes"]["due_odometer"], a["brakes"]["due_date"], a["brakes"]["base_kind"]) == (23000, D(2026, 12, 1), "part")
    assert a["tyre_rotation"]["due_odometer"] == 17000 and a["tyre_rotation"]["status"] == "ok"          # the spare does not count
    assert a["wipers"]["due_date"] == D(2026, 10, 1) and a["battery_12v"]["due_date"] == D(2028, 9, 1)


def test_tyres_age_from_their_dot_and_past_five_years_they_are_looked_at_every_year() -> None:
    assert ma.dot_date("2324") == D.fromisocalendar(2024, 23, 1) and ma.dot_date("DOT 0120") == D.fromisocalendar(2020, 1, 1)
    assert ma.dot_date("9999") is None and ma.dot_date("") is None and ma.dot_date("23245") is None
    tyres = [Part("tyres", "front", D(2024, 6, 1), dot="1021"), Part("tyres", "rear", D(2024, 6, 1), dot="4023")]    # the oldest decides: March 2021
    born = D.fromisocalendar(2021, 10, 1)
    a = by_key(ma.evaluate(facts(parts=tyres), {}, D(2026, 2, 1)))["tyre_age"]
    assert a["status"] == "ok" and a["due_date"] == ma.add_months(born, 60)
    a = by_key(ma.evaluate(facts(parts=tyres), {}, D(2026, 4, 1)))["tyre_age"]
    assert a["status"] == "due"
    checked = {"tyre_age": State(checked_at=D(2026, 4, 2), checked_odometer=9000.0)}
    a = by_key(ma.evaluate(facts(parts=tyres), checked, D(2026, 12, 1)))["tyre_age"]
    assert a["status"] == "ok" and a["due_date"] == D(2027, 4, 2)                                       # a year after the look
    assert by_key(ma.evaluate(facts(parts=tyres), checked, D(2027, 4, 3)))["tyre_age"]["status"] == "due"
    # the 12 V battery: three years after it was fitted (the delivery, without a part), then every year
    b = by_key(ma.evaluate(facts(), {}, D(2028, 3, 2)))["battery_12v"]
    assert b["status"] == "due" and b["interval_months"] == 36


def test_petrol_that_has_sat_for_four_months_and_again_at_six() -> None:
    f = facts(last_refuel=D(2025, 9, 1))
    assert by_key(ma.evaluate(f, {}, D(2025, 12, 31)))["fuel_age"]["status"] == "ok"
    a = by_key(ma.evaluate(f, {}, D(2026, 1, 2)))["fuel_age"]
    assert (a["status"], a["band"], a["base_kind"], a["can_log"]) == ("due", "m4", "refuel", False)
    assert by_key(ma.evaluate(f, {}, D(2026, 3, 2)))["fuel_age"]["band"] == "m6"                         # a second notice
    # answered «revisado» at four months: it counts from that day; a later refuel rules again
    seen = {"fuel_age": State(checked_at=D(2026, 1, 3))}
    a = by_key(ma.evaluate(f, seen, D(2026, 3, 2)))["fuel_age"]
    assert (a["status"], a["base_kind"], a["due_date"]) == ("ok", "check", D(2026, 5, 3))
    a = by_key(ma.evaluate(facts(last_refuel=D(2026, 2, 1)), seen, D(2026, 3, 2)))["fuel_age"]
    assert (a["base_kind"], a["due_date"]) == ("refuel", D(2026, 6, 1))


def test_how_the_car_is_used_changes_the_advice() -> None:
    a = by_key(ma.evaluate(facts(odometer=8500.0, fast_share=0.62, ev_share=0.81), {}, NOW))
    assert a["tyre_rotation"]["interval_km"] == 8000 and a["tyre_rotation"]["status"] == "due" and "8.000" in a["tyre_rotation"]["note"].render("es")
    assert "81 %" in a["brakes"]["note"].render("es") and "óxido" in a["brakes"]["note"].render("es")
    b = by_key(ma.evaluate(facts(odometer=8500.0, fast_share=0.4, ev_share=0.5), {}, NOW))
    assert b["tyre_rotation"]["interval_km"] == 10000 and b["tyre_rotation"]["note"] is None and b["brakes"]["note"] is None


def test_later_postpones_30_days_or_1000_km_and_off_silences_it() -> None:
    f = facts(odometer=16000.0)
    snooze = State(snooze_until=NOW + datetime.timedelta(days=30), snooze_odometer=17000.0)
    a = by_key(ma.evaluate(f, {"brakes": snooze, "wipers": snooze}, NOW + datetime.timedelta(days=60)))    # wipers due by then (12 months)
    assert a["brakes"]["status"] == "due"                                                              # the 30 days are over
    f2 = facts(odometer=16000.0, start_date=D(2024, 1, 1))
    a = by_key(ma.evaluate(f2, {"brakes": snooze, "wipers": snooze}, NOW))
    assert (a["brakes"]["status"], a["wipers"]["status"]) == ("snoozed", "snoozed") and a["brakes"]["snooze_odometer"] == 17000
    assert a["wipers"]["snooze_odometer"] is None                                                      # wipers do not count km
    a = by_key(ma.evaluate(facts(odometer=17000.0, start_date=D(2024, 1, 1)), {"brakes": snooze, "wipers": snooze}, NOW))
    assert (a["brakes"]["status"], a["wipers"]["status"]) == ("due", "snoozed")                        # 1,000 km on: brakes are back, wipers wait for the date
    off = by_key(ma.evaluate(f2, {"brakes": State(enabled=False)}, NOW))
    assert off["brakes"]["status"] == "off"
    order = [x["key"] for x in ma.evaluate(f2, {"brakes": State(enabled=False), "wipers": snooze}, NOW)]
    assert order[0] == "tyre_rotation" and order[-1] == "brakes" and order.index("wipers") < order.index("tyre_age")   # due, postponed, in time, off


def test_a_custom_plan_with_a_task_for_it_does_not_get_that_recommendation() -> None:
    a = by_key(ma.evaluate(facts(custom_tasks=["Rotación de neumáticos", "Escobillas limpiaparabrisas", "Batería auxiliar de 12 V"]), {}, NOW))
    assert set(a) == {"brakes", "tyre_age"}


# ───────────────────────── routes ─────────────────────────
class _Req:
    client = type("C", (), {"host": "127.0.0.1"})()
    headers = {"user-agent": "t"}


@pytest.fixture
async def world(db):
    """juan (manager, owns an Ebro delivered on 01-03-2025 with 16,000 km), luis (member, may only look), otro (another manager)."""
    h = passwords.hash_password("una-contraseña-larga-1")
    clients = {}
    async with async_session_factory() as s:
        s.add_all([AppUser(id=1, role="manager", username="juan", password_hash=h), AppUser(id=3, role="member", username="luis", parent_id=1, password_hash=h),
                   AppUser(id=4, role="manager", username="otro", password_hash=h)])
        s.add_all([Vehicle(owner_id=1, vin=VIN, name="Ebro", brand="EBRO", model="S700 PHEV", odometer=16000.0, delivery_date=DELIVERY),
                   Vehicle(owner_id=4, vin=OTHER, name="Otro")])
        await s.flush()
        s.add_all([DeviceAccess(user_id=3, vin=VIN, level="view", granted_by=1), MaintenancePlan(vin=VIN, mode="official"),
                   Refuel(vin=VIN, timestamp=D(2025, 8, 1), liters=30.0, detected=True),
                   Drive(vin=VIN, start_time=D(2025, 12, 1), end_time=D(2025, 12, 1, 1), distance_km=40.0, speed_avg_kmh=40.0, end_odometer=16000.0, end_ev_odometer=12800.0)])
        await s.commit()
        for uid in (1, 3, 4):
            token = await auth.create_session(s, await s.get(AppUser, uid), _Req())
            c = httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://testserver")
            c.cookies.set(auth.COOKIE_NAME, token)
            clients[uid] = c
    yield clients
    for c in clients.values():
        await c.aclose()


async def test_the_list_says_what_is_due_and_the_three_answers_change_it(world) -> None:
    juan = world[1]
    with freeze_time(NOW):
        d = (await juan.get(f"/api/vehicles/{VIN}/maintenance/advice")).json()
        a = by_key(d["advice"])
        assert d["due"] == 3 and [x["key"] for x in d["advice"]][:3] == ["brakes", "tyre_rotation", "fuel_age"]
        assert a["brakes"]["name"] == "Revisar pastillas y discos de freno" and "80 %" in a["brakes"]["note"] and a["brakes"]["remaining_km"] == -1000
        assert a["fuel_age"]["due_date"].startswith("2025-12-01") and a["wipers"]["status"] == "ok"
        # «Revisado, está bien»
        d = (await juan.post(f"/api/vehicles/{VIN}/maintenance/advice/brakes/check")).json()
        b = by_key(d["advice"])["brakes"]
        assert (b["status"], b["due_odometer"], b["base_kind"]) == ("ok", 31000, "check") and b["due_date"].startswith("2027-01-10") and d["due"] == 2
        # «Recuérdamelo más adelante»
        d = (await juan.post(f"/api/vehicles/{VIN}/maintenance/advice/tyre_rotation/snooze")).json()
        r = by_key(d["advice"])["tyre_rotation"]
        assert (r["status"], r["snooze_odometer"]) == ("snoozed", 17000) and r["snooze_until"].startswith("2026-02-09") and d["due"] == 1
        # off and on again
        d = (await juan.put(f"/api/vehicles/{VIN}/maintenance/advice/fuel_age", json={"enabled": False})).json()
        assert by_key(d["advice"])["fuel_age"]["status"] == "off" and d["due"] == 0
        d = (await juan.put(f"/api/vehicles/{VIN}/maintenance/advice/fuel_age", json={"enabled": True})).json()
        assert by_key(d["advice"])["fuel_age"]["status"] == "due"
        # a check with its own day and km; never in the future; an unknown one does not exist
        d = (await juan.post(f"/api/vehicles/{VIN}/maintenance/advice/tyre_rotation/check", json={"date": "2026-01-02T00:00:00", "odometer": 15500})).json()
        assert by_key(d["advice"])["tyre_rotation"]["due_odometer"] == 25500
        assert (await juan.post(f"/api/vehicles/{VIN}/maintenance/advice/brakes/check", json={"date": "2026-03-01T00:00:00"})).status_code == 422
        assert (await juan.post(f"/api/vehicles/{VIN}/maintenance/advice/nope/check")).status_code == 404


async def test_recording_the_intervention_answers_the_recommendation_with_its_day_and_km(world) -> None:
    juan = world[1]
    with freeze_time(NOW):
        r = await juan.post(f"/api/vehicles/{VIN}/maintenance/events", json={"title": "Revisar pastillas y discos de freno", "category": "Frenos",
                                                                              "service_date": "2026-01-08T00:00:00", "odometer": 15900, "advice_key": "brakes"})
        assert r.status_code == 200, r.text
        b = by_key((await juan.get(f"/api/vehicles/{VIN}/maintenance/advice")).json()["advice"])["brakes"]
        assert (b["status"], b["due_odometer"], b["base_kind"]) == ("ok", 30900, "check") and b["due_date"].startswith("2027-01-08")
        # an intervention without it, or with a made-up key, answers nothing
        await juan.post(f"/api/vehicles/{VIN}/maintenance/events", json={"title": "Rotación", "advice_key": "nope"})
        assert by_key((await juan.get(f"/api/vehicles/{VIN}/maintenance/advice")).json()["advice"])["tyre_rotation"]["status"] == "due"


async def test_who_may_look_and_who_may_answer_and_the_texts_come_in_the_accounts_language(world) -> None:
    with freeze_time(NOW):
        assert (await world[3].get(f"/api/vehicles/{VIN}/maintenance/advice")).status_code == 200            # a member who may look
        assert (await world[3].post(f"/api/vehicles/{VIN}/maintenance/advice/brakes/check")).status_code == 403
        assert (await world[3].put(f"/api/vehicles/{VIN}/maintenance/advice/brakes", json={"enabled": False})).status_code == 403
        assert (await world[4].get(f"/api/vehicles/{VIN}/maintenance/advice")).status_code in (403, 404)     # another manager's car
        assert (await world[4].post(f"/api/vehicles/{VIN}/maintenance/advice/brakes/snooze")).status_code in (403, 404)
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://testserver") as anon:
            assert (await anon.get(f"/api/vehicles/{VIN}/maintenance/advice")).status_code == 401
        en = by_key((await world[1].get(f"/api/vehicles/{VIN}/maintenance/advice", headers={"Accept-Language": "en"})).json()["advice"])
        assert en["brakes"]["name"] == "Check the brake pads and discs" and "rust" in en["brakes"]["note"]


async def test_a_custom_plan_with_its_own_task_hides_that_recommendation(world) -> None:
    async with async_session_factory() as s:
        (await s.get(MaintenancePlan, VIN)).mode = "custom"
        s.add(MaintenanceItem(vin=VIN, name="Rotación de neumáticos", category="Neumáticos", interval_km=10000, origin="custom"))
        s.add(VehiclePart(vin=VIN, category="tyres", position="front", name="Neumáticos", attrs={"dot": "1021"}))
        await s.commit()
    with freeze_time(D(2026, 4, 1)):
        a = by_key((await world[1].get(f"/api/vehicles/{VIN}/maintenance/advice")).json()["advice"])
    assert "tyre_rotation" not in a and a["tyre_age"]["status"] == "due" and a["tyre_age"]["base_kind"] == "part"


# ───────────────────────── the notice ─────────────────────────
@pytest.fixture
def notices(monkeypatch):
    sent = []

    async def dispatch(cls, category, title, message, tags=None, priority="default", **kwargs):
        sent.append((category, title.render("es"), message.render("es")))
        return []

    monkeypatch.setattr(NotificationService, "dispatch", classmethod(dispatch))
    monkeypatch.setattr(VehiclePoller, "request_poll", lambda self, vin, force=False: None)
    return sent


async def poll(at, odometer=16000.0):
    p = VehiclePoller.get_instance()
    p._last_maint_check_at.pop(VIN, None)
    with freeze_time(at):
        async with async_session_factory() as s:
            v = (await s.execute(select(Vehicle).where(Vehicle.vin == VIN))).scalar_one()
            v.odometer = odometer
            await p._check_and_dispatch_alerts(s, v, {"odometer_km": odometer})
            await s.commit()


async def test_the_first_look_at_a_car_is_silent_and_only_what_comes_due_later_notifies(world, notices) -> None:
    await poll(NOW)                                                                              # three are due already: no notice
    assert not [n for n in notices if "ecomenda" in n[1]]
    due = by_key((await world[1].get(f"/api/vehicles/{VIN}/maintenance/advice")).json()["advice"])
    assert [k for k, a in due.items() if a["status"] == "due"]                                   # but they are listed
    await poll(NOW + datetime.timedelta(days=3))
    assert not [n for n in notices if "ecomenda" in n[1]]
    # the petrol reaches six months without a refuel after that first look: that one does notify
    await poll(NOW + datetime.timedelta(days=30))
    advice = [n for n in notices if "ecomenda" in n[1]]
    assert len(advice) == 1 and advice[0][1] == "Recomendación de mantenimiento: Ebro" and advice[0][2].startswith("Gasolina envejecida en el depósito")


async def test_several_due_at_once_are_one_notice_and_each_one_is_sent_once_until_it_is_answered(world, notices) -> None:
    async with async_session_factory() as s:                                                     # a car EVbro had looked at before
        s.add(AlertState(vin=VIN, key=f"advice:{ma.SEEN_MARK}", sent_at=NOW))
        await s.commit()
    await poll(NOW)
    advice = [n for n in notices if "ecomenda" in n[1]]
    assert len(advice) == 1 and advice[0][0] == "maintenance" and advice[0][1] == "3 recomendaciones de mantenimiento: Ebro"
    assert all(text in advice[0][2] for text in ("• Revisar pastillas y discos de freno", "• Rotar los neumáticos y mirar el dibujo", "• Gasolina envejecida en el depósito"))
    assert "Contesta en Mantenimiento" in advice[0][2]
    await poll(NOW + datetime.timedelta(days=3))
    assert len([n for n in notices if "ecomenda" in n[1]]) == 1                                  # nothing new: not repeated
    # answered and due again later → a new notice, this time for that one alone
    with freeze_time(NOW + datetime.timedelta(days=4)):
        await world[1].post(f"/api/vehicles/{VIN}/maintenance/advice/tyre_rotation/check")
    await poll(NOW + datetime.timedelta(days=5))
    assert len([n for n in notices if "ecomenda" in n[1]]) == 1
    # the petrol gets its second notice at six months without a refuel (1 August → 1 February)
    await poll(NOW + datetime.timedelta(days=30))
    advice = [n for n in notices if "ecomenda" in n[1]]
    assert len(advice) == 2 and advice[-1][1] == "Recomendación de mantenimiento: Ebro" and advice[-1][2].startswith("Gasolina envejecida en el depósito")
    # 10,000 km after that «revisado» the rotation is due again, with the odometer of the reading in hand
    await poll(NOW + datetime.timedelta(days=40), odometer=26100.0)
    advice = [n for n in notices if "ecomenda" in n[1]]
    assert len(advice) == 3 and advice[-1][2].startswith("Rotar los neumáticos y mirar el dibujo")
