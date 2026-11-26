"""app/entitlements.py beyond the API: what runs with nobody logged in (timetables, alerts, the manager's own connections) asks the
same question, and an account without the whole history reads only its recent part. Nothing is ever deleted for it."""
from __future__ import annotations

import asyncio
import datetime
import io
import zipfile

import httpx
import pytest
from sqlalchemy import func, select

from app import entitlements
from app.config import get_timezone
from app.db.models import (
    AppUser,
    ChargePlan,
    ChargingSession,
    ClimateSchedule,
    Drive,
    Expense,
    GeofenceEvent,
    MqttLink,
    TraccarLink,
    Vehicle,
)
from app.db.session import async_session_factory
from app.main import app
from app.security import auth, passwords
from app.services import climate_schedule, renewal_alerts
from app.services.charge_manager import ChargeManager
from app.services.mqtt_hub import MqttHub, MqttLinkClient
from app.services.poller import VehiclePoller
from app.services.traccar import TraccarHub
from app.timeutil import utcnow

VIN = "LSJA0000000000061"
OTHER = "LSJA0000000000062"


class _Req:
    client = type("C", (), {"host": "127.0.0.1"})()
    headers = {"user-agent": "t"}


def switch_off(*areas: str, only: str = "ana"):
    """An installation's rule: `only` does not have `areas`; everybody else has everything."""
    async def rule(session, user, feature):
        return not (user.username == only and feature in areas)
    entitlements.set_checker(rule)


@pytest.fixture
async def ana(db, data_dir):
    """Ana (manager, one car) signed in, and Luis (another manager, another car). The rule in force before the test is put back after."""
    before = entitlements._checker
    entitlements.set_checker(None)
    async with async_session_factory() as s:
        user = AppUser(id=2, username="ana", role="manager", password_hash=passwords.hash_password("una-contraseña-larga-1"), vehicle_limit=2, member_limit=2)
        luis = AppUser(id=3, username="luis", role="manager", password_hash=passwords.hash_password("una-contraseña-larga-2"), vehicle_limit=2, member_limit=2)
        s.add_all([user, luis, Vehicle(owner_id=2, vin=VIN, name="Coche de Ana"), Vehicle(owner_id=3, vin=OTHER, name="Coche de Luis")])
        await s.commit()
        token = await auth.create_session(s, user, _Req())
    c = httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://testserver")
    c.cookies.set(auth.COOKIE_NAME, token)
    yield c
    await c.aclose()
    entitlements.set_checker(before)


# ───────────────────────── the recent history ─────────────────────────
async def _history() -> dict:
    """One drive, one charge and one geofence event from last week, and one of each from three months ago."""
    now = utcnow()
    recent, old = now - datetime.timedelta(days=7), now - datetime.timedelta(days=90)
    ids = {}
    async with async_session_factory() as s:
        for label, when in (("recent", recent), ("old", old)):
            d = Drive(vin=VIN, start_time=when, end_time=when + datetime.timedelta(minutes=30), distance_km=12.0, start_address=f"Calle {label}")
            c = ChargingSession(vin=VIN, start_time=when, end_time=when + datetime.timedelta(hours=2), kwh_added=10.0, start_battery_pct=40, end_battery_pct=70)
            g = GeofenceEvent(vin=VIN, geofence_name="Casa", kind="enter", timestamp=when)
            s.add_all([d, c, g])
            await s.flush()
            ids[label] = {"drive": d.id, "charge": c.id}
        await s.commit()
    return ids


