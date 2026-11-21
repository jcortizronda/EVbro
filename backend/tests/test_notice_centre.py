"""The bell of the header: every notice stays in the account's history, even without any channel, and the dot counts the unseen ones.

09-11-2026 on a real server: five of seven accounts had no push, Telegram, Pushover or webhook, so their notices were neither
delivered nor kept anywhere; only two accounts had received anything in a month, and the «Cuenta Ebro desconectada» notice of a
manager whose password the cloud rejected was lost.
"""
from __future__ import annotations

import datetime

import httpx
import pytest
from sqlalchemy import select

from app.db.models import AppUser, NotificationLog, PushSubscription
from app.db.session import async_session_factory
from app.main import app
from app.security import auth, passwords
from app.services.notification_service import NotificationService

PW = "una-contraseña-larga-1"


class _Req:
    client = type("C", (), {"host": "127.0.0.1"})()
    headers = {"user-agent": "t"}


@pytest.fixture
async def accounts(db, monkeypatch):
    """Ana has no channel at all; Beto has push on one phone."""
    h = passwords.hash_password(PW)
    async with async_session_factory() as s:
        s.add_all([AppUser(id=2, username="ana", role="manager", password_hash=h, vehicle_limit=1),
                   AppUser(id=3, username="beto", role="manager", password_hash=h, vehicle_limit=1)])
        await s.flush()
        s.add(PushSubscription(endpoint_hash="3" * 64, user_id=3, subscription={"endpoint": "https://push.example/beto", "keys": {}}))
        await s.commit()

    def fake(cls, subscription, payload):
        return True, None, False

    monkeypatch.setattr(NotificationService, "_webpush_blocking", classmethod(fake))


async def client_for(user_id):
    async with async_session_factory() as s:
        token = await auth.create_session(s, await s.get(AppUser, user_id), _Req())
    c = httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://testserver")
    c.cookies.set(auth.COOKIE_NAME, token)
    return c


async def rows(user_id):
    async with async_session_factory() as s:
        return (await s.execute(select(NotificationLog).where(NotificationLog.user_id == user_id))).scalars().all()


async def test_a_notice_to_an_account_without_channels_is_kept_in_its_history(accounts) -> None:
    await NotificationService.notify_account_session_lost(2, ["Ebro"])
    (row,) = await rows(2)
    assert (row.channel, row.success, row.category) == ("app", True, "account")
    await NotificationService.dispatch(user_id=3, category="maintenance", title="ITV", message="Pasa la ITV")
    assert [r.channel for r in await rows(3)] == ["webpush"]            # a delivered notice is not written twice


async def test_the_dot_counts_unseen_notices_once_whatever_the_channels_and_opening_the_bell_clears_it(accounts) -> None:
    ana = await client_for(2)
    try:
        assert (await ana.get("/api/notifications/unread")).json() == {"count": 0}
        await NotificationService.dispatch(user_id=2, category="maintenance", title="ITV", message="Pasa la ITV")
        await NotificationService.dispatch(user_id=2, category="renewals", title="Seguro", message="Renueva el seguro")
        async with async_session_factory() as s:                      # the same notice through a second channel: still one notice
            first = (await s.execute(select(NotificationLog).where(NotificationLog.title == "ITV"))).scalar_one()
            s.add(NotificationLog(user_id=2, category="maintenance", title="ITV", message="Pasa la ITV", channel="telegram",
                                  success=False, error="sin red", timestamp=first.timestamp))
            await s.commit()
        assert (await ana.get("/api/notifications/unread")).json() == {"count": 2}
        logs = (await ana.get("/api/notifications/logs")).json()
        assert len(logs) == 3 and all(r["unread"] for r in logs)
        assert (await ana.post("/api/notifications/seen")).status_code == 200
        assert (await ana.get("/api/notifications/unread")).json() == {"count": 0}
        assert not any(r["unread"] for r in (await ana.get("/api/notifications/logs")).json())
        await NotificationService.dispatch(user_id=2, category="security", title="Puertas", message="El coche está abierto")
        assert (await ana.get("/api/notifications/unread")).json() == {"count": 1}
    finally:
        await ana.aclose()


async def test_each_account_sees_and_clears_only_its_own_notices(accounts) -> None:
    await NotificationService.dispatch(user_id=3, category="maintenance", title="ITV", message="Pasa la ITV")
    ana, beto = await client_for(2), await client_for(3)
    try:
        assert (await ana.get("/api/notifications/unread")).json() == {"count": 0}
        assert (await beto.get("/api/notifications/unread")).json() == {"count": 1}
        await ana.post("/api/notifications/seen")
        assert (await beto.get("/api/notifications/unread")).json() == {"count": 1}
        async with async_session_factory() as s:
            assert (await s.get(AppUser, 3)).notifications_seen_at is None
            assert (await s.get(AppUser, 2)).notifications_seen_at <= datetime.datetime.utcnow()
    finally:
        await ana.aclose()
        await beto.aclose()


async def test_the_notice_routes_need_a_session(db) -> None:
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://testserver") as anon:
        assert (await anon.get("/api/notifications/unread")).status_code == 401
        assert (await anon.post("/api/notifications/seen")).status_code == 401
