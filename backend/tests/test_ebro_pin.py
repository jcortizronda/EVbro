"""Changing the car-control PIN of the Ebro account in the cloud (core/ebro_account.py, POST /api/account/ebro-pin), which is not the
PIN stored in EVbro (POST /api/account/pin, only EVbro's copy). Read from the official app and tried against the real cloud on 24-11-2026
(it works with the taskId of the car settings, `scene = 2`): these tests pin the request and what EVbro does with each answer."""
from __future__ import annotations

import json

import httpx
import pytest
from sqlalchemy import select

from app.api import routes_account
from app.core import ebro_account, taskid, vehicles
from app.core.context import CoreCtx
from app.core.errors import CommandError
from app.db.models import AppUser, EbroAccount, Vehicle
from app.db.session import async_session_factory
from app.main import app
from app.security import auth, passwords
from app.services import accounts
from app.services.poller import VehiclePoller

VIN = "LSJA0000000000071"


def _ctx(tmp_path) -> CoreCtx:
    token = tmp_path / "token.json"
    token.write_text(json.dumps({"access_token": "acc-1"}))
    return CoreCtx(vin=VIN, pin="111111", tsp_host="https://tsp.example", bff="https://bff.example/api", token_path=str(token))


@pytest.mark.parametrize("pin, ok", [("123456", True), ("12345", False), ("1234567", False), ("12a456", False), ("١٢٣٤٥٦", False)])
def test_the_new_pin_has_six_digits(pin, ok) -> None:
    assert ebro_account.pin_format_ok(pin) is ok


def test_the_change_proves_the_typed_pin_and_sends_the_new_one_as_the_app_does(tmp_path, monkeypatch) -> None:
    ctx = _ctx(tmp_path)
    calls = []

    def bff_post(ctx_, path, body, headers=None, **kw):
        calls.append((path, body, headers))
        return {"code": 0, "data": {"taskId": "T-1"}} if path.endswith("checkPassword") else {"code": 0}

    monkeypatch.setattr(taskid, "bff_post", bff_post)
    monkeypatch.setattr(ebro_account, "bff_post", bff_post)
    monkeypatch.setattr(vehicles, "query_list", lambda ctx_: {})
    answer = ebro_account.change_pin(ctx, "42", "222222", "333333")
    assert ebro_account.pin_change_looks_accepted(answer)
    check = next(b for p, b, _ in calls if p.endswith("checkPassword"))
    assert check["password"] == taskid.encode_pin("222222")                 # the typed PIN, not the stored one
    assert check["scene"] == 2                    # the car settings' taskId: one for commands (scene 0) got A00089 (24-11-2026)
    path, body, headers = calls[-1]
    assert path == "/tsp/v1/app/cpm/changePassword" and headers["Authorization"] == "Bearer acc-1"
    assert body == {"tUserId": "42", "channelId": "4", "language": "ES", "vin": VIN, "taskId": "T-1",
                    "newPassword": taskid.encode_pin("333333"), "needDecode": 0, "type": 0}
    assert ctx.pin == "111111"                                              # the context's PIN is not touched


def test_a_wrong_current_pin_stops_before_the_change_and_counts_for_the_lockout(tmp_path, monkeypatch) -> None:
    ctx = _ctx(tmp_path)
    sent = []
    monkeypatch.setattr(taskid, "bff_post", lambda ctx_, path, body, headers=None, **kw:
                        {"code": 1, "message": "A07908"} if path.endswith("checkPassword") else {"code": 0})
    monkeypatch.setattr(ebro_account, "bff_post", lambda *a, **kw: sent.append(a) or {"code": 0})
    monkeypatch.setattr(vehicles, "query_list", lambda ctx_: {})
    with pytest.raises(CommandError):
        ebro_account.change_pin(ctx, "42", "999999", "333333")
    assert sent == [] and ctx.lockout.failed_attempts == 1


