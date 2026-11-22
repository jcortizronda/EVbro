"""Messages from the administrator to the manager accounts (console → «Mensajes»): they land in each account's notice centre and
go out through its channels; an account may silence them (kept in the bell, nothing sent). Plain text, and the console only learns
how many accounts got each one."""
from __future__ import annotations

import httpx
import pytest
from sqlalchemy import select

from app.admin_app import admin_app
from app.api import routes_admin_auth, routes_auth
from app.config import settings
from app.db.models import AdminAudit, AdminMessage, AppUser, NotificationLog, PushSubscription
from app.db.session import async_session_factory
from app.main import app
from app.security import auth, passwords
from app.services import admin_messages
from app.services.notification_service import NotificationService

SETUP_CODE = "test-setup-code"
PW = "una-contraseña-larga-1"
ANA, BETO, CARLA, LUCIA, PABLO = 10, 11, 12, 20, 21


class _Req:
    client = type("C", (), {"host": "127.0.0.1"})()
    headers = {"user-agent": "t"}


@pytest.fixture
async def world(db, data_dir, monkeypatch):
    """The administrator (signed in to the console) and three managers: ana (member lucia), beto (member pablo) and carla, who is
    deactivated. Everybody but pablo has a phone subscribed. Returns (console client, {user id: app client}, pushes sent)."""
    monkeypatch.setattr(settings, "SETUP_CODE", SETUP_CODE)
    auth.reset_setup_code()
    for t in (routes_auth.ip_throttle, routes_admin_auth.ip_throttle, routes_admin_auth.pair_throttle):
        t._fails.clear()
    console = httpx.AsyncClient(transport=httpx.ASGITransport(app=admin_app), base_url="http://testserver")
    r = await console.post("/api/auth/setup", json={"username": "root", "password": PW, "setup_code": SETUP_CODE})
    assert r.status_code == 200, r.text

    h = passwords.hash_password(PW)
    clients = {}
    async with async_session_factory() as s:
        s.add_all([AppUser(id=ANA, username="ana", role="manager", password_hash=h), AppUser(id=BETO, username="beto", role="manager", password_hash=h),
                   AppUser(id=CARLA, username="carla", role="manager", password_hash=h, is_active=False),
                   AppUser(id=LUCIA, username="lucia", role="member", parent_id=ANA, password_hash=h),
                   AppUser(id=PABLO, username="pablo", role="member", parent_id=BETO, password_hash=h)])
        for uid in (ANA, BETO, CARLA, LUCIA):
            s.add(PushSubscription(endpoint_hash=f"{uid:02d}" * 32, user_id=uid, subscription={"endpoint": f"https://push.example/u{uid}", "keys": {}}))
        await s.commit()
        for uid in (ANA, BETO, LUCIA, PABLO):
            token = await auth.create_session(s, await s.get(AppUser, uid), _Req())
            c = httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://testserver")
            c.cookies.set(auth.COOKIE_NAME, token)
            clients[uid] = c

    sent = []

    def fake(cls, subscription, payload):
        sent.append((int(subscription["endpoint"].rsplit("/u", 1)[1]), payload))
        return True, None, False

    monkeypatch.setattr(NotificationService, "_webpush_blocking", classmethod(fake))
    yield console, clients, sent
    for c in (console, *clients.values()):
        await c.aclose()
    auth.reset_setup_code()


async def notices(client) -> list:
    return [(n["category"], n["title"], n["message"], n["channel"]) for n in (await client.get("/api/notifications/logs")).json()]


async def test_a_message_to_all_reaches_every_usable_manager_and_only_them(world) -> None:
    console, clients, sent = world
    r = await console.post("/api/admin/messages", json={"title": "Mantenimiento el sábado", "body": "De 22:00 a 23:00 el servidor estará parado."})
    assert r.status_code == 200, r.text
    m = r.json()
    assert (m["audience"], m["targets"], m["include_members"], m["recipients"], m["pushed"], m["silenced"]) == ("all", None, False, 2, 2, 0)
    for uid in (ANA, BETO):
        assert await notices(clients[uid]) == [("admin_message", "Mantenimiento el sábado", "De 22:00 a 23:00 el servidor estará parado.", "webpush")]
        assert (await clients[uid].get("/api/notifications/unread")).json() == {"count": 1}
    for uid in (LUCIA, PABLO):                                                   # member accounts were not asked for
        assert await notices(clients[uid]) == []
    assert sorted(uid for uid, _ in sent) == [ANA, BETO]                        # not carla: a deactivated account gets nothing
    async with async_session_factory() as s:
        assert (await s.execute(select(NotificationLog).where(NotificationLog.user_id == CARLA))).first() is None
    # two messages are two notifications on the phone, not one replacing the other
    await console.post("/api/admin/messages", json={"title": "Otro", "body": "Texto"})
    tags = {p["tag"] for _, p in sent}
    assert len(tags) == 2 and all(t.startswith("admin_message-") for t in tags)


