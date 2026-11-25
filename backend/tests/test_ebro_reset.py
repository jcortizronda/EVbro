"""Resetting the Ebro password or PIN with a code by SMS (core/ebro_reset.py, /api/account/ebro-reset/*). The cloud refuses the SMS
without the puzzle captcha (seen on 24-11-2026); the rest is read from the official app and not yet tried against the real cloud: these
tests pin the requests and what EVBro does with each answer."""
from __future__ import annotations

import base64
import hashlib
import json

import httpx
import pytest
from Crypto.Cipher import AES
from Crypto.Util.Padding import unpad
from sqlalchemy import select

from app.api import routes_account
from app.core import ebro_auth, ebro_reset, taskid
from app.core.context import CoreCtx
from app.core.errors import CommandError
from app.db.models import AppUser, EbroAccount, Vehicle
from app.db.session import async_session_factory
from app.main import app
from app.security import auth, passwords
from app.services import accounts
from app.services.poller import VehiclePoller

VIN = "LSJA0000000000091"
KEY = "abcdefghijklmnop"


def _decrypt(value: str, key: str = KEY) -> str:
    return unpad(AES.new(key.encode(), AES.MODE_ECB).decrypt(base64.b64decode(value)), 16).decode()


class _Answer:
    status_code = 200
    text = ""

    def __init__(self, body):
        self._body = body

    def json(self):
        return self._body


@pytest.fixture
def ctx(tmp_path):
    token = tmp_path / "token.json"
    token.write_text(json.dumps({"access_token": "acc-1"}))
    return CoreCtx(vin=VIN, tsp_host="https://tsp.example", bff="https://bff.example/api", token_path=str(token))


def test_the_captcha_is_answered_as_the_app_does(ctx, monkeypatch) -> None:
    sent = []

    def post(url, params=None, headers=None, data=None, timeout=None):
        sent.append((url, params, headers))
        if url.endswith("/code/create"):
            return _Answer({"code": 0, "data": {"repCode": "0000", "repData": {"originalImageBase64": "BG", "jigsawImageBase64": "PC",
                                                                                "token": "TK", "secretKey": KEY}}})
        return _Answer({"code": 0, "data": {"repCode": "0000"}})

    monkeypatch.setattr(ebro_reset.requests, "post", post)
    assert ebro_reset.captcha_create(ctx) == {"background": "BG", "piece": "PC", "token": "TK", "secret_key": KEY}
    assert sent[-1][0] == "https://bff.example/api/code/create" and sent[-1][1] == {"captchaType": "blockPuzzle"}
    proof = ebro_reset.captcha_check(ctx, "TK", KEY, 123.5)
    url, params, headers = sent[-1]
    assert url.endswith("/code/check") and params["token"] == "TK" and params["captchaType"] == "blockPuzzle"
    assert _decrypt(params["pointJson"]) == '{"x":123.5,"y":5}'
    assert _decrypt(proof) == 'TK---{"x":123.5,"y":5}'
    assert headers["Authorization"] == "Bearer acc-1"

    monkeypatch.setattr(ebro_reset.requests, "post", lambda *a, **kw: _Answer({"code": 0, "data": {"repCode": "6111"}}))
    assert ebro_reset.captcha_check(ctx, "TK", KEY, 10) is None                    # the piece did not fit
    assert ebro_reset.captcha_encrypt("plain", "") == "plain"                        # no key: the text as it is


def test_the_sms_request_is_signed_with_its_three_fields(ctx, monkeypatch) -> None:
    sent = {}

    def post(url, headers=None, files=None, timeout=None):
        sent.update(url=url, headers=headers, form={k: v[1] for k, v in files.items()})
        return _Answer({"code": 0})

    monkeypatch.setattr(ebro_reset.requests, "post", post)
    assert ebro_reset.succeeded(ebro_reset.send_sms_code(ctx, "600000001", "34", ebro_reset.MODULE_PIN, "PROOF"))
    h = sent["headers"]
    assert sent["url"] == "https://bff.example/api/marketing/v2/app/code/sendSmsCode"
    assert sent["form"] == {"mobile": "600000001", "module": "TSP-UPDATE-PASSWD", "areaCode": "34", "captchaVerification": "PROOF"}
    assert (h["nonce"], h["url"], h["keys"]) == ("chery_legend_marketing", "marketing/v2/app/code/sendSmsCode", "mobile,module,areaCode")
    base = f"{ebro_auth.SIGN_SECRET}chery_legend_marketing marketing/v2/app/code/sendSmsCode{h['timestamp']}[600000001,TSP-UPDATE-PASSWD,34]"
    assert h["signature"] == hashlib.sha256(base.replace(" ", "").encode()).hexdigest()
    assert "Content-Type" not in h and h["Authorization"] == "Bearer acc-1"


