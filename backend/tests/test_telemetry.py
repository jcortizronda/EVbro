"""Anonymous reports (services/telemetry.py): nothing is collected until the administrator decides, a report carries nothing personal,
errors are grouped by where they happen, and the project's server receives, limits and aggregates the reports."""
from __future__ import annotations

import datetime
import json
import logging

import httpx
import pytest
from sqlalchemy import func, select

from app.admin_app import admin_app
from app.api import routes_admin_auth, routes_auth
from app.config import settings
from app.db.models import (
    AppUser,
    CommandAudit,
    TelemetryErrorGroup,
    TelemetryErrorHit,
    TelemetryInstall,
    TelemetryLocalError,
    Vehicle,
)
from app.db.session import async_session_factory
from app.main import app
from app.security import auth, passwords
from app.services import electricity_tariffs, telemetry

SETUP_CODE = "test-setup-code"
ORIGIN = {"Origin": "http://testserver"}
VIN = "LSJA0000000000061"
PLATE = "1234 KLM"


class _Req:
    client = type("C", (), {"host": "127.0.0.1"})()
    headers = {"user-agent": "t"}


@pytest.fixture(autouse=True)
def _clean(monkeypatch, data_dir):
    monkeypatch.setattr(settings, "SETUP_CODE", SETUP_CODE)
    monkeypatch.setattr(settings, "TELEMETRY_URL", "https://data.example.org")
    monkeypatch.setattr(settings, "TELEMETRY_RECEIVER", False)
    auth.reset_setup_code()
    for t in (routes_auth.ip_throttle, routes_auth.pair_throttle, routes_admin_auth.ip_throttle, routes_admin_auth.pair_throttle):
        t._fails.clear()
    telemetry.set_enabled_cache(False)
    telemetry._state.errors.clear()
    telemetry._state.polls = {"ok": 0, "empty": 0, "error": 0}
    telemetry.install_log_handler()
    yield
    telemetry.set_enabled_cache(False)
    telemetry._state.errors.clear()
    auth.reset_setup_code()


@pytest.fixture
async def world(db):
    """Ana (2) with a car that has a VIN, a plate and a name, two commands; the console admin; Ana's browser session."""
    async with async_session_factory() as s:
        s.add(AppUser(id=2, role="manager", username="ana-secreta", vehicle_limit=-1, password_hash=passwords.hash_password("una-contraseña-larga-1")))
        s.add(Vehicle(owner_id=2, vin=VIN, name="El coche de Ana", brand="Ebro", model="S900 PHEV", fuel_type="phev", license_plate=PLATE))
        await s.flush()
        s.add(CommandAudit(vin=VIN, command="clima_on", success=True, code="0", client_ip="10.1.2.3"))
        s.add(CommandAudit(vin=VIN, command="lock", success=False, code="A00084", message="Calle Mayor 5", client_ip="10.1.2.3"))
        await s.commit()
    console = httpx.AsyncClient(transport=httpx.ASGITransport(app=admin_app), base_url="http://testserver", headers=ORIGIN)
    assert (await console.post("/api/auth/setup", json={"username": "admin", "password": "una-contraseña-larga-1", "setup_code": SETUP_CODE})).status_code == 200
    async with async_session_factory() as s:
        token = await auth.create_session(s, await s.get(AppUser, 2), _Req())
    ana = httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://testserver", headers=ORIGIN)
    ana.cookies.set(auth.COOKIE_NAME, token)
    yield {"console": console, "ana": ana}
    for c in (console, ana):
        await c.aclose()


@pytest.fixture
def in_process_receiver(monkeypatch):
    """The «project's server» is this same application: reports go back into it in process."""
    sent = []

    async def fake(url, payload):
        assert url == "https://data.example.org" + telemetry.REPORT_PATH
        sent.append(payload)
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://testserver") as c:
            r = await c.post(telemetry.REPORT_PATH, content=json.dumps(payload), headers={"Content-Type": "application/json"})
            return r.status_code, r.json()
    monkeypatch.setattr(telemetry, "_http", fake)
    return sent


def _boom():
    raise KeyError(f"vin {VIN} at https://legend.ebroauto.com/api?token=abcdefghijklmnopqrstuvwxyz0123 from ana@example.com")


