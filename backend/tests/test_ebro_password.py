"""Changing the password of the Ebro account from EVBro (core/ebro_account.py, POST /api/account/ebro-password).

The request is read from the official app (24-11-2026) and has not been sent to the real cloud yet: these tests pin the request
the app makes and what EVBro does with each answer.
"""
from __future__ import annotations

import base64
import json

import httpx
import pytest
from Crypto.Cipher import AES
from Crypto.Util.Padding import unpad
from sqlalchemy import select

from app.api import routes_account
from app.core import ebro_account, http
from app.core.context import CoreCtx
from app.db.models import AppUser, EbroAccount
from app.db.session import async_session_factory
from app.main import app
from app.security import auth, passwords

CURRENT = "Vieja-1234"
NEW = "Nueva#2026"


def _decrypt(value: str) -> str:
    key = b"w9R8Ag1KiL0pvMHc"
    return unpad(AES.new(key, AES.MODE_CBC, key).decrypt(base64.b64decode(value)), 16).decode()


@pytest.mark.parametrize("password, ok", [
    ("Nueva#2026", True),
    ("abc123.x", True),                      # 8 characters, the shortest allowed
    ("Abcdefgh12345#78", True),              # 16, the longest
    ("Abcdefgh123456#78", False),            # 17
    ("Ab1#xyz", False),                      # 7
    ("Nueva2026x", False),                   # no special character
    ("nueva#clave", False),                  # no digit
    ("1234#5678", False),                    # no letter
])
def test_the_new_password_follows_the_official_apps_rule(password, ok) -> None:
    assert ebro_account.password_format_ok(password) is ok


def test_the_request_is_the_one_of_the_official_app(tmp_path, monkeypatch) -> None:
    token = tmp_path / "token.json"
    token.write_text(json.dumps({"access_token": "acc-1", "refresh_token": "ref-1"}))
    ctx = CoreCtx(tsp_host="https://tsp.example", bff="https://bff.example/api", token_path=str(token))
    sent = {}

    class _Answer:
        status_code = 200
        text = ""

        def json(self):
            return {"code": 0, "msg": None, "data": True}

    def post(url, data=None, headers=None, timeout=None, params=None):
        sent.update(url=url, body=json.loads(data), headers=headers, params=params)
        return _Answer()

    monkeypatch.setattr(http.requests, "post", post)
    answer = ebro_account.change_password(ctx, CURRENT, NEW)
    assert ebro_account.accepted(answer)
    assert sent["url"] == "https://bff.example/api/marketing/v1/user/user/updatePasswd"
    assert sent["params"] == {"client_id": "bigapp"}
    assert set(sent["body"]) == {"oldPassword", "newPassword"}
    assert (_decrypt(sent["body"]["oldPassword"]), _decrypt(sent["body"]["newPassword"])) == (CURRENT, NEW)
    assert sent["headers"]["Authorization"] == "Bearer acc-1"
    assert sent["headers"]["url"] == "/marketing/v1/user/user/updatePasswd"          # the signed path, without the query


def test_only_code_0_is_a_change() -> None:
    assert ebro_account.accepted({"code": "0"})
    assert not ebro_account.accepted({"code": 1, "msg": "La contraseña original es incorrecta"})
    assert not ebro_account.accepted({"_raw": "<html>"})
    assert ebro_account.refusal({"code": 1, "msg": "La contraseña original es incorrecta"}) == "La contraseña original es incorrecta"
    assert ebro_account.refusal({"msg": "bad access_token=abc"}) == "bad"


@pytest.fixture(autouse=True)
def _clean_throttle():
    routes_account.ebro_password_throttle._fails.clear()
    yield
    routes_account.ebro_password_throttle._fails.clear()


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
        s.add(EbroAccount(id=3, user_id=1, phone="600000001", password=CURRENT, area_code="34", is_active=True))
        await s.commit()
        token = await auth.create_session(s, user, _Req())
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://testserver") as client:
        client.cookies.set(auth.COOKIE_NAME, token)
        yield client


def _cloud(monkeypatch, outcome=None, raises=None):
    calls = []

    def change(session_path, phone, area_code, current, new):
        calls.append((phone, area_code, current, new))
        if raises:
            raise raises
        return outcome

    monkeypatch.setattr(routes_account, "change_account_password", change)
    return calls


async def _stored_password() -> str:
    async with async_session_factory() as s:
        return (await s.execute(select(EbroAccount).where(EbroAccount.id == 3))).scalar_one().password