def test_the_pin_and_password_resets_send_what_the_app_sends(ctx, monkeypatch) -> None:
    calls = []

    def bff_post(ctx_, path, body, headers=None, params=None, **kw):
        calls.append((path, body, headers, params))
        return {"code": 0}

    monkeypatch.setattr(ebro_reset, "bff_post", bff_post)
    check, answer = ebro_reset.reset_pin(ctx, "42", "600000001", "34", "123456", "654321")
    assert ebro_reset.succeeded(check) and ebro_reset.succeeded(answer)
    assert calls[0][:2] == ("/tsp/v1/app/cpm/smsCheck", {"phone": "600000001", "captcha": "123456", "areaCode": "34", "module": "TSP-UPDATE-PASSWD"})
    assert calls[1][:2] == ("/tsp/v1/app/cpm/retrievePassword", {"tUserId": "42", "channelId": "4", "vin": VIN,
                                                                 "password": taskid.encode_pin("654321"), "needDecode": 0})

    calls.clear()
    monkeypatch.setattr(ebro_reset, "bff_post", lambda *a, **kw: calls.append(a) or {"code": 1, "msg": "Código incorrecto"})
    check, answer = ebro_reset.reset_pin(ctx, "42", "600000001", "34", "000000", "654321")
    assert answer is None and len(calls) == 1 and ebro_reset.refusal(check) == "Código incorrecto"     # a wrong code stops there

    calls.clear()
    monkeypatch.setattr(ebro_reset, "bff_post", bff_post)
    ebro_reset.reset_password(ctx, "600000001", "34", "123456", "Nueva#2026")
    path, body, headers, params = calls[0]
    assert path == "/marketing/v1/user/user/passwd" and body == {}
    assert list(params) == ["mobile", "code", "needDecode", "password", "areaCode"]
    assert params["code"] == ebro_auth.sm4_code("123456") and params["needDecode"] == 0 and params["areaCode"] == "34"
    assert (headers["nonce"], headers["url"], headers["keys"]) == ("chery_legend_marketing", "/v1/user/user/passwd", "areaCode,mobile")
    base = f"{ebro_auth.SIGN_SECRET}chery_legend_marketing/v1/user/user/passwd{headers['timestamp']}[34,600000001]"
    assert headers["signature"] == hashlib.sha256(base.encode()).hexdigest()


def test_the_services_only_ask_for_the_sms_when_the_piece_fits(ctx, monkeypatch) -> None:
    monkeypatch.setattr(accounts, "_bff_login", lambda ctx_: ("ut", "42"))
    sms = []
    monkeypatch.setattr(ebro_reset, "send_sms_code", lambda *a: sms.append(a) or {"code": 0})
    monkeypatch.setattr(ebro_reset, "captcha_check", lambda ctx_, token, key, x: None)
    assert accounts.reset_send_sms(ctx, "600000001", "34", "TSP-UPDATE-PASSWD", "TK", KEY, 10)["captcha_failed"] is True
    assert sms == []
    monkeypatch.setattr(ebro_reset, "captcha_check", lambda ctx_, token, key, x: "PROOF")
    assert accounts.reset_send_sms(ctx, "600000001", "34", "TSP-UPDATE-PASSWD", "TK", KEY, 10)["sent"] is True
    assert sms[0][-1] == "PROOF"


def test_the_pin_reset_is_believed_when_the_new_pin_works(ctx, monkeypatch) -> None:
    monkeypatch.setattr(accounts, "_bff_login", lambda ctx_: ("ut", "42"))
    monkeypatch.setattr(ebro_reset, "reset_pin", lambda *a: ({"code": 0}, {"code": 0}))
    proved = []
    monkeypatch.setattr(taskid, "mint_with_pin", lambda ctx_, tuid, pin: proved.append(pin) or "T")
    assert accounts.reset_pin_with_code(ctx, "600000001", "34", "123456", "654321")["changed"] is True and proved == ["654321"]

    def refused(ctx_, tuid, pin):
        raise CommandError("PIN rechazado", reason="pin")

    monkeypatch.setattr(taskid, "mint_with_pin", refused)
    assert accounts.reset_pin_with_code(ctx, "600000001", "34", "123456", "654321")["unconfirmed"] is True
    monkeypatch.setattr(ebro_reset, "reset_pin", lambda *a: ({"code": 1, "msg": "Código incorrecto"}, None))
    out = accounts.reset_pin_with_code(ctx, "600000001", "34", "000000", "654321")
    assert out == {"changed": False, "unconfirmed": False, "reason": "Código incorrecto"}


# ───────────────────────── the routes ─────────────────────────
@pytest.fixture(autouse=True)
def _clean():
    for t in (routes_account.ebro_sms_throttle, routes_account.ebro_code_throttle):
        t._fails.clear()
    routes_account._pending_captcha.clear()
    yield
    routes_account._pending_captcha.clear()