def test_scrub_keeps_identifiers_and_drops_anything_personal() -> None:
    out = telemetry.scrub(f"KeyError 'battery_level' for {VIN} plate {PLATE} at 40.1234,-3.1234 user 'Ana García' "
                          "mail ana@example.com ip 192.168.1.50 url https://x.example/a?b=c token abcdefghijklmnopqrstuvwxyz0123")
    for secret in (VIN, "1234", "40.1234", "Ana García", "ana@example.com", "192.168", "x.example", "abcdefghijklmnop"):
        assert secret not in out
    assert "'battery_level'" in out and "<vin>" in out and "<email>" in out and "<url>" in out and "'<text>'" in out


async def test_nothing_is_collected_until_the_administrator_decides(world) -> None:
    console = world["console"]
    s = (await console.get("/api/admin/telemetry")).json()
    assert s["decided"] is False and s["enabled"] is None and s["available"] is True and s["receiver"] is False
    logging.getLogger("app.test").error("something failed")
    r = await world["ana"].post("/api/telemetry/client-error", json={"type": "TypeError", "message": "x", "stack": ["js/core/auth.js:10:5 init"]})
    assert r.status_code == 200 and r.json()["collecting"] is False
    assert telemetry._state.errors == {}
    assert (await console.post("/api/admin/telemetry/send")).status_code == 409


async def test_the_preview_is_the_whole_report_and_carries_nothing_personal(world) -> None:
    r = await world["console"].get("/api/admin/telemetry/preview")
    assert r.status_code == 200
    rep = r.json()
    text = json.dumps(rep, ensure_ascii=False)
    for secret in (VIN, PLATE, "El coche de Ana", "ana-secreta", "Calle Mayor", "10.1.2.3"):
        assert secret not in text, secret
    assert rep["format"] == telemetry.FORMAT and rep["size"]["managers"] == "1" and rep["size"]["cars"] == "1"
    assert rep["models"] == [{"model": "S900 PHEV", "fuel_type": "phev", "cars": "1"}]
    cmds = {c["command"]: c for c in rep["commands"]}
    assert cmds["clima_on"]["ok"] == 1 and cmds["lock"] == {"command": "lock", "model": "S900 PHEV", "ok": 0, "failed": 1, "codes": {"A00084": 1}}
    assert rep["features"]["traccar"] is False and isinstance(rep["features"]["web_push"], bool)


async def test_errors_are_grouped_by_place_scrubbed_and_survive_a_flush(world) -> None:
    console = world["console"]
    assert (await console.put("/api/admin/telemetry", json={"enabled": True})).json()["enabled"] is True
    log = logging.getLogger("app.services.poller")
    for _ in range(3):
        try:
            _boom()
        except KeyError:
            log.error("Exception polling", exc_info=True)
    try:
        electricity_tariffs.to_local("not a date")                       # raised inside EVBro's own code
    except AttributeError:
        log.error("tariff", exc_info=True)
    log.warning(f"Car MQTT channel kicked for {VIN}")
    r = await world["ana"].post("/api/telemetry/client-error", json={
        "type": "TypeError", "message": f"Cannot read properties of undefined (reading 'vin') {VIN}",
        "stack": ["js/features/charges.js:120:7 renderCharges", "js/core/navigation.js:44:3 switchTab", "https://evil/x.js:1:1"]})
    assert r.json()["collecting"] is True
    async with async_session_factory() as s:
        assert await telemetry.flush(s) == 4
        rows = {r.kind + ":" + (r.error_type or ""): r for r in (await s.execute(select(TelemetryLocalError))).scalars()}
    boom = rows["server:KeyError"]
    assert boom.count == 3 and boom.frames == []                          # frames of the tests are not EVBro's code: they do not travel
    assert rows["server:AttributeError"].frames[-1].startswith("app/services/electricity_tariffs.py:")
    assert VIN not in (boom.message or "") and "ana@example.com" not in boom.message and "legend.ebroauto" not in boom.message
    assert rows["server:app.services.poller"].level == "warning" and VIN not in rows["server:app.services.poller"].message
    web = rows["web:TypeError"]
    assert web.frames == ["js/core/navigation.js:44 switchTab", "js/features/charges.js:120 renderCharges"]
    assert "'vin'" in web.message and VIN not in web.message


