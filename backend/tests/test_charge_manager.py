"""Charge manager with a simulated car: what is sent to the car and how the state evolves."""
from __future__ import annotations

import datetime

import httpx
import pytest

from app.config import settings
from app.db.models import AppUser, ChargePlan, Vehicle
from app.db.session import async_session_factory
from app.main import app
from app.security import auth, passwords
from app.services import charge_manager as cm
from app.services.charge_manager import ChargeError, ChargeManager
from app.services.poller import VehiclePoller

VIN = "LSJA0000000000001"
UTC = datetime.timezone.utc
T0 = datetime.datetime(2026, 1, 15, 21, 0, tzinfo=UTC)       # 22:00 in Spain (winter)


class FakeCar:
    """Stands in for the real sender: records the commands and answers whatever it is told."""
    def __init__(self):
        self.sent: list[tuple[str, dict, str]] = []
        self.response = {"success": True, "message": "ok", "reason": None}
        self.car = {"enabled": False, "start": None, "end": None, "duration_min": None}   # what it «reads» from the car

    async def read(self, vin):
        return self.car

    async def __call__(self, vin, key, params, origin):
        self.sent.append((key, params, origin))
        return dict(self.response)

    @property
    def plans(self):
        return [p["chargeAppointPlans"][0] for _k, p, _o in self.sent]


class Clock:
    def __init__(self, now=T0): self.now = now
    def __call__(self): return self.now
    def advance(self, **kw): self.now += datetime.timedelta(**kw)


@pytest.fixture(autouse=True)
def _env(monkeypatch):
    monkeypatch.setattr(settings, "TIMEZONE", "Europe/Madrid")
    VehiclePoller._latest_snapshots = {}
    yield
    VehiclePoller._latest_snapshots = {}


@pytest.fixture
async def manager(db, monkeypatch):
    async with async_session_factory() as s:
        s.add(Vehicle(owner_id=1, vin=VIN, name="Ebro"))
        await s.commit()
    car, clock = FakeCar(), Clock()
    g = ChargeManager(sender=car, clock=clock, reader=car.read, verify_delay_s=None)
    g.fake_car, g.fake_clock = car, clock
    monkeypatch.setattr(ChargeManager, "_instance", g)
    return g


async def plan_row() -> ChargePlan:
    async with async_session_factory() as s:
        return await s.get(ChargePlan, VIN)


def reading(soc, charging=True, plugged=True):
    return {"battery_level": soc, "is_charging": charging, "cable_connected": plugged}


# ───────────────────────── user actions ─────────────────────────
async def test_stop_charging_sends_an_already_finished_window(manager) -> None:
    state = await manager.stop(VIN)
    (key, params, origin), = manager.fake_car.sent
    plan = params["chargeAppointPlans"][0]
    assert key == "carga_prog_on" and params["mainSwitch"] == 1
    assert (plan["startTime"], plan["timeConsuming"]) == (19 * 60 + 30, 60)       # 21:00 UTC − 90 min
    assert state["mode"] == "hold" and state["hold_reason"] == "user" and state["status"] == "hold_user"
    assert state["schedule"]["enabled"] is False


async def test_start_charging_removes_the_schedule(manager) -> None:
    await manager.stop(VIN)
    state = await manager.start(VIN)
    key, params, _ = manager.fake_car.sent[-1]
    assert key == "carga_prog_off" and params["mainSwitch"] == 0 and params["chargeAppointPlans"][0]["switchStatus"] == 0
    assert state["mode"] == "free" and state["hold_reason"] is None and state["status"] == "free"


async def test_schedule_with_start_and_end(manager) -> None:
    state = await manager.set_schedule(VIN, 22 * 60, 8 * 60)                       # 22:00 → 06:00
    plan = manager.fake_car.plans[-1]
    assert plan["startTime"] == 21 * 60 and plan["timeConsuming"] == 480 and plan["switchStatus"] == 1   # 22:00 local = 21:00 UTC
    assert state["mode"] == "scheduled" and state["schedule"] == {"enabled": True, "start": "22:00", "end": "06:00", "duration_min": 480}


@pytest.mark.parametrize("duration,reason", [(30, "duration_too_short"), (1440, "duration_too_long")])
async def test_schedule_validates_the_duration(manager, duration, reason) -> None:
    with pytest.raises(ChargeError) as e:
        await manager.set_schedule(VIN, 0, duration)
    assert e.value.reason == reason and manager.fake_car.sent == []


async def test_clear_the_schedule(manager) -> None:
    await manager.set_schedule(VIN, 22 * 60, 480)
    state = await manager.clear_schedule(VIN)
    assert manager.fake_car.sent[-1][0] == "carga_prog_off"
    assert state["mode"] == "free" and state["schedule"]["enabled"] is False
    assert state["schedule"]["start"] == "22:00"                                  # remembered for next time


