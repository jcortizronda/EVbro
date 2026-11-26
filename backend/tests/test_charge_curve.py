"""services/charge_curve.py: the energy of a charge added up from its power readings, and when that sum can be trusted."""
from __future__ import annotations

import datetime
from types import SimpleNamespace

import pytest

from app.services import charge_curve

T0 = datetime.datetime(2026, 11, 20, 22, 0)


def readings(powers, every_s=120, start=T0):
    return [SimpleNamespace(timestamp=start + datetime.timedelta(seconds=i * every_s), power_kw=p) for i, p in enumerate(powers)]


def test_a_steady_charge_adds_up_to_power_times_time() -> None:
    pts = readings([6.6] * 61)                                   # two hours at 6.6 kW, a reading every two minutes
    out = charge_curve.energy(pts, T0, T0 + datetime.timedelta(hours=2), gain_pct=40.0)
    assert out == {"kwh": pytest.approx(13.2), "coverage": 1.0, "reliable": True, "capacity_kwh": pytest.approx(33.0)}


def test_the_ramp_down_at_the_end_counts_for_what_it_is() -> None:
    pts = readings([6.0] * 31 + [3.0, 0.0])                      # one hour at 6 kW, then two readings going down to nothing
    out = charge_curve.energy(pts, T0, T0 + datetime.timedelta(minutes=64))
    assert out["kwh"] == pytest.approx(6.0 + 4.5 * 2 / 60 + 1.5 * 2 / 60, abs=0.01) and out["capacity_kwh"] is None      # no gain given


def test_a_hole_in_the_readings_is_left_out_and_makes_the_sum_unreliable() -> None:
    first = readings([6.0] * 16)                                 # half an hour…
    later = readings([6.0] * 16, start=T0 + datetime.timedelta(hours=1, minutes=30))      # …an hour with no readings, half an hour more
    out = charge_curve.energy(first + later, T0, T0 + datetime.timedelta(hours=2), gain_pct=50.0)
    assert out["kwh"] == pytest.approx(6.0) and out["coverage"] == 0.5          # only what was seen: 2 × 30 min at 6 kW
    assert out["reliable"] is False and out["capacity_kwh"] is None


def test_too_little_to_say_anything() -> None:
    end = T0 + datetime.timedelta(hours=1)
    assert charge_curve.energy([], T0, end) is None
    assert charge_curve.energy(readings([6.0]), T0, end) is None
    assert charge_curve.energy(readings([None, None, None]), T0, end) is None
    assert charge_curve.energy(readings([0.0, 0.0, 0.0]), T0, end) is None       # readings, but no power in them
    assert charge_curve.energy(readings([6.0, 6.0]), T0, None) is None           # a charge still going on
    few = charge_curve.energy(readings([6.0, 6.0, 6.0]), T0, T0 + datetime.timedelta(minutes=4))
    assert few["reliable"] is False                                              # three readings are not a curve


def test_a_small_gain_says_nothing_about_the_whole_battery_and_discharge_is_not_energy_in() -> None:
    pts = readings([6.0] * 31)
    assert charge_curve.energy(pts, T0, T0 + datetime.timedelta(hours=1), gain_pct=10.0)["capacity_kwh"] is None
    neg = charge_curve.energy(readings([-2.0] * 10 + [6.0] * 21), T0, T0 + datetime.timedelta(hours=1))
    assert neg["kwh"] < 6.0 * 42 / 60 + 0.01 and neg["kwh"] > 3.9                # the negative readings count as zero


async def test_the_detail_of_a_charge_carries_the_measured_figure_beside_the_stored_one(db, data_dir) -> None:
    import httpx

    from app.db.models import AppUser, ChargePoint, ChargingSession, Vehicle
    from app.db.session import async_session_factory
    from app.main import app
    from app.security import auth, passwords

    class _Req:
        client = type("C", (), {"host": "127.0.0.1"})()
        headers = {"user-agent": "t"}

    vin = "LSJA0000000000081"
    end = T0 + datetime.timedelta(hours=2)
    async with async_session_factory() as s:
        user = AppUser(id=1, role="manager", username="juan", vehicle_limit=-1, password_hash=passwords.hash_password("una-contraseña-larga-1"))
        s.add_all([user, Vehicle(owner_id=1, vin=vin, name="Ebro", battery_capacity_kwh=30.0)])
        seen = ChargingSession(vin=vin, start_time=T0, end_time=end, start_battery_pct=40, end_battery_pct=80, kwh_added=12.0)
        holes = ChargingSession(vin=vin, start_time=T0, end_time=end, start_battery_pct=40, end_battery_pct=80, kwh_added=12.0)
        s.add_all([seen, holes])
        await s.flush()
        s.add_all([ChargePoint(charging_session_id=seen.id, timestamp=p.timestamp, battery_pct=50.0, power_kw=p.power_kw) for p in readings([6.6] * 61)])
        s.add_all([ChargePoint(charging_session_id=holes.id, timestamp=p.timestamp, battery_pct=50.0, power_kw=p.power_kw) for p in readings([6.6] * 10)])
        await s.commit()
        token = await auth.create_session(s, user, _Req())
        ids = (seen.id, holes.id)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://testserver") as c:
        c.cookies.set(auth.COOKIE_NAME, token)
        full = (await c.get(f"/api/charges/{ids[0]}")).json()["charge"]
        assert full["kwh_added"] == 12.0                                         # the stored figure (and the cost with it) is not touched
        assert full["kwh_curve"] == pytest.approx(13.2) and full["curve_coverage"] == 1.0 and full["curve_capacity_kwh"] == pytest.approx(33.0)
        partial = (await c.get(f"/api/charges/{ids[1]}")).json()["charge"]
        assert partial["kwh_curve"] is None and partial["curve_coverage"] == 0.15 and partial["curve_capacity_kwh"] is None