async def test_the_chosen_managers_and_their_member_accounts(world) -> None:
    console, clients, sent = world
    r = await console.post("/api/admin/messages", json={"title": "Solo para Ana", "body": "Hola", "manager_ids": [ANA], "include_members": True})
    assert r.status_code == 200, r.text
    m = r.json()
    assert (m["audience"], m["targets"], m["include_members"], m["recipients"]) == ("selected", "ana", True, 2)
    assert len(await notices(clients[ANA])) == 1 and len(await notices(clients[LUCIA])) == 1
    assert await notices(clients[BETO]) == [] and await notices(clients[PABLO]) == []
    assert sorted(uid for uid, _ in sent) == [ANA, LUCIA]
    # nobody to send it to, an account that cannot sign in, one that does not exist, a member or the administrator: refused whole
    for ids in ([], [CARLA], [ANA, 999], [LUCIA], [1]):
        r = await console.post("/api/admin/messages", json={"title": "No", "body": "No", "manager_ids": ids})
        assert r.status_code == 422, (ids, r.text)
    assert len(await notices(clients[ANA])) == 1 and len((await console.get("/api/admin/messages")).json()) == 1


async def test_a_silenced_account_keeps_the_message_in_its_bell_and_receives_nothing(world) -> None:
    console, clients, sent = world
    assert (await clients[BETO].get("/api/notifications/config")).json()["notify_admin_messages"] is True
    assert (await clients[BETO].put("/api/notifications/config", json={"notify_admin_messages": False})).status_code == 200
    assert (await clients[BETO].get("/api/notifications/config")).json()["notify_admin_messages"] is False
    m = (await console.post("/api/admin/messages", json={"title": "Novedades", "body": "Hay versión nueva."})).json()
    assert (m["recipients"], m["pushed"], m["silenced"]) == (2, 1, 1)
    assert [uid for uid, _ in sent] == [ANA]
    assert await notices(clients[BETO]) == [("admin_message", "Novedades", "Hay versión nueva.", "app")]      # kept, not sent
    assert (await clients[BETO].get("/api/notifications/unread")).json() == {"count": 1}
    # silencing these messages does not silence the car's notices
    await NotificationService.dispatch(category="security", title="Coche abierto", message="Texto", user_id=BETO)
    assert [uid for uid, _ in sent] == [ANA, BETO]


async def test_an_account_without_channels_still_gets_it_in_its_bell(world) -> None:
    console, clients, sent = world
    m = (await console.post("/api/admin/messages", json={"title": "Aviso", "body": "Texto", "manager_ids": [BETO], "include_members": True})).json()
    assert (m["recipients"], m["pushed"], m["silenced"]) == (2, 1, 0)
    assert await notices(clients[PABLO]) == [("admin_message", "Aviso", "Texto", "app")]


async def test_the_message_is_plain_text_with_limits(world) -> None:
    console, clients, sent = world
    body = "Línea uno\r\n<b>dos</b> & <script>alert(1)</script>\x00\x07\n\n  tres  "
    r = await console.post("/api/admin/messages", json={"title": "  <i>Título</i>\ncon salto ", "body": body})
    assert r.status_code == 200, r.text
    assert r.json()["title"] == "<i>Título</i> con salto"
    assert r.json()["body"] == "Línea uno\n<b>dos</b> & <script>alert(1)</script>\n\n  tres"       # kept as text, never interpreted
    assert (await notices(clients[ANA]))[0][2] == r.json()["body"]
    for bad in ({"title": "", "body": "x"}, {"title": "x", "body": "   "}, {"title": "x"}, {"title": "x" * (admin_messages.TITLE_MAX + 1), "body": "x"},
                {"title": "x", "body": "x" * (admin_messages.BODY_MAX + 1)}):
        assert (await console.post("/api/admin/messages", json=bad)).status_code == 422, bad
    assert len((await console.get("/api/admin/messages")).json()) == 1


async def test_the_console_keeps_what_was_sent_and_the_accounts_cannot_reach_it(world) -> None:
    console, clients, sent = world
    await console.post("/api/admin/messages", json={"title": "Primero", "body": "Uno"})
    await console.post("/api/admin/messages", json={"title": "Segundo", "body": "Dos", "manager_ids": [BETO]})
    rows = (await console.get("/api/admin/messages")).json()
    assert [(m["title"], m["username"], m["targets"], m["recipients"]) for m in rows] == [("Segundo", "root", "beto", 1), ("Primero", "root", None, 2)]
    async with async_session_factory() as s:
        audit = (await s.execute(select(AdminAudit).where(AdminAudit.action == "message.send").order_by(AdminAudit.id))).scalars().all()
        assert [(a.username, a.target, a.detail) for a in audit] == [("root", "todos los gestores", "Primero (2)"), ("root", "beto", "Segundo (1)")]
        assert len((await s.execute(select(AdminMessage))).scalars().all()) == 2
    # the app has no such route, and a manager's session is worth nothing in the console
    assert (await clients[ANA].get("/api/admin/messages")).status_code in (401, 404)
    assert (await clients[ANA].post("/api/admin/messages", json={"title": "x", "body": "y"})).status_code in (401, 404)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=admin_app), base_url="http://testserver") as c:
        c.cookies.set(auth.COOKIE_NAME, clients[ANA].cookies.get(auth.COOKIE_NAME))
        assert (await c.get("/api/admin/messages")).status_code == 401
        assert (await c.post("/api/admin/messages", json={"title": "x", "body": "y"})).status_code == 401
    assert await notices(clients[BETO]) != [] and len(await notices(clients[ANA])) == 1


async def test_a_dismissed_message_is_gone_for_that_account_only(world) -> None:
    console, clients, sent = world
    await console.post("/api/admin/messages", json={"title": "Aviso", "body": "Texto"})
    log_id = (await clients[ANA].get("/api/notifications/logs")).json()[0]["id"]
    assert (await clients[ANA].delete(f"/api/notifications/logs/{log_id}")).status_code == 200
    assert await notices(clients[ANA]) == [] and len(await notices(clients[BETO])) == 1