@pytest.fixture
async def web(db, tmp_path, monkeypatch):
    from app.config import settings
    from app.services.car_events import CarEventHub

    monkeypatch.setattr(settings, "DATA_DIR", str(tmp_path))

    async def no_restart(self, owner_id, poller):
        return None

    monkeypatch.setattr(CarEventHub, "restart_owner", no_restart)

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


async def _account() -> EbroAccount:
    async with async_session_factory() as s:
        return (await s.execute(select(EbroAccount).where(EbroAccount.id == 3))).scalar_one()


async def test_the_secret_key_stays_on_the_server_and_each_puzzle_is_good_for_one_try(web, monkeypatch) -> None:
    monkeypatch.setattr(routes_account, "reset_captcha", lambda ctx: {"background": "BG", "piece": "PC", "token": "TK", "secret_key": KEY})
    r = await web.post("/api/account/ebro-reset/captcha", json={"purpose": "pin"})
    assert r.status_code == 200 and r.json() == {"background": "BG", "piece": "PC", "width": 310}
    seen = []
    monkeypatch.setattr(routes_account, "reset_send_sms", lambda ctx, phone, area, module, token, key, x:
                        seen.append((module, token, key, x)) or {"sent": False, "captcha_failed": True, "reason": None})
    assert (await web.post("/api/account/ebro-reset/sms", json={"x": 120.5})).status_code == 422
    assert seen == [("TSP-UPDATE-PASSWD", "TK", KEY, 120.5)]
    assert (await web.post("/api/account/ebro-reset/sms", json={"x": 120.5})).status_code == 409      # already used: ask for another

    await web.post("/api/account/ebro-reset/captcha", json={"purpose": "password"})
    monkeypatch.setattr(routes_account, "reset_send_sms", lambda ctx, phone, area, module, token, key, x:
                        seen.append((module,)) or {"sent": True, "captcha_failed": False, "reason": None})
    r = await web.post("/api/account/ebro-reset/sms", json={"x": 50})
    assert r.status_code == 200 and seen[-1] == ("APP-UPDATE-PASSWD",) and "600000001" not in r.text


async def test_three_sms_in_ten_minutes_at_most(web, monkeypatch) -> None:
    monkeypatch.setattr(routes_account, "reset_captcha", lambda ctx: {"background": "BG", "piece": "PC", "token": "TK", "secret_key": KEY})
    monkeypatch.setattr(routes_account, "reset_send_sms", lambda *a: {"sent": True, "captcha_failed": False, "reason": None})
    for _ in range(3):
        assert (await web.post("/api/account/ebro-reset/captcha", json={"purpose": "pin"})).status_code == 200
        assert (await web.post("/api/account/ebro-reset/sms", json={"x": 50})).status_code == 200
    assert (await web.post("/api/account/ebro-reset/captcha", json={"purpose": "pin"})).status_code == 429


async def test_a_pin_reset_updates_evbros_copy(web, monkeypatch) -> None:
    calls = []
    monkeypatch.setattr(routes_account, "reset_pin_with_code", lambda ctx, phone, area, code, new:
                        calls.append((ctx.vin, code, new)) or {"changed": True, "unconfirmed": False, "reason": None})
    r = await web.post("/api/account/ebro-reset/pin", json={"vin": VIN, "code": "123456", "new_pin": "654321"})
    assert r.status_code == 200 and r.json()["stored_pin_updated"] is True and calls == [(VIN, "123456", "654321")]
    assert (await _account()).pin == "654321"
    assert (await web.post("/api/account/ebro-reset/pin", json={"vin": VIN, "code": "123456", "new_pin": "6543"})).status_code == 422


async def test_wrong_codes_wait_and_a_good_password_reset_keeps_the_new_one(web, monkeypatch) -> None:
    monkeypatch.setattr(routes_account, "reset_password_with_code", lambda *a: {"changed": False, "reason": "Código incorrecto", "token_data": None})
    for _ in range(3):
        r = await web.post("/api/account/ebro-reset/password", json={"code": "000000", "new_password": "Nueva#2026"})
        assert r.status_code == 424 and "Código incorrecto" in r.json()["detail"]
    assert (await web.post("/api/account/ebro-reset/password", json={"code": "000000", "new_password": "Nueva#2026"})).status_code == 429
    assert (await _account()).password == "Vieja-1234"

    routes_account.ebro_code_throttle._fails.clear()
    monkeypatch.setattr(routes_account, "reset_password_with_code", lambda *a: {"changed": True, "reason": None, "token_data": {"access_token": "n"}})
    r = await web.post("/api/account/ebro-reset/password", json={"code": "123456", "new_password": "Nueva#2026"})
    assert r.status_code == 200 and r.json()["session_renewed"] is True
    acc = await _account()
    assert acc.password == "Nueva#2026" and acc.token_data == {"access_token": "n"}
    assert (await web.post("/api/account/ebro-reset/password", json={"code": "123456", "new_password": "corta"})).status_code == 422