async def test_without_the_whole_history_an_account_reads_only_the_recent_part(ana) -> None:
    ids = await _history()
    drives = (await ana.get(f"/api/vehicles/{VIN}/drives")).json()
    assert len(drives["drives"] if isinstance(drives, dict) else drives) == 2            # no rule: everything

    switch_off("history_full")
    status = (await ana.get("/api/auth/status")).json()
    assert "history_full" in status["disabled_features"] and status["history_days"] == 30

    listed = (await ana.get(f"/api/vehicles/{VIN}/drives?period=all")).json()
    rows = listed["drives"] if isinstance(listed, dict) else listed
    assert [d["id"] for d in rows] == [ids["recent"]["drive"]]
    assert [c["id"] for c in (await ana.get(f"/api/vehicles/{VIN}/charges")).json()] == [ids["recent"]["charge"]]
    assert (await ana.get(f"/api/vehicles/{VIN}/charges/summary")).json()["count"] == 1           # totals too
    events = (await ana.get(f"/api/vehicles/{VIN}/timeline")).json()["events"]
    assert sorted(e["kind"] for e in events) == ["charge", "drive", "geofence"]
    assert len((await ana.get(f"/api/geofences/events/{VIN}")).json()) == 1
    # one row by its id: the old ones are not there to be read
    assert (await ana.get(f"/api/drives/{ids['recent']['drive']}")).status_code == 200
    assert (await ana.get(f"/api/drives/{ids['old']['drive']}")).status_code == 404
    assert (await ana.get(f"/api/drives/{ids['old']['drive']}/gpx")).status_code == 404
    assert (await ana.get(f"/api/charges/{ids['recent']['charge']}")).status_code == 200
    assert (await ana.get(f"/api/charges/{ids['old']['charge']}")).status_code == 404
    assert (await ana.get("/api/search?q=Calle")).json()["hits"][0]["id"] == ids["recent"]["drive"]
    assert len((await ana.get("/api/search?q=Calle")).json()["hits"]) == 1
    # sums over any span of days are the whole history's
    r = await ana.get(f"/api/vehicles/{VIN}/journeys")
    assert r.status_code == 403 and r.headers[entitlements.HEADER] == "history_full"


async def test_nothing_is_deleted_and_the_history_comes_back(ana) -> None:
    ids = await _history()
    switch_off("history_full")
    assert (await ana.get(f"/api/drives/{ids['old']['drive']}")).status_code == 404
    async with async_session_factory() as s:                                   # every row is still in the database
        assert (await s.execute(select(func.count(Drive.id)))).scalar_one() == 2
        assert (await s.execute(select(func.count(ChargingSession.id)))).scalar_one() == 2
        assert (await s.execute(select(func.count(GeofenceEvent.id)))).scalar_one() == 2
    with zipfile.ZipFile(io.BytesIO((await ana.get("/api/account/export")).content)) as z:
        exported = z.read("evbro-export.json").decode()                        # the account's own copy has all of it
    assert "Calle old" in exported and "Calle recent" in exported
    entitlements.set_checker(None)                                             # the area comes back: so does the old history
    assert (await ana.get(f"/api/drives/{ids['old']['drive']}")).status_code == 200
    assert len((await ana.get(f"/api/vehicles/{VIN}/charges")).json()) == 2


async def test_changing_data_is_checked_against_the_whole_table(ana) -> None:
    """A request that writes keeps reading everything: the cost of an old charge can still be saved (the limit is only a view)."""
    ids = await _history()
    switch_off("history_full")
    r = await ana.put(f"/api/charges/{ids['old']['charge']}/type", json={"charge_type": "dc"})
    assert r.status_code == 200, r.text
    async with async_session_factory() as s:
        assert (await s.get(ChargingSession, ids["old"]["charge"])).charge_type == "dc"


async def test_the_timeline_and_the_search_leave_out_the_areas_that_are_off(ana) -> None:
    now = utcnow()
    async with async_session_factory() as s:
        s.add_all([Expense(vin=VIN, date=now, title="Peaje de prueba", amount=4.5, category="tolls"),
                   Drive(vin=VIN, start_time=now, end_time=now, distance_km=3.0, start_address="Peaje viejo")])
        await s.commit()
    kinds = {e["kind"] for e in (await ana.get(f"/api/vehicles/{VIN}/timeline")).json()["events"]}
    assert kinds == {"expense", "drive"}
    switch_off("costs")
    assert {e["kind"] for e in (await ana.get(f"/api/vehicles/{VIN}/timeline")).json()["events"]} == {"drive"}
    assert (await ana.get(f"/api/vehicles/{VIN}/timeline?kinds=expense")).json()["events"] == []
    assert {h["kind"] for h in (await ana.get("/api/search?q=Peaje")).json()["hits"]} == {"drive"}


