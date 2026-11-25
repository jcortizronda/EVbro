"""Scheduled departure («Reserva tu viaje» in the official app): the plan lives in the car. EVbro reads it from what the car
already reports and changes its time, its days and its switch. One schedule, like the official app.

Captured from the official app on 21-11-2026: the order is `appointmentTravel` and the reading comes in `travelQuery`.
"""
from __future__ import annotations

import datetime
from zoneinfo import ZoneInfo

import httpx
import pytest

from app.core import commands
from app.db.models import AppUser, DeviceAccess, EbroAccount, TelemetrySnapshot, Vehicle
from app.db.session import async_session_factory
from app.main import app
from app.security import auth, passwords
from app.services import departure
from app.services.poller import VehiclePoller

VIN = "LSJA0000000000051"
MADRID = ZoneInfo("Europe/Madrid")
WINTER = datetime.datetime(2026, 11, 21, 12, 0, tzinfo=datetime.timezone.utc)      # Madrid is UTC+1
SUMMER = datetime.datetime(2026, 7, 21, 12, 0, tzinfo=datetime.timezone.utc)       # Madrid is UTC+2
# What the cloud returns for a car nobody has touched, and for one with two departures (an S800)
ONE = {"mainSwitcher": 0, "appointmentTravelSetVOS": [{"travelTime": 420, "travelDate": [1, 2, 3, 4, 5, 6, 7], "switcher": 0, "id": 1}]}
TWO = {"mainSwitcher": 1, "appointmentTravelSetVOS": [{"travelTime": 420, "travelDate": [1, 2, 3, 4, 5], "switcher": 1, "id": 1},
                                                      {"travelTime": 960, "travelDate": [6, 7], "switcher": 0, "id": 2}]}


# ───────────────────────── reading the plan ─────────────────────────
def test_the_plan_is_read_in_local_time() -> None:
    """`travelTime` is minutes of the day in UTC, like the scheduled charge."""
    plan = departure.read_plan(TWO, MADRID, WINTER)
    assert plan["known"] and plan["enabled"] is True
    # one schedule, like the official app: of a car that holds two entries, the first
    assert plan["departures"] == [{"time": "08:00", "days": [1, 2, 3, 4, 5], "enabled": True}]
    assert departure.read_plan(TWO, MADRID, SUMMER)["departures"][0]["time"] == "09:00"


def test_a_departure_only_counts_with_the_master_switch_on() -> None:
    """Seen on a real car: a departure with its own switch on and the master one off. Nothing is scheduled then."""
    raw = {"mainSwitcher": 0, "appointmentTravelSetVOS": [{"travelTime": 420, "travelDate": [1, 2, 3, 4, 5, 6, 7], "switcher": 1}]}
    plan = departure.read_plan(raw, MADRID, WINTER)
    assert plan["enabled"] is False and plan["departures"][0]["enabled"] is False


def test_a_car_that_has_not_reported_its_plan_is_unknown_not_off() -> None:
    for raw in (None, {}, {"dumpEnergy": "60"}, {"appointmentTravelSetVOS": "x"}):
        plan = departure.read_plan(raw, MADRID, WINTER)
        assert plan == {"known": False, "enabled": None, "departures": [], "timezone": "Europe/Madrid"}


# ───────────────────────── building the order ─────────────────────────
def test_the_order_carries_one_schedule_and_keeps_the_days_of_the_car_when_none_are_given() -> None:
    key, params = departure.build_order([{"time": "07:30", "enabled": True}], TWO, MADRID, WINTER)
    assert key == "salida_prog_on"
    assert params == {"mainSwitcher": 1, "appointmentTravelAOS": [
        {"switcher": 1, "travelTime": 390, "travelWeekday": [1, 2, 3, 4, 5]}]}       # 07:30 in Madrid is 06:30 UTC in winter


def test_the_days_chosen_travel_with_the_order_monday_first() -> None:
    key, params = departure.build_order([{"time": "07:30", "enabled": True, "days": [5, 1, 1, 3]}], TWO, MADRID, WINTER)
    assert key == "salida_prog_on"
    assert [e["travelWeekday"] for e in params["appointmentTravelAOS"]] == [[1, 3, 5]]              # sorted, without repeats