def test_the_service_trusts_the_change_only_when_the_new_pin_works(tmp_path, monkeypatch) -> None:
    ctx = _ctx(tmp_path)
    monkeypatch.setattr(accounts, "_bff_login", lambda ctx_: ("ut", "42"))
    proved = []

    def mint(ctx_, tuid, pin):
        proved.append(pin)
        if pin == "000000":
            raise CommandError("PIN rechazado", reason="pin")
        return "T-2"

    monkeypatch.setattr(taskid, "mint_with_pin", mint)
    monkeypatch.setattr(ebro_account, "change_pin", lambda ctx_, tuid, cur, new: {"code": 1, "msg": "No"})
    assert accounts.change_account_pin(ctx, "222222", "333333") == {"changed": False, "unconfirmed": False, "reason": "No", "code": 1}
    assert proved == []

    monkeypatch.setattr(ebro_account, "change_pin", lambda ctx_, tuid, cur, new: {"code": "0"})
    assert accounts.change_account_pin(ctx, "222222", "333333")["changed"] is True
    assert proved == ["333333"]
    out = accounts.change_account_pin(ctx, "222222", "000000")
    assert out["changed"] is False and out["unconfirmed"] is True

    monkeypatch.setattr(accounts, "_bff_login", lambda ctx_: (None, None))
    with pytest.raises(RuntimeError, match="no_usertoken"):
        accounts.change_account_pin(ctx, "222222", "333333")


# ───────────────────────── the route ─────────────────────────
@pytest.fixture(autouse=True)
def _clean_throttle():
    routes_account.ebro_pin_throttle._fails.clear()
    yield
    routes_account.ebro_pin_throttle._fails.clear()


@pytest.fixture
async def web(db, tmp_path, monkeypatch):
    from app.config import settings

    monkeypatch.setattr(settings, "DATA_DIR", str(tmp_path))

    class _Req:
        client = type("C", (), {"host": "127.0.0.1"})()
        headers = {"user-agent": "t"}

    async with async_session_factory() as s:
        user = AppUser(id=1, role="manager", username="juan", vehicle_limit=-1, password_hash=passwords.hash_password("una-contraseña-larga-1"))
        s.add(user)
        s.add(EbroAccount(id=3, user_id=1, phone="600000001", password="Vieja-1234", pin="222222", area_code="34", is_active=True))
        s.add(Vehicle(vin=VIN, owner_id=1, name="Coche"))
        await s.commit()
        token = await auth.create_session(s, user, _Req())
    VehiclePoller.get_instance()._contexts.pop(VIN, None)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://testserver") as client:
        client.cookies.set(auth.COOKIE_NAME, token)
        yield client
    VehiclePoller.get_instance()._contexts.pop(VIN, None)


def _cloud(monkeypatch, outcome=None, raises=None):
    calls = []

    def change(ctx, current, new):
        calls.append((ctx.vin, current, new))
        if raises:
            raise raises
        return outcome

    monkeypatch.setattr(routes_account, "change_account_pin", change)
    return calls


async def _stored_pin() -> str:
    async with async_session_factory() as s:
        return (await s.execute(select(EbroAccount).where(EbroAccount.id == 3))).scalar_one().pin


def _body(**kw):
    return {"vin": VIN, "current_pin": "222222", "new_pin": "333333", **kw}


async def test_a_change_the_cloud_takes_updates_evbros_copy(web, monkeypatch) -> None:
    calls = _cloud(monkeypatch, {"changed": True, "unconfirmed": False, "reason": None, "code": 0})
    r = await web.post("/api/account/ebro-pin", json=_body())
    assert r.status_code == 200 and r.json() == {"status": "ok", "stored_pin_updated": True}
    assert calls == [(VIN, "222222", "333333")]
    assert await _stored_pin() == "333333"
    assert VehiclePoller.get_instance()._contexts[VIN].pin == "333333"


