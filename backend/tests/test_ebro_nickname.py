"""The car's name in the Ebro account (its nickname): core/ebro_account.py, PUT /api/vehicles/{vin}/ebro-name. Read from the official app
on 24-11-2026 and not yet sent to the real cloud: these tests pin the request (a PUT whose fields go in the query string and in the
signature, `ebro_auth.sign_values`) and what EVBro does with each answer."""
from __future__ import annotations

import hashlib
import json

import httpx
import pytest
from sqlalchemy import select

from app.api import routes_vehicles
from app.core import ebro_account, ebro_auth, http
from app.core.context import CoreCtx
from app.db.models import AppUser, Vehicle
from app.db.session import async_session_factory
from app.main import app
from app.security import auth, passwords
from app.services import accounts
from app.services.poller import VehiclePoller

VIN = "LSJA0000000000081"


def test_the_signature_carries_the_values_as_the_app_does() -> None:
    sig, ts, keys = ebro_auth.sign_values("/marketing/owner-auth/app", {"vin": VIN, "defaultFlag": 1, "nickname": "Coche"}, ts_ms=1700)
    base = f"{ebro_auth.SIGN_SECRET}{ebro_auth.SIGN_NONCE}/marketing/owner-auth/app1700[{VIN},1,Coche]"
    assert (sig, ts, keys) == (hashlib.sha256(base.encode()).hexdigest(), 1700, "vin,defaultFlag,nickname")
    assert ebro_auth.sign_values("/x", {}, ts_ms=5)[0] == ebro_auth.sign_post("/x", 5)[0]        # no values: the POST signature
    assert ebro_auth.sign_values("/x", {"a": None, "b": True}, ts_ms=5)[0] == hashlib.sha256(
        f"{ebro_auth.SIGN_SECRET}{ebro_auth.SIGN_NONCE}/x5[null,true]".encode()).hexdigest()     # Dart's toString


def test_the_requests_are_the_ones_of_the_app(tmp_path, monkeypatch) -> None:
    token = tmp_path / "token.json"
    token.write_text(json.dumps({"access_token": "acc-1"}))
    ctx = CoreCtx(vin=VIN, tsp_host="https://tsp.example", bff="https://bff.example/api", token_path=str(token))
    sent = []

    class _Answer:
        status_code = 200
        text = ""

        def __init__(self, body):
            self._body = body

        def json(self):
            return self._body

    def request(method, url, headers=None, params=None, timeout=None):
        sent.append((method, url, headers, params))
        if method == "GET":
            return _Answer({"code": 0, "data": [{"vin": VIN, "nickname": "Viejo", "defaultFlag": 1}, "basura"]})
        return _Answer({"code": 0})

    monkeypatch.setattr(http.requests, "request", request)
    assert ebro_account.owner_cars(ctx) == [{"vin": VIN, "nickname": "Viejo", "defaultFlag": 1}]
    method, url, headers, params = sent[-1]
    assert (method, url, params) == ("GET", "https://bff.example/api/web/car-service/owner/list", None)
    assert headers["Authorization"] == "Bearer acc-1" and "keys" not in headers

    assert ebro_account.accepted(ebro_account.set_car_nickname(ctx, VIN, "Nuevo", 1))
    method, url, headers, params = sent[-1]
    assert (method, url) == ("PUT", "https://bff.example/api/marketing/owner-auth/app")
    assert list(params.items()) == [("vin", VIN), ("defaultFlag", 1), ("nickname", "Nuevo")]      # the app's order
    assert headers["keys"] == "vin,defaultFlag,nickname" and headers["url"] == "/marketing/owner-auth/app"
    expected = ebro_auth.sign_values("/marketing/owner-auth/app", params, ts_ms=int(headers["timestamp"]))[0]
    assert headers["signature"] == expected


def test_the_list_may_come_inside_records(tmp_path, monkeypatch) -> None:
    ctx = CoreCtx(vin=VIN, tsp_host="t", bff="b", token_path=str(tmp_path / "t.json"))
    (tmp_path / "t.json").write_text("{}")
    monkeypatch.setattr(http, "bff_request", lambda *a, **kw: {"code": 0, "data": {"records": [{"vin": VIN}]}})
    assert ebro_account.owner_cars(ctx) == [{"vin": VIN}]