async def test_clearing_the_schedule_does_not_lift_a_hold(manager) -> None:
    await manager.stop(VIN)
    n = len(manager.fake_car.sent)
    state = await manager.clear_schedule(VIN)
    assert state["mode"] == "hold" and len(manager.fake_car.sent) == n


async def test_stop_and_start_restores_the_user_schedule(manager) -> None:
    await manager.set_schedule(VIN, 22 * 60, 480)
    state = await manager.stop(VIN)
    assert state["mode"] == "hold" and state["schedule"]["enabled"] is True        # kept, not lost
    state = await manager.start(VIN)
    key, params, _ = manager.fake_car.sent[-1]
    plan = params["chargeAppointPlans"][0]
    assert key == "carga_prog_on" and (plan["startTime"], plan["timeConsuming"], plan["switchStatus"]) == (21 * 60, 480, 1)
    assert state["mode"] == "scheduled" and state["schedule"]["start"] == "22:00"


async def test_stop_keeps_the_schedule_the_car_had_and_start_restores_it(manager) -> None:
    manager.fake_car.car = {"enabled": True, "start": "01:00", "end": "05:00", "duration_min": 240}   # set from the official app
    await manager.stop(VIN)
    state = await manager.start(VIN)
    plan = manager.fake_car.plans[-1]
    assert (plan["startTime"], plan["timeConsuming"]) == (0, 240)                                   # 01:00 local = 00:00 UTC
    assert state["mode"] == "scheduled" and state["schedule"] == {"enabled": True, "start": "01:00", "end": "05:00", "duration_min": 240}


async def test_stop_and_start_without_a_previous_schedule_turns_scheduling_off(manager) -> None:
    await manager.stop(VIN)
    state = await manager.start(VIN)
    assert manager.fake_car.sent[-1][0] == "carga_prog_off" and state["schedule"]["enabled"] is False


async def test_stopping_twice_does_not_mistake_the_hold_window_for_a_schedule(manager) -> None:
    await manager.stop(VIN)
    manager.fake_car.car = {"enabled": True, "start": "20:30", "end": "21:30", "duration_min": 60}   # the hold window
    await manager.stop(VIN)
    state = await manager.start(VIN)
    assert state["mode"] == "free" and state["schedule"]["enabled"] is False


async def test_clear_a_schedule_created_outside_evbro(manager) -> None:
    state = await manager.clear_schedule(VIN)
    assert manager.fake_car.sent[-1][0] == "carga_prog_off" and state["mode"] == "free"


# ───────────────────────── checking that the car applied it ─────────────────────────
async def test_verify_resends_once_if_the_car_did_not_apply_the_clear(manager) -> None:
    await manager.stop(VIN)
    await manager.start(VIN)
    n = len(manager.fake_car.sent)
    manager.fake_car.car = {"enabled": True, "start": "20:30", "end": "21:30", "duration_min": 60}   # still has the window
    assert await manager.verify_applied(VIN) is False
    assert len(manager.fake_car.sent) == n + 1 and manager.fake_car.sent[-1][0] == "carga_prog_off"
    assert manager.fake_car.sent[-1][2] == "automático (verificación)"
    manager.fake_car.car = {"enabled": False, "start": None, "end": None, "duration_min": None}
    assert await manager.verify_applied(VIN) is True and len(manager.fake_car.sent) == n + 1


async def test_verify_touches_nothing_if_it_cannot_read_or_it_matches(manager) -> None:
    await manager.stop(VIN)
    n = len(manager.fake_car.sent)
    manager.fake_car.car = None
    assert await manager.verify_applied(VIN) is True
    manager.fake_car.car = {"enabled": True, "start": "20:30", "end": "21:30", "duration_min": 60}   # hold applied: matches
    assert await manager.verify_applied(VIN) is True and len(manager.fake_car.sent) == n


async def test_state_marks_applying_while_the_car_does_not_reflect_the_command(manager) -> None:
    await manager.stop(VIN)
    await manager.start(VIN)
    manager.fake_car.car = {"enabled": True, "start": "20:30", "end": "21:30", "duration_min": 60}
    assert (await manager.state(VIN, live=True))["car_pending"] is True
    manager.fake_clock.advance(minutes=5)
    assert (await manager.state(VIN, live=True))["car_pending"] is False        # after a while it is no longer «applying»