async def test_without_a_stored_pin_nothing_is_stored(web, monkeypatch) -> None:
    async with async_session_factory() as s:
        (await s.execute(select(EbroAccount).where(EbroAccount.id == 3))).scalar_one().pin = None
        await s.commit()
    _cloud(monkeypatch, {"changed": True, "unconfirmed": False, "reason": None, "code": 0})
    r = await web.post("/api/account/ebro-pin", json=_body(current_pin="555555"))
    assert r.status_code == 200 and r.json()["stored_pin_updated"] is False
    assert await _stored_pin() is None


async def test_refusals_change_nothing(web, monkeypatch) -> None:
    _cloud(monkeypatch, {"changed": False, "unconfirmed": False, "reason": "No permitido", "code": 1})
    r = await web.post("/api/account/ebro-pin", json=_body())
    assert r.status_code == 424 and "No permitido" in r.json()["detail"]
    routes_account.ebro_pin_throttle._fails.clear()
    _cloud(monkeypatch, {"changed": False, "unconfirmed": True, "reason": "PIN rechazado", "code": 0})
    r = await web.post("/api/account/ebro-pin", json=_body())
    assert r.status_code == 424 and "todavía no acepta el PIN nuevo" in r.json()["detail"]
    routes_account.ebro_pin_throttle._fails.clear()
    _cloud(monkeypatch, raises=CommandError("PIN de comandos rechazado", reason="pin"))
    assert (await web.post("/api/account/ebro-pin", json=_body())).status_code == 424
    assert await _stored_pin() == "222222"


async def test_bad_input_never_reaches_the_cloud(web, monkeypatch) -> None:
    calls = _cloud(monkeypatch, {"changed": True, "unconfirmed": False, "reason": None, "code": 0})
    for body, status in ((_body(new_pin="12345"), 422), (_body(new_pin="222222"), 422), (_body(current_pin="abc"), 422),
                         (_body(vin="LSJA0000000000099"), 404)):
        assert (await web.post("/api/account/ebro-pin", json=body)).status_code == status
    assert calls == []


async def test_the_cloud_checks_the_current_pin_not_evbros_copy_and_two_failures_wait(web, monkeypatch) -> None:
    """24-11-2026: the current PIN had to match the one stored in EVbro, and that got in the way; the cloud checks it now."""
    calls = _cloud(monkeypatch, {"changed": True, "unconfirmed": False, "reason": None, "code": 0})
    r = await web.post("/api/account/ebro-pin", json=_body(current_pin="999999"))       # not the stored 222222
    assert r.status_code == 200 and calls == [(VIN, "999999", "333333")]
    assert await _stored_pin() == "333333"                                               # the copy follows the cloud

    calls = _cloud(monkeypatch, raises=CommandError("PIN de comandos rechazado", reason="pin"))
    assert (await web.post("/api/account/ebro-pin", json=_body(current_pin="111111"))).status_code == 424
    assert (await web.post("/api/account/ebro-pin", json=_body(current_pin="111112"))).status_code == 424
    assert (await web.post("/api/account/ebro-pin", json=_body(current_pin="111113"))).status_code == 429
    assert len(calls) == 2


async def test_a_manual_car_is_not_an_ebro_car(web, monkeypatch) -> None:
    calls = _cloud(monkeypatch, {"changed": True, "unconfirmed": False, "reason": None, "code": 0})
    async with async_session_factory() as s:
        (await s.execute(select(Vehicle).where(Vehicle.vin == VIN))).scalar_one().is_manual = True
        await s.commit()
    assert (await web.post("/api/account/ebro-pin", json=_body())).status_code == 404
    assert calls == []



async def test_the_stored_pin_is_the_ebro_pin_six_digits_or_nothing(web) -> None:
    """The official app's PIN has 6 digits; EVbro said «4 a 6» and saved anything (24-11-2026)."""
    for bad in ("1234", "12345a", "1234567"):
        assert (await web.post("/api/account/pin", json={"pin": bad})).status_code == 422
    assert await _stored_pin() == "222222"
    assert (await web.post("/api/account/pin", json={"pin": "654321"})).status_code == 200
    assert (await web.post("/api/account/pin", json={"pin": ""})).status_code == 200
    assert await _stored_pin() is None