async def test_a_change_the_cloud_takes_keeps_the_new_password_and_session(web, monkeypatch) -> None:
    calls = _cloud(monkeypatch, {"changed": True, "reason": None, "token_data": {"access_token": "nuevo"}})
    r = await web.post("/api/account/ebro-password", json={"current_password": CURRENT, "new_password": NEW})
    assert r.status_code == 200 and r.json() == {"status": "ok", "session_renewed": True}
    assert calls == [("600000001", "34", CURRENT, NEW)]
    assert await _stored_password() == NEW
    async with async_session_factory() as s:
        acc = (await s.execute(select(EbroAccount).where(EbroAccount.id == 3))).scalar_one()
        assert acc.token_data == {"access_token": "nuevo"} and acc.last_login is not None


async def test_a_refusal_of_the_cloud_is_a_424_and_changes_nothing(web, monkeypatch) -> None:
    _cloud(monkeypatch, {"changed": False, "reason": "La contraseña original es incorrecta", "token_data": None})
    r = await web.post("/api/account/ebro-password", json={"current_password": CURRENT, "new_password": NEW})
    assert r.status_code == 424 and "La contraseña original es incorrecta" in r.json()["detail"]
    assert await _stored_password() == CURRENT


async def test_bad_input_never_reaches_the_cloud(web, monkeypatch) -> None:
    calls = _cloud(monkeypatch, {"changed": True, "reason": None, "token_data": None})
    for body in ({"current_password": CURRENT, "new_password": "corta1#"},         # format
                 {"current_password": CURRENT, "new_password": CURRENT},           # the same one
                 {"current_password": "Otra-1234", "new_password": NEW}):          # not the stored one
        assert (await web.post("/api/account/ebro-password", json=body)).status_code == 422
    assert calls == []


async def test_two_failures_wait_ten_minutes_to_protect_the_account(web, monkeypatch) -> None:
    calls = _cloud(monkeypatch, {"changed": False, "reason": "no", "token_data": None})
    assert (await web.post("/api/account/ebro-password", json={"current_password": "Otra-1234", "new_password": NEW})).status_code == 422
    assert (await web.post("/api/account/ebro-password", json={"current_password": CURRENT, "new_password": NEW})).status_code == 424
    r = await web.post("/api/account/ebro-password", json={"current_password": CURRENT, "new_password": NEW})
    assert r.status_code == 429 and len(calls) == 1


async def test_a_session_that_does_not_open_asks_to_connect_the_account_again(web, monkeypatch) -> None:
    _cloud(monkeypatch, raises=RuntimeError("no_usertoken"))
    r = await web.post("/api/account/ebro-password", json={"current_password": CURRENT, "new_password": NEW})
    assert r.status_code == 424 and await _stored_password() == CURRENT


async def test_a_change_without_a_new_session_still_keeps_the_new_password(web, monkeypatch) -> None:
    _cloud(monkeypatch, {"changed": True, "reason": None, "token_data": None})
    r = await web.post("/api/account/ebro-password", json={"current_password": CURRENT, "new_password": NEW})
    assert r.status_code == 200 and r.json()["session_renewed"] is False
    assert await _stored_password() == NEW


async def test_without_an_ebro_account_there_is_nothing_to_change(web, monkeypatch) -> None:
    async with async_session_factory() as s:
        (await s.execute(select(EbroAccount).where(EbroAccount.id == 3))).scalar_one().is_active = False
        await s.commit()
    assert (await web.post("/api/account/ebro-password", json={"current_password": CURRENT, "new_password": NEW})).status_code == 404


def test_the_service_signs_in_with_the_new_password_only_after_the_cloud_takes_it(tmp_path, monkeypatch) -> None:
    from app.services import accounts

    logins, stored = [], []
    monkeypatch.setattr(accounts, "_bff_login", lambda ctx: ("user-token", "42"))
    monkeypatch.setattr(accounts, "store_session", lambda path, data: stored.append((path, data)))

    def login(phone, password, area_code="34", host=None):
        logins.append((phone, password, area_code))
        return True, {"access_token": "nuevo"}

    monkeypatch.setattr(accounts.ebro_login, "password_login", login)
    path = str(tmp_path / "token.json")

    monkeypatch.setattr(accounts.ebro_account, "change_password", lambda ctx, current, new: {"code": 1, "msg": "No"})
    assert accounts.change_account_password(path, "600000001", "34", CURRENT, NEW) == {"changed": False, "reason": "No", "token_data": None}
    assert logins == [] and stored == []

    monkeypatch.setattr(accounts.ebro_account, "change_password", lambda ctx, current, new: {"code": 0})
    out = accounts.change_account_password(path, "600000001", "34", CURRENT, NEW)
    assert out == {"changed": True, "reason": None, "token_data": {"access_token": "nuevo"}}
    assert logins == [("600000001", NEW, "34")] and stored == [(path, {"access_token": "nuevo"})]

    monkeypatch.setattr(accounts, "_bff_login", lambda ctx: (None, None))
    with pytest.raises(RuntimeError, match="no_usertoken"):
        accounts.change_account_password(path, "600000001", "34", CURRENT, NEW)