@pytest.mark.parametrize("days", [[], [0], [8], [1, "2"], [True], "135", [1.5]])
def test_a_departure_needs_at_least_one_real_weekday(days) -> None:
    with pytest.raises(departure.DepartureError) as err:
        departure.build_order([{"time": "07:30", "enabled": True, "days": days}], ONE, MADRID, WINTER)
    assert err.value.reason == "days_invalid"


def test_turning_every_departure_off_turns_the_master_switch_off() -> None:
    key, params = departure.build_order([{"time": "08:00", "enabled": False}], ONE, MADRID, WINTER)
    assert key == "salida_prog_off"
    assert params == {"mainSwitcher": 0, "appointmentTravelAOS": [{"switcher": 0, "travelTime": 420, "travelWeekday": [1, 2, 3, 4, 5, 6, 7]}]}


def test_what_is_read_and_sent_back_unchanged_is_the_same_plan() -> None:
    plan = departure.read_plan(TWO, MADRID, WINTER)
    _key, params = departure.build_order([{"time": d["time"], "enabled": d["enabled"]} for d in plan["departures"]], TWO, MADRID, WINTER)
    sent = [(e["travelTime"], e["travelWeekday"], e["switcher"]) for e in params["appointmentTravelAOS"]]
    assert sent == [(e["travelTime"], e["travelDate"], e["switcher"]) for e in TWO["appointmentTravelSetVOS"][:1]]


def test_a_car_without_departures_gets_one_for_every_day() -> None:
    _key, params = departure.build_order([{"time": "06:45", "enabled": True}], {"appointmentTravelSetVOS": []}, MADRID, WINTER)
    assert params["appointmentTravelAOS"] == [{"switcher": 1, "travelTime": 345, "travelWeekday": [1, 2, 3, 4, 5, 6, 7]}]


@pytest.mark.parametrize("wanted, reason", [
    ([{"time": "07:30", "enabled": True}, {"time": "18:00", "enabled": False}], "plan_invalid"),      # one schedule, not a time per day
    ([{"time": "7:30", "enabled": True}], "time_invalid"),
    ([{"time": "25:00", "enabled": True}], "time_invalid"),
    ([], "plan_invalid"),
])
def test_a_plan_that_does_not_fit_the_car_is_refused(wanted, reason) -> None:
    with pytest.raises(departure.DepartureError) as err:
        departure.build_order(wanted, TWO, MADRID, WINTER)
    assert err.value.reason == reason


# ───────────────────────── the routes ─────────────────────────
class _Req:
    client = type("C", (), {"host": "127.0.0.1"})()
    headers = {"user-agent": "tests"}


@pytest.fixture
async def world(db, data_dir, monkeypatch):
    """Manager 1 owns the car (PIN saved); member 2 can only look at it. {user id: client}, and the orders sent."""
    sent: list[dict] = []

    def fake_send(ctx, cmd_key, emit=None, params=None):
        sent.append({"key": cmd_key, "pin": ctx.pin, "params": params})
        return "aceptado"

    async def no_poll(self, vin, force=False):
        return None

    monkeypatch.setattr(commands, "send", fake_send)
    monkeypatch.setattr(VehiclePoller, "poll_vehicle", no_poll)
    monkeypatch.setattr(VehiclePoller, "_contexts", {}, raising=False)
    monkeypatch.setattr(VehiclePoller, "_latest_snapshots", {}, raising=False)
    monkeypatch.setattr("app.api.routes_departure.get_timezone", lambda: MADRID)
    h = passwords.hash_password("una-contraseña-larga-1")
    async with async_session_factory() as s:
        s.add_all([AppUser(id=1, role="manager", username="juan", password_hash=h),
                   AppUser(id=2, role="member", username="lucia", parent_id=1, password_hash=h)])
        s.add(Vehicle(owner_id=1, vin=VIN, name="Ebro"))
        s.add(EbroAccount(phone="600000051", password="x", pin="1234", is_active=True, user_id=1))
        await s.flush()
        s.add(DeviceAccess(user_id=2, vin=VIN, level="view", granted_by=1))
        s.add(TelemetrySnapshot(vin=VIN, timestamp=datetime.datetime(2026, 11, 21, 10, 0), state="parked", raw_data=dict(TWO)))
        await s.commit()
        clients = {}
        for uid in (1, 2):
            token = await auth.create_session(s, await s.get(AppUser, uid), _Req())
            c = httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://testserver")
            c.cookies.set(auth.COOKIE_NAME, token)
            clients[uid] = c
    yield clients, sent
    for c in clients.values():
        await c.aclose()