def test_the_service_believes_the_list_not_the_answer(tmp_path, monkeypatch) -> None:
    ctx = CoreCtx(vin=VIN, tsp_host="t", bff="b", token_path=str(tmp_path / "t.json"))
    monkeypatch.setattr(accounts, "_bff_login", lambda ctx_: ("ut", "42"))
    state = {"nickname": "Viejo"}
    puts = []
    monkeypatch.setattr(ebro_account, "owner_cars", lambda ctx_: [{"vin": VIN.lower(), "nickname": state["nickname"], "defaultFlag": 0}])

    def put(ctx_, vin, nickname, default_flag, applies=True):
        puts.append((vin, nickname, default_flag))
        if applies:
            state["nickname"] = nickname
        return {"code": 0}

    monkeypatch.setattr(ebro_account, "set_car_nickname", put)
    assert accounts.rename_car_in_ebro(ctx, "Nuevo")["changed"] is True
    assert puts == [(VIN, "Nuevo", 0)]                                       # the car's defaultFlag goes back as it was
    assert accounts.rename_car_in_ebro(ctx, "Nuevo")["changed"] is True and len(puts) == 1     # already that name: nothing sent

    monkeypatch.setattr(ebro_account, "set_car_nickname", lambda *a: put(*a, applies=False))
    out = accounts.rename_car_in_ebro(ctx, "Otro")
    assert out["changed"] is False and out["unconfirmed"] is True

    monkeypatch.setattr(ebro_account, "set_car_nickname", lambda *a: {"code": 1, "msg": "Demasiado largo"})
    assert accounts.rename_car_in_ebro(ctx, "Otro") == {"changed": False, "unconfirmed": False, "reason": "Demasiado largo", "code": 1}

    monkeypatch.setattr(ebro_account, "owner_cars", lambda ctx_: [])
    assert accounts.rename_car_in_ebro(ctx, "Otro")["changed"] is False

    monkeypatch.setattr(accounts, "_bff_login", lambda ctx_: (None, None))
    with pytest.raises(RuntimeError, match="no_usertoken"):
        accounts.rename_car_in_ebro(ctx, "Otro")


# ───────────────────────── the route ─────────────────────────
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
        s.add(Vehicle(vin=VIN, owner_id=1, name="Viejo"))
        await s.commit()
        token = await auth.create_session(s, user, _Req())
    VehiclePoller.get_instance()._contexts.pop(VIN, None)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://testserver") as client:
        client.cookies.set(auth.COOKIE_NAME, token)
        yield client
    VehiclePoller.get_instance()._contexts.pop(VIN, None)


async def _name() -> str:
    async with async_session_factory() as s:
        return (await s.execute(select(Vehicle).where(Vehicle.vin == VIN))).scalar_one().name


async def test_a_name_ebro_takes_is_kept_in_evbro(web, monkeypatch) -> None:
    calls = []
    monkeypatch.setattr(routes_vehicles, "rename_car_in_ebro", lambda ctx, name: calls.append((ctx.vin, name)) or
                        {"changed": True, "unconfirmed": False, "reason": None, "code": 0})
    r = await web.put(f"/api/vehicles/{VIN}/ebro-name", json={"name": "  Nuevo  "})
    assert r.status_code == 200 and calls == [(VIN, "Nuevo")] and await _name() == "Nuevo"


async def test_a_refusal_keeps_the_old_name(web, monkeypatch) -> None:
    monkeypatch.setattr(routes_vehicles, "rename_car_in_ebro", lambda ctx, name: {"changed": False, "unconfirmed": False, "reason": "No", "code": 1})
    r = await web.put(f"/api/vehicles/{VIN}/ebro-name", json={"name": "Nuevo"})
    assert r.status_code == 424 and "No" in r.json()["detail"] and await _name() == "Viejo"
    monkeypatch.setattr(routes_vehicles, "rename_car_in_ebro", lambda ctx, name: {"changed": False, "unconfirmed": True, "reason": None, "code": 0})
    assert (await web.put(f"/api/vehicles/{VIN}/ebro-name", json={"name": "Nuevo"})).status_code == 424
    assert await _name() == "Viejo"


async def test_bad_names_and_manual_cars_never_reach_the_cloud(web, monkeypatch) -> None:
    calls = []
    monkeypatch.setattr(routes_vehicles, "rename_car_in_ebro", lambda ctx, name: calls.append(name))
    assert (await web.put(f"/api/vehicles/{VIN}/ebro-name", json={"name": "   "})).status_code == 422
    assert (await web.put(f"/api/vehicles/{VIN}/ebro-name", json={"name": "x" * 31})).status_code == 422
    async with async_session_factory() as s:
        (await s.execute(select(Vehicle).where(Vehicle.vin == VIN))).scalar_one().is_manual = True
        await s.commit()
    assert (await web.put(f"/api/vehicles/{VIN}/ebro-name", json={"name": "Nuevo"})).status_code == 409
    assert calls == []