# ───────────────────────── failures ─────────────────────────
async def test_if_the_car_rejects_the_state_does_not_change_and_is_explained(manager) -> None:
    manager.fake_car.response = {"success": False, "message": "PIN no configurado", "reason": "pin"}
    with pytest.raises(ChargeError) as e:
        await manager.stop(VIN)
    assert e.value.reason == "pin" and e.value.status == 422
    row = await plan_row()
    assert row.mode == "free" and "PIN" in row.last_error
    assert (await manager.state(VIN))["last_error"] == row.last_error


async def test_expired_ebro_session_is_424(manager) -> None:
    manager.fake_car.response = {"success": False, "message": "Sesión caducada", "reason": "reauth"}
    with pytest.raises(ChargeError) as e:
        await manager.start(VIN)
    assert e.value.status == 424 and e.value.reason == "reauth"


# ───────────────────────── battery target ─────────────────────────
async def test_the_target_only_accepts_the_dropdown_values(manager) -> None:
    with pytest.raises(ChargeError) as e:
        await manager.set_target(VIN, 52)
    assert e.value.reason == "target_invalid"
    assert (await manager.set_target(VIN, 80))["target_pct"] == 80


async def test_if_nothing_was_touched_the_manager_never_sends_anything(manager) -> None:
    await manager.on_snapshot(VIN, reading(100))
    await manager.tick()
    assert manager.fake_car.sent == [] and await plan_row() is None             # no commands and no new rows


async def test_setting_a_target_only_stores_it_until_it_is_reached(manager) -> None:
    await manager.set_target(VIN, 80)
    await manager.on_snapshot(VIN, reading(60))
    assert manager.fake_car.sent == []


async def test_reaching_the_target_while_charging_holds_and_asks_for_fine_polling(manager) -> None:
    await manager.set_target(VIN, 80)
    await manager.on_snapshot(VIN, reading(81))
    assert len(manager.fake_car.sent) == 1 and manager.fake_car.sent[0][2] == "automático"
    state = await manager.state(VIN)
    assert state["mode"] == "hold" and state["hold_reason"] == "target" and state["status"] == "hold_target"
    assert manager.wants_fast_poll(VIN) is True


async def test_released_when_plugged_with_room_and_back_to_free_charging(manager) -> None:
    await manager.set_target(VIN, 80)
    await manager.on_snapshot(VIN, reading(80))
    manager.fake_clock.advance(hours=14)
    await manager.on_snapshot(VIN, reading(45, charging=False, plugged=True))
    assert manager.fake_car.sent[-1][0] == "carga_prog_off"                        # no user schedule → charge now
    state = await manager.state(VIN)
    assert state["mode"] == "free" and manager.wants_fast_poll(VIN) is False


async def test_released_back_to_the_user_schedule(manager) -> None:
    await manager.set_schedule(VIN, 22 * 60, 480)
    await manager.set_target(VIN, 80)
    await manager.on_snapshot(VIN, reading(80))
    assert (await manager.state(VIN))["mode"] == "hold"
    manager.fake_clock.advance(hours=14)
    await manager.on_snapshot(VIN, reading(40, charging=False))
    key, params, _ = manager.fake_car.sent[-1]
    assert key == "carga_prog_on" and params["chargeAppointPlans"][0]["timeConsuming"] == 480
    assert (await manager.state(VIN))["mode"] == "scheduled"


async def test_raising_the_target_to_100_releases_the_hold(manager) -> None:
    await manager.set_target(VIN, 80)
    await manager.on_snapshot(VIN, reading(82))
    state = await manager.set_target(VIN, 100)
    assert state["mode"] == "free" and manager.fake_car.sent[-1][0] == "carga_prog_off"


async def test_automatic_actions_are_spaced_out_after_a_failure_too(manager) -> None:
    await manager.set_target(VIN, 80)
    manager.fake_car.response = {"success": False, "message": "sin red", "reason": None}
    await manager.on_snapshot(VIN, reading(85))                                    # tries to hold and fails (does not raise)
    await manager.on_snapshot(VIN, reading(85))                                    # 0 min later: no retry
    assert len(manager.fake_car.sent) == 1
    manager.fake_clock.advance(minutes=cm.MIN_AUTO_GAP_MIN + 1)
    await manager.on_snapshot(VIN, reading(85))
    assert len(manager.fake_car.sent) == 2


# ───────────────────────── renewing the hold ─────────────────────────
async def test_the_hold_renews_itself_before_it_reopens(manager, monkeypatch) -> None:
    monkeypatch.setattr(cm, "TICK_EVERY_S", 0)
    await manager.stop(VIN)
    first = manager.fake_car.plans[-1]["startTime"]
    manager.fake_clock.advance(hours=10)
    await manager.tick()
    assert len(manager.fake_car.sent) == 1                                        # 12 h before it opens: not needed
    manager.fake_clock.advance(hours=9)                                                 # 3 h 30 left
    await manager.tick()
    assert len(manager.fake_car.sent) == 2
    second = manager.fake_car.plans[-1]["startTime"]
    assert second != first
    row = await plan_row()
    assert row.mode == "hold" and row.hold_reason == "user" and row.hold_start_utc == second