async def test_a_report_travels_end_to_end_and_the_receiver_limits_and_aggregates(world, in_process_receiver, monkeypatch) -> None:
    console = world["console"]
    await console.put("/api/admin/telemetry", json={"enabled": True})
    try:
        _boom()
    except KeyError:
        logging.getLogger("app.x").exception("boom")

    # receiver off: the report is not accepted and the errors wait for the next one
    r = (await console.post("/api/admin/telemetry/send")).json()
    assert r["ok"] is False and r["status"] == 404
    async with async_session_factory() as s:
        assert (await s.execute(select(func.count()).select_from(TelemetryLocalError))).scalar_one() == 1

    monkeypatch.setattr(settings, "TELEMETRY_RECEIVER", True)
    r = (await console.post("/api/admin/telemetry/send")).json()
    assert r["ok"] is True and r["errors_sent"] == 1 and r["state"]["last"]["ok"] is True
    async with async_session_factory() as s:
        assert (await s.execute(select(func.count()).select_from(TelemetryLocalError))).scalar_one() == 0
        inst = (await s.execute(select(TelemetryInstall))).scalar_one()
        assert inst.reports == 1 and "errors" not in inst.report and VIN not in json.dumps(inst.report)
        group = (await s.execute(select(TelemetryErrorGroup))).scalar_one()
        assert group.error_type == "KeyError" and group.total_count == 1
        assert (await s.execute(select(TelemetryErrorHit))).scalar_one().install_id == inst.install_id

    # a second report straight away is refused (one every 30 minutes per installation)
    r = (await console.post("/api/admin/telemetry/send")).json()
    assert r["ok"] is False and r["status"] == 429

    summary = (await console.get("/api/admin/telemetry/received")).json()
    assert summary["installs"]["active_7d"] == 1 and summary["models"] == [{"value": "S900 PHEV · phev", "installs": 1}]
    assert summary["errors"][0]["installs"] == 1 and summary["errors"][0]["resolved"] is False
    gid = summary["errors"][0]["id"]
    assert (await console.post(f"/api/admin/telemetry/errors/{gid}/resolve", json={"resolved": True})).status_code == 200

    async def resend(version, commit):
        async with async_session_factory() as s:
            inst = (await s.execute(select(TelemetryInstall))).scalar_one()
            inst.last_seen = datetime.datetime.utcnow() - datetime.timedelta(hours=2)
            await s.commit()
            report = await telemetry.build_report(s)
        report["server"]["version"], report["server"]["commit"] = version, commit
        report["errors"] = [{**summary["errors"][0], "fingerprint": group.fingerprint, "count": 2}]
        async with async_session_factory() as s:
            await telemetry.receive(s, report)
            return (await s.execute(select(TelemetryErrorGroup))).scalar_one()

    # the same build sends it again: it stays resolved (until 11-11-2026 every resolved error reopened on the next report)
    first = group.versions[-1]
    version, _, commit = first.partition("+")
    g = await resend(version, commit or None)
    assert g.resolved_at is not None and g.total_count == 3
    # a newer build (same version number, another commit) sends it: reopened
    g = await resend(version, "abcdef1234567")
    assert g.resolved_at is None and g.total_count == 5 and g.versions[-1] == f"{version}+abcdef1"
    # resolved again, and a newer version reopens it too
    assert (await console.post(f"/api/admin/telemetry/errors/{gid}/resolve", json={"resolved": True})).status_code == 200
    g = await resend("9.9.9", None)
    assert g.resolved_at is None and g.versions[-1] == "9.9.9"


async def test_the_receiver_refuses_bad_reports(world, monkeypatch) -> None:
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://testserver") as c:
        assert (await c.post(telemetry.REPORT_PATH, json={"format": telemetry.FORMAT})).status_code == 404     # receiver off
        monkeypatch.setattr(settings, "TELEMETRY_RECEIVER", True)
        assert (await c.post(telemetry.REPORT_PATH, json={"format": "other"})).status_code == 422
        assert (await c.post(telemetry.REPORT_PATH, json={"format": telemetry.FORMAT, "install_id": "ana"})).status_code == 422
        assert (await c.post(telemetry.REPORT_PATH, content=b"x" * (telemetry.MAX_REPORT_BYTES + 20_000))).status_code == 413
        ok = await c.post(telemetry.REPORT_PATH, json={"format": telemetry.FORMAT, "install_id": "0b7c1f5e-2a3d-4e5f-8a9b-0c1d2e3f4a5b"})
        assert ok.status_code == 200


