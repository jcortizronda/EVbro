"""What a manager is told when the Ebro cloud refuses their password, when connecting the account and when looking for cars.

09-11-2026 on a real install: a refused password came out as a 502 «could not connect» (and an error in the log), and «Buscar
vehículos» with a password the cloud no longer accepted answered «0 vehicles».
"""
from __future__ import annotations

import datetime

import httpx
import pytest
from sqlalchemy import select

from app.api import routes_account
from app.api.routes_account import cloud_refusal
from app.db.models import AppUser, EbroAccount, Vehicle
from app.db.session import async_session_factory
from app.main import app
from app.security import auth, passwords
from app.services import poller as poller_mod
from app.services.poller import LoginFailed, VehiclePoller

REFUSAL = "HTTP 401: El nombre de usuario no existe o la contraseña es incorrecta"
VIN = "LSJA0000000000051"


def test_the_clouds_reason_is_shown_without_the_http_prefix_and_never_a_token() -> None:
    assert cloud_refusal(REFUSAL) == "El nombre de usuario no existe o la contraseña es incorrecta"
    assert cloud_refusal("HTTP 500: {'access_token': 'x'}") == "{'"
    assert cloud_refusal("") == "credenciales rechazadas"


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
        await s.commit()
        token = await auth.create_session(s, user, _Req())
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://testserver") as client:
        client.cookies.set(auth.COOKIE_NAME, token)
        yield client


async def test_a_refused_password_is_a_401_with_the_clouds_reason_and_a_network_failure_a_502(web, monkeypatch) -> None:
    def refused(phone, password, area_code="34", **kw):
        raise RuntimeError(REFUSAL)

    monkeypatch.setattr(routes_account.ebro_login, "login_with_password", refused)
    r = await web.post("/api/account/login", json={"phone": "600000001", "area_code": "34", "password": "mala", "pin": ""})
    assert r.status_code == 401
    assert "contraseña es incorrecta" in r.json()["detail"] and "HTTP" not in r.json()["detail"]

    def unreachable(phone, password, area_code="34", **kw):
        raise ConnectionError("tspconsole-eu.example")

    monkeypatch.setattr(routes_account.ebro_login, "login_with_password", unreachable)
    r = await web.post("/api/account/login", json={"phone": "600000001", "area_code": "34", "password": "mala", "pin": ""})
    assert r.status_code == 502 and "example" not in r.json()["detail"]


@pytest.fixture
async def connected(web, monkeypatch):
    """A manager with a connected account that already holds a session, and a poller with nothing in memory."""
    async with async_session_factory() as s:
        s.add(EbroAccount(id=3, user_id=1, phone="600000001", password="secreta", area_code="34", is_active=True,
                          token_data={"access_token": "viva", "refresh_token": "r"}, last_login=datetime.datetime(2026, 11, 6, 18, 56)))
        await s.commit()
    poller = VehiclePoller()
    poller._session_lost, poller._relogin_attempt_at, poller._contexts = {}, {}, {}
    monkeypatch.setattr(VehiclePoller, "get_instance", classmethod(lambda cls: poller))
    monkeypatch.setattr(VehiclePoller, "request_poll", lambda self, vin, force=False: None)
    return poller


async def test_looking_for_cars_uses_the_session_the_account_holds_before_any_password_login(web, connected, monkeypatch) -> None:
    logins = []

    def login(phone, password, area_code="34", **kw):
        logins.append(phone)
        return {"access_token": "nueva"}

    def list_cars(token_data, skip_vins=frozenset()):
        assert token_data["access_token"] == "viva"                          # the stored session, not a fresh login
        return [{"vin": VIN, "nickname": "Ebro", "fullName": "S900 PHEV", "brandName": "Ebro"}]

    monkeypatch.setattr(poller_mod.ebro_login, "login_with_password", login)
    monkeypatch.setattr(poller_mod, "fetch_account_vehicles", list_cars)
    r = await web.post("/api/vehicles/discover")
    assert r.status_code == 200 and r.json()["vehicles"] == [VIN] and logins == []


async def test_looking_for_cars_with_a_password_the_cloud_rejects_says_so_and_stops_the_reads(web, connected, monkeypatch) -> None:
    def dead_session(token_data, skip_vins=frozenset()):
        raise RuntimeError("no_usertoken")

    def refused(phone, password, area_code="34", **kw):
        raise RuntimeError(REFUSAL)

    monkeypatch.setattr(poller_mod, "fetch_account_vehicles", dead_session)
    monkeypatch.setattr(poller_mod.ebro_login, "login_with_password", refused)
    r = await web.post("/api/vehicles/discover")
    assert r.status_code == 424 and "contraseña" in r.json()["detail"]
    async with async_session_factory() as s:
        acc = await s.get(EbroAccount, 3)
        assert connected.session_lost(acc.id, acc.last_login)                # the header says «Sesión caducada», the poller waits
        assert await connected.lost_owner_ids(s) == {1}
    # the account status tells the same story to Ajustes → Cuenta
    assert (await web.get("/api/account/status")).json()["session_ok"] is False

    def unreachable(phone, password, area_code="34", **kw):
        raise ConnectionError("down")

    connected._session_lost.clear()
    monkeypatch.setattr(poller_mod.ebro_login, "login_with_password", unreachable)
    r = await web.post("/api/vehicles/discover")
    assert r.status_code == 502 and not connected._session_lost                # nothing to conclude from a network failure


async def test_without_strict_mode_a_refusal_is_an_empty_list_and_a_warning_not_an_error(connected, monkeypatch, caplog) -> None:
    def refused(phone, password, area_code="34", **kw):
        raise RuntimeError(REFUSAL)

    monkeypatch.setattr(poller_mod, "fetch_account_vehicles", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("dead")))
    monkeypatch.setattr(poller_mod.ebro_login, "login_with_password", refused)
    async with async_session_factory() as s:
        acc = await s.get(EbroAccount, 3)
    with caplog.at_level("WARNING", logger="app.services.poller"):
        assert await connected.discover_vehicles(account=acc) == []
    assert [r.levelname for r in caplog.records if "rejected the password" in r.message] == ["WARNING"]
    with pytest.raises(LoginFailed) as exc:
        await connected.discover_vehicles(account=acc, strict=True)
    assert exc.value.rejected is True
    async with async_session_factory() as s:
        assert (await s.execute(select(Vehicle))).scalars().all() == []