# ───────────────────────── what EVbro does by itself ─────────────────────────
async def test_a_timetable_of_an_account_without_automation_is_not_sent(ana) -> None:
    calls = []

    async def send(vin, temperature):
        calls.append(vin)
        return {"success": True, "message": "ok"}

    now = datetime.datetime(2026, 11, 2, 7, 31, tzinfo=get_timezone())
    async with async_session_factory() as s:
        s.add_all([ClimateSchedule(vin=VIN, time_local="07:30", days="0,1,2,3,4,5,6", temperature=21.0),
                   ClimateSchedule(vin=OTHER, time_local="07:30", days="0,1,2,3,4,5,6", temperature=21.0)])
        await s.commit()
        switch_off("automation")
        assert await climate_schedule.run_due(s, send, now) == 1
        assert calls == [OTHER]                                                # Luis's car, not Ana's
        mine = (await s.execute(select(ClimateSchedule).where(ClimateSchedule.vin == VIN))).scalar_one()
        assert mine.last_run_on is None and mine.enabled                       # kept as it was, for when the area comes back
        entitlements.set_checker(None)
        assert await climate_schedule.run_due(s, send, now) == 1 and calls == [OTHER, VIN]


class _Car:
    def __init__(self):
        self.sent = []

    async def read(self, vin):
        return {"enabled": False, "start": None, "end": None, "duration_min": None}

    async def __call__(self, vin, key, params, origin):
        self.sent.append((vin, key, origin))
        return {"success": True, "message": "ok", "reason": None}


async def test_the_charge_target_of_an_account_without_automation_does_not_pause_the_charge(ana, monkeypatch) -> None:
    car = _Car()
    manager = ChargeManager(sender=car, reader=car.read, verify_delay_s=None)
    monkeypatch.setattr(ChargeManager, "_instance", manager)
    async with async_session_factory() as s:
        s.add_all([ChargePlan(vin=VIN, target_pct=80), ChargePlan(vin=OTHER, target_pct=80)])
        await s.commit()
    switch_off("automation")
    full = {"battery_level": 85, "is_charging": True, "cable_connected": True}
    await manager.on_snapshot(VIN, full)
    assert car.sent == []                                                      # Ana's car goes on charging
    await manager.on_snapshot(OTHER, full)
    assert [(vin, origin) for vin, _key, origin in car.sent] == [(OTHER, "automático")]      # Luis's is paused at its target
    async with async_session_factory() as s:
        assert (await s.get(ChargePlan, VIN)).target_pct == 80                 # the figure is kept


async def test_maintenance_and_expense_alerts_follow_their_areas(ana, monkeypatch) -> None:
    from app.services import maintenance_advice, maintenance_alerts
    from app.services.notification_service import NotificationService
    looked = []

    async def collect(session, vin, odo, now):
        looked.append(("maintenance", vin))
        return []

    async def advice(session, vin, now, odo):
        looked.append(("advice", vin))
        return []

    async def renewals(session, vin, now=None, expenses=True):
        looked.append(("renewals", vin, expenses))
        return []

    async def quiet(*args, **kwargs):
        return []

    monkeypatch.setattr(maintenance_alerts, "collect_alerts", collect)
    monkeypatch.setattr(maintenance_advice, "due_alerts", advice)
    monkeypatch.setattr(renewal_alerts, "collect_renewals", renewals)
    monkeypatch.setattr(NotificationService, "dispatch", classmethod(quiet))
    switch_off("maintenance", "costs")
    poller = VehiclePoller()
    async with async_session_factory() as s:
        for vin in (VIN, OTHER):
            vehicle = (await s.execute(select(Vehicle).where(Vehicle.vin == vin))).scalar_one()
            await poller._check_and_dispatch_alerts(s, vehicle, {"odometer_km": 1000.0, "state": "parked"})
    assert ("maintenance", VIN) not in looked and ("advice", VIN) not in looked
    assert ("maintenance", OTHER) in looked and ("advice", OTHER) in looked
    assert ("renewals", VIN, False) in looked and ("renewals", OTHER, True) in looked