async def test_renewing_keeps_the_hold_reason(manager, monkeypatch) -> None:
    monkeypatch.setattr(cm, "TICK_EVERY_S", 0)
    await manager.set_target(VIN, 80)
    await manager.on_snapshot(VIN, reading(80))
    manager.fake_clock.advance(hours=19)
    await manager.tick()
    assert (await plan_row()).hold_reason == "target"


# ───────────────────────── API ─────────────────────────
class _Req:
    client = type("C", (), {"host": "127.0.0.1"})()
    headers = {"user-agent": "t"}


async def _web(manager):
    async with async_session_factory() as s:
        user = AppUser(id=1, role="manager", username="juan", password_hash=passwords.hash_password("una-contraseña-larga-1"))
        s.add(user)
        await s.commit()
        token = await auth.create_session(s, user, _Req())
    c = httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://testserver")
    c.cookies.set(auth.COOKIE_NAME, token)
    return c


async def test_api_target_stop_start(manager) -> None:
    async with await _web(manager) as c:
        r = await c.put(f"/api/vehicles/{VIN}/charge/target", json={"target_pct": 80})
        assert r.status_code == 200 and r.json()["target_pct"] == 80 and r.json()["target_choices"] == list(range(50, 101, 5))
        bad = await c.put(f"/api/vehicles/{VIN}/charge/target", json={"target_pct": 52})
        assert bad.status_code == 422 and bad.json()["reason"] == "target_invalid"
        r = await c.post(f"/api/vehicles/{VIN}/charge/stop")
        assert r.status_code == 200 and r.json()["status"] == "hold_user"
        r = await c.post(f"/api/vehicles/{VIN}/charge/start")
        assert r.status_code == 200 and r.json()["status"] == "free"
        assert (await c.get(f"/api/vehicles/{VIN}/charge/plan")).json()["target_pct"] == 80


async def test_api_schedule_with_end_or_duration(manager) -> None:
    async with await _web(manager) as c:
        url = f"/api/vehicles/{VIN}/charge/schedule"
        r = await c.put(url, json={"start": "23:30", "end": "07:30"})
        assert r.status_code == 200 and r.json()["schedule"] == {"enabled": True, "start": "23:30", "end": "07:30", "duration_min": 480}
        r = await c.put(url, json={"start": "01:00", "duration_min": 180})
        assert r.json()["schedule"]["end"] == "04:00"
        r = await c.delete(url)
        assert r.status_code == 200 and r.json()["schedule"]["enabled"] is False


@pytest.mark.parametrize("body,reason", [
    ({"start": "25:00", "end": "06:00"}, "time_invalid"),
    ({"start": "22:00"}, "schedule_invalid"),
    ({"start": "22:00", "end": "06:00", "duration_min": 120}, "schedule_invalid"),
    ({"start": "22:00", "end": "22:00"}, "schedule_invalid"),
    ({"start": "22:00", "duration_min": 30}, "duration_too_short"),
])
async def test_api_rejects_invalid_schedules_without_touching_the_car(manager, body, reason) -> None:
    async with await _web(manager) as c:
        r = await c.put(f"/api/vehicles/{VIN}/charge/schedule", json=body)
    assert r.status_code == 422 and r.json()["reason"] == reason and manager.fake_car.sent == []


async def test_api_car_error_and_unknown_vehicle(manager) -> None:
    manager.fake_car.response = {"success": False, "message": "Sesión caducada", "reason": "reauth"}
    async with await _web(manager) as c:
        r = await c.post(f"/api/vehicles/{VIN}/charge/stop")
        assert r.status_code == 424 and r.json()["reason"] == "reauth" and r.json()["success"] is False
        assert (await c.post("/api/vehicles/LSJA9999999999999/charge/stop")).status_code == 404


def test_holds_charge_tells_evbro_stops_from_any_other() -> None:
    manager = ChargeManager()
    row = ChargePlan(vin=VIN, mode="hold", hold_reason="user")
    manager._remember_hold(row)
    assert manager.holds_charge(VIN) is True and manager.wants_fast_poll(VIN) is False
    row.hold_reason = "target"
    manager._remember_hold(row)
    assert manager.holds_charge(VIN) is True and manager.wants_fast_poll(VIN) is True
    row.mode, row.hold_reason = "scheduled", None
    manager._remember_hold(row)
    assert manager.holds_charge(VIN) is False
