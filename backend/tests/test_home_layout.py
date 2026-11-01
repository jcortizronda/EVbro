"""Each account's own Inicio: the widgets it shows, in order. Personal, so read-only accounts may change theirs too."""
from __future__ import annotations

import httpx
import pytest

from app.db.models import AppUser
from app.db.session import async_session_factory
from app.main import app
from app.security import auth, passwords


class _Req:
    client = type("C", (), {"host": "127.0.0.1"})()
    headers = {"user-agent": "t"}


async def _client(user_id: int) -> httpx.AsyncClient:
    async with async_session_factory() as s:
        token = await auth.create_session(s, await s.get(AppUser, user_id), _Req())
    c = httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://testserver", headers={"Origin": "http://testserver"})
    c.cookies.set(auth.COOKIE_NAME, token)
    return c


@pytest.fixture
async def accounts(db):
    h = passwords.hash_password("una-contraseña-larga-1")
    async with async_session_factory() as s:
        s.add_all([AppUser(id=1, role="manager", username="juan", vehicle_limit=-1, password_hash=h),
                   AppUser(id=2, role="member", username="ana", parent_id=1, read_only=True, password_hash=h)])
        await s.commit()
    juan, ana = await _client(1), await _client(2)
    yield juan, ana
    await juan.aclose()
    await ana.aclose()


async def test_the_layout_is_saved_per_account_in_order_and_can_be_reset(accounts) -> None:
    juan, ana = accounts
    assert (await juan.get("/api/account/home-layout")).json() == {"layout": None}                 # never touched: the default
    r = await juan.put("/api/account/home-layout", json={"widgets": ["map", "range", "service", "map"]})
    assert r.status_code == 200 and r.json()["layout"] == {"widgets": ["map", "range", "service"]}   # duplicates dropped, order kept
    assert (await juan.get("/api/account/home-layout")).json()["layout"]["widgets"] == ["map", "range", "service"]
    assert (await ana.get("/api/account/home-layout")).json() == {"layout": None}                  # somebody else's Inicio is not touched
    assert (await juan.put("/api/account/home-layout", json={"widgets": []})).json()["layout"] == {"widgets": []}   # everything removed is valid
    assert (await juan.delete("/api/account/home-layout")).json() == {"layout": None}
    assert (await juan.get("/api/account/home-layout")).json() == {"layout": None}


async def test_a_read_only_account_arranges_its_own_inicio(accounts) -> None:
    _, ana = accounts
    r = await ana.put("/api/account/home-layout", json={"widgets": ["last_trip", "map"]})
    assert r.status_code == 200 and r.json()["layout"]["widgets"] == ["last_trip", "map"]


@pytest.mark.parametrize("widgets", [["Map"], ["<script>"], ["a" * 30], [""], ["map"] * 41, "map"])
async def test_only_widget_keys_are_accepted(accounts, widgets) -> None:
    juan, _ = accounts
    assert (await juan.put("/api/account/home-layout", json={"widgets": widgets})).status_code == 422


async def test_without_a_session_there_is_no_layout(db) -> None:
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://testserver", headers={"Origin": "http://testserver"}) as c:
        assert (await c.get("/api/account/home-layout")).status_code == 401


async def test_energy_and_comfort_have_their_own_layout_and_reset(accounts) -> None:
    juan, _ = accounts
    await juan.put("/api/account/home-layout", json={"widgets": ["map", "range"]})
    r = await juan.put("/api/account/home-layout", json={"page": "charges", "widgets": ["history", "energy"]})
    assert r.json()["layout"] == {"widgets": ["map", "range"], "pages": {"charges": ["history", "energy"]}}
    await juan.put("/api/account/home-layout", json={"page": "climate", "widgets": ["seats"]})
    assert (await juan.delete("/api/account/home-layout")).json()["layout"] == {"pages": {"charges": ["history", "energy"], "climate": ["seats"]}}
    assert (await juan.delete("/api/account/home-layout?page=charges")).json()["layout"] == {"pages": {"climate": ["seats"]}}
    assert (await juan.delete("/api/account/home-layout?page=climate")).json()["layout"] is None
    assert (await juan.put("/api/account/home-layout", json={"page": "settings", "widgets": []})).status_code == 422