async def test_renewals_without_the_expenses(ana) -> None:
    soon = utcnow() + datetime.timedelta(days=5)
    async with async_session_factory() as s:
        car = (await s.execute(select(Vehicle).where(Vehicle.vin == VIN))).scalar_one()
        car.insurance_renewal_date = soon
        s.add(Expense(vin=VIN, date=utcnow(), title="Impuesto de circulación", amount=60.0, category="tax", next_due_date=soon))
        await s.commit()
        assert len(await renewal_alerts.collect_renewals(s, VIN)) == 2
        only = await renewal_alerts.collect_renewals(s, VIN, expenses=False)
        assert [a["key"].split(":")[0] for a in only] == ["insurance"]         # the date on the car sheet is not an expense


# ───────────────────────── the manager's own connections ─────────────────────────
async def test_mqtt_follows_the_account(ana, monkeypatch) -> None:
    started, stopped = [], []

    async def start(self):
        started.append(self)

    async def stop(self):
        stopped.append(self)

    monkeypatch.setattr(MqttLinkClient, "start", start)
    monkeypatch.setattr(MqttLinkClient, "stop", stop)
    async with async_session_factory() as s:
        s.add_all([MqttLink(user_id=2, host="broker.example", enabled=True), MqttLink(user_id=3, host="broker.example", enabled=True)])
        await s.commit()
    hub = MqttHub()
    switch_off("integrations")
    await hub.start()
    assert set(hub._clients) == {3} and hub.status(2) is None                  # only Luis's broker is connected
    entitlements.set_checker(None)
    monkeypatch.setattr(MqttHub, "_instance", hub)
    monkeypatch.setattr(TraccarHub, "_instance", TraccarHub())
    await entitlements.refresh_services()                                      # no rule: nothing to bring in line
    assert set(hub._clients) == {3}
    switch_off("integrations", only="luis")
    await entitlements.refresh_services()
    assert set(hub._clients) == {2} and len(stopped) == 1                      # Ana's opens, Luis's closes
    await hub.stop()


async def test_traccar_follows_the_account(ana, monkeypatch) -> None:
    async def run(self, key, devices):
        await asyncio.sleep(3600)

    monkeypatch.setattr(TraccarHub, "_run", run)
    async with async_session_factory() as s:
        s.add_all([TraccarLink(vin=VIN, host="traccar.example", api_token="t", device_id=1),
                   TraccarLink(vin=OTHER, host="traccar.example", api_token="t", device_id=2)])
        await s.commit()
    hub = TraccarHub()
    switch_off("integrations")
    await hub.start()
    try:
        assert hub._linked == {OTHER} and [sorted(d.values()) for d in hub._routes.values()] == [[OTHER]]
        tasks = dict(hub._tasks)
        await hub.sync_allowed()
        assert hub._tasks == tasks                                             # nothing changed: the subscriptions are not restarted
        entitlements.set_checker(None)
        await hub.sync_allowed()
        assert hub._linked == {VIN, OTHER}
    finally:
        await hub.stop()


async def test_without_a_rule_nothing_is_looked_up(ana, monkeypatch) -> None:
    """The public installation: no rule, so the background never opens a session to ask."""
    def boom(*args, **kwargs):
        raise AssertionError("no session should be opened to ask")

    monkeypatch.setattr("app.db.session.async_session_factory", boom)
    assert await entitlements.vin_allowed(VIN, "automation") and await entitlements.owner_allowed(2, "integrations")
    await entitlements.refresh_services()