async def test_the_plan_of_the_car_is_served_from_its_last_reading(world) -> None:
    clients, sent = world
    plan = (await clients[1].get(f"/api/vehicles/{VIN}/departure")).json()
    assert plan["known"] and plan["enabled"] and [d["days"] for d in plan["departures"]] == [[1, 2, 3, 4, 5]]
    assert (await clients[2].get(f"/api/vehicles/{VIN}/departure")).status_code == 200          # looking is allowed with «ver»
    VehiclePoller._latest_snapshots[VIN] = {"raw_data": dict(ONE)}                              # a newer reading in memory wins
    assert (await clients[1].get(f"/api/vehicles/{VIN}/departure")).json()["enabled"] is False
    assert sent == []                                                                          # reading sends nothing to the car


async def test_saving_sends_one_order_with_the_saved_pin(world) -> None:
    clients, sent = world
    body = {"departures": [{"time": "07:30", "enabled": True}]}
    r = await clients[1].put(f"/api/vehicles/{VIN}/departure", json=body)
    assert r.status_code == 200, r.text
    assert r.json()["success"] and r.json()["enabled"] and [d["time"] for d in r.json()["departures"]] == ["07:30"]
    assert len(sent) == 1 and sent[0]["key"] == "salida_prog_on" and sent[0]["pin"] == "1234"
    assert [e["travelWeekday"] for e in sent[0]["params"]["appointmentTravelAOS"]] == [[1, 2, 3, 4, 5]]
    # choosing the days: they travel with the order and come back in the answer
    body["departures"][0]["days"] = [1, 3]
    r = await clients[1].put(f"/api/vehicles/{VIN}/departure", json=body)
    assert r.status_code == 200 and [d["days"] for d in r.json()["departures"]] == [[1, 3]]
    assert [e["travelWeekday"] for e in sent[1]["params"]["appointmentTravelAOS"]] == [[1, 3]]
    body["departures"][0]["days"] = []
    r = await clients[1].put(f"/api/vehicles/{VIN}/departure", json=body)
    assert r.status_code == 422 and r.json()["reason"] == "days_invalid" and len(sent) == 2


async def test_a_view_only_account_and_a_bad_plan_send_nothing(world) -> None:
    clients, sent = world
    body = {"departures": [{"time": "07:30", "enabled": True}]}
    assert (await clients[2].put(f"/api/vehicles/{VIN}/departure", json=body)).status_code == 403
    r = await clients[1].put(f"/api/vehicles/{VIN}/departure", json={"departures": [{"time": "7:30", "enabled": True}]})
    assert r.status_code == 422 and r.json()["reason"] == "time_invalid"
    two = {"departures": [{"time": "07:30", "enabled": True}, {"time": "17:00", "enabled": False}]}
    assert (await clients[1].put(f"/api/vehicles/{VIN}/departure", json=two)).status_code == 422            # one schedule only
    assert (await clients[1].put(f"/api/vehicles/{VIN}/departure", json={"departures": []})).status_code == 422
    assert sent == []


async def test_an_order_the_car_refuses_is_said_and_not_shown_as_saved(world, monkeypatch) -> None:
    from app.core.errors import CommandError

    clients, _sent = world

    def refused(ctx, cmd_key, emit=None, params=None):
        raise CommandError("HTTP 200 A00084 — comando no permitido", code="A00084")

    monkeypatch.setattr(commands, "send", refused)
    r = await clients[1].put(f"/api/vehicles/{VIN}/departure", json={"departures": [{"time": "07:30", "enabled": True}]})
    assert r.status_code == 424 and r.json()["success"] is False and r.json()["code"] == "A00084"