async def test_turning_it_off_forgets_what_was_waiting_and_an_empty_url_disables_it(world, monkeypatch) -> None:
    console = world["console"]
    await console.put("/api/admin/telemetry", json={"enabled": True})
    logging.getLogger("app.y").error("pending")
    async with async_session_factory() as s:
        await telemetry.flush(s)
    await console.put("/api/admin/telemetry", json={"enabled": False})
    async with async_session_factory() as s:
        assert (await s.execute(select(func.count()).select_from(TelemetryLocalError))).scalar_one() == 0
    monkeypatch.setattr(settings, "TELEMETRY_URL", "")
    assert (await console.put("/api/admin/telemetry", json={"enabled": True})).status_code == 409
    assert (await console.get("/api/admin/telemetry")).json()["available"] is False


def test_warnings_about_the_installation_or_an_account_are_not_reported(monkeypatch) -> None:
    """A new encryption key or a password the Ebro cloud rejects is for whoever runs the server, not a fault in the code."""
    import logging

    monkeypatch.setattr(telemetry._state, "enabled", True)
    monkeypatch.setattr(telemetry._state, "errors", {})
    handler = telemetry.TelemetryLogHandler()
    log = logging.getLogger("app.services.example")
    log.addHandler(handler)
    try:
        log.warning("The Ebro cloud rejected the stored password", extra={"telemetry": False})
        assert telemetry._state.errors == {}
        log.warning("Read of 123456 brought no data: exception (timeout)")
        assert len(telemetry._state.errors) == 1
    finally:
        log.removeHandler(handler)


def test_reads_without_data_are_grouped_by_cause(monkeypatch) -> None:
    """A read without data used to be one group for every cause: the console showed the last message (a DNS failure) over hundreds
    of expired sessions. Each cause is now a group of its own, and a session the cloud closed is not reported."""
    import logging

    from app.services.poller import read_failure_group

    assert read_failure_group({"reason": "no_usertoken"}) == "session"
    assert read_failure_group({"reason": "exception", "error": "HTTPSConnectionPool(...): Max retries exceeded (NameResolutionError)"}) == "network"
    assert read_failure_group({"reason": "exception", "error": "KeyError: 'data'"}) == "exception"
    assert read_failure_group({"reason": "busy"}) == "busy"

    monkeypatch.setattr(telemetry._state, "enabled", True)
    monkeypatch.setattr(telemetry._state, "errors", {})
    handler = telemetry.TelemetryLogHandler()
    log = logging.getLogger("app.services.example")
    log.addHandler(handler)
    try:
        for group in ("network", "exception", "network"):
            log.warning("Read of 123456 brought no data: exception", extra={"telemetry_group": group})
        counts = {e["location"].rsplit("[", 1)[-1].rstrip("]"): e["count"] for e in telemetry._state.errors.values()}
        assert set(counts) == {"network", "exception"}
        assert counts["network"] == 2 * counts["exception"]          # the root logger may hold the app's handler as well
    finally:
        log.removeHandler(handler)


def test_a_wrong_pin_is_not_reported(monkeypatch) -> None:
    """core/taskid.py logs checkPassword's raw answer; a wrong PIN (A07908/A07909) is the person's typo, any other answer is kept."""
    import logging

    monkeypatch.setattr(telemetry._state, "enabled", True)
    monkeypatch.setattr(telemetry._state, "errors", {})
    handler = telemetry.TelemetryLogHandler()
    log = logging.getLogger("app.core.taskid")
    log.addHandler(handler)
    try:
        log.warning("[taskId] checkPassword NO devolvió un taskId → %s (respuesta cruda)", "code=1 'A07908'")
        log.warning("[taskId] checkPassword NO devolvió un taskId → %s (respuesta cruda)", "code=1 'A07909'")
        assert telemetry._state.errors == {}
        log.warning("[taskId] checkPassword NO devolvió un taskId → %s (respuesta cruda)", "code=1 'A00123'")
        assert len(telemetry._state.errors) == 1
    finally:
        log.removeHandler(handler)
