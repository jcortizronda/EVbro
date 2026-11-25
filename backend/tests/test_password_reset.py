"""«¿Olvidaste la contraseña?»: the account's own e-mail (PUT /api/auth/email) and the single-use links sent to it
(/api/auth/password-reset, /confirm; services/password_reset.py and services/mailer.py)."""
from __future__ import annotations

import datetime
import re

import httpx
import pytest
from sqlalchemy import select, update

from app.api import routes_auth
from app.config import settings
from app.db.models import AppUser, AuthSession, PasswordReset
from app.db.session import async_session_factory
from app.main import app
from app.security import passwords
from app.services import mailer

PASSWORD = "una-contraseña-larga-1"
NEW = "otra-contraseña-larga-2"


@pytest.fixture
def outbox(monkeypatch, data_dir):
    """SMTP configured, nothing leaves: the messages land here."""
    sent = []
    monkeypatch.setattr(settings, "SMTP_HOST", "smtp.example.com")
    monkeypatch.setattr(settings, "SMTP_FROM", "EVbro <no-responder@example.com>")
    monkeypatch.setattr(settings, "PUBLIC_URL", "https://evbro.example.com/")
    monkeypatch.setattr(settings, "WEBAUTHN_ORIGIN", None)
    monkeypatch.setattr(mailer, "send", lambda to, subject, body, html=None: sent.append((to, subject, body, html)) or True)
    for t in (routes_auth.ip_throttle, routes_auth.pair_throttle, routes_auth.reset_ip_throttle, routes_auth.reset_account_throttle):
        t._fails.clear()
    return sent


@pytest.fixture
async def client(db):
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://testserver") as c:
        yield c


async def _user(uid: int, username: str, email: str | None = None, **kw) -> None:
    async with async_session_factory() as s:
        s.add(AppUser(id=uid, username=username, email=email, role=kw.pop("role", "manager"), password_hash=passwords.hash_password(PASSWORD),
                      vehicle_limit=2, member_limit=5, **kw))
        await s.commit()


async def _login(client, username: str, password: str = PASSWORD) -> int:
    return (await client.post("/api/auth/login", json={"username": username, "password": password})).status_code


def _token(body: str) -> str:
    return re.search(r"https://evbro\.example\.com/#reset=([A-Za-z0-9_-]+)", body).group(1)


# ───────────────────────── the mailer ─────────────────────────
def test_addresses_are_checked_and_kept_in_lower_case() -> None:
    assert mailer.normalize_email("  Ana.Perez+ev@Example.COM ") == "ana.perez+ev@example.com"
    assert mailer.normalize_email("") is None and mailer.normalize_email(None) is None
    for bad in ("ana", "ana@", "@example.com", "ana@example", "a b@example.com", "ana@exa_mple.com", "x" * 250 + "@e.com"):
        with pytest.raises(ValueError):
            mailer.normalize_email(bad)


def test_the_links_never_come_from_the_request(monkeypatch) -> None:
    monkeypatch.setattr(settings, "PUBLIC_URL", None)
    monkeypatch.setattr(settings, "WEBAUTHN_ORIGIN", "https://evbro.example.com/, https://otra.example.com")
    assert mailer.public_url() == "https://evbro.example.com"
    monkeypatch.setattr(settings, "WEBAUTHN_ORIGIN", None)
    monkeypatch.setattr(settings, "SMTP_HOST", "smtp.example.com")
    monkeypatch.setattr(settings, "SMTP_FROM", "x@example.com")
    assert mailer.public_url() is None and mailer.enabled() is False         # no address to build the link on: no option


def test_smtp_with_starttls_and_login(monkeypatch) -> None:
    calls = []

    class _Smtp:
        def __init__(self, host, port, timeout=None):
            calls.append(("open", host, port))

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def starttls(self, context=None):
            calls.append(("starttls",))

        def login(self, user, password):
            calls.append(("login", user))

        def send_message(self, msg):
            calls.append(("send", msg["To"], msg["Subject"], msg["From"]))
            calls.append(("parts", [p.get_content_type() for p in msg.iter_parts()] if msg.is_multipart() else [msg.get_content_type()]))

    monkeypatch.setattr(mailer.smtplib, "SMTP", _Smtp)
    for k, v in (("SMTP_HOST", "smtp.example.com"), ("SMTP_PORT", 587), ("SMTP_USER", "u"), ("SMTP_PASSWORD", "p"),
                 ("SMTP_FROM", "EVbro <no-responder@example.com>"), ("SMTP_SECURITY", "starttls")):
        monkeypatch.setattr(settings, k, v)
    assert mailer.send("ana@example.com", "Asunto", "Cuerpo") is True
    assert calls == [("open", "smtp.example.com", 587), ("starttls",), ("login", "u"),
                     ("send", "ana@example.com", "Asunto", "EVbro <no-responder@example.com>"), ("parts", ["text/plain"])]
    calls.clear()
    assert mailer.send("ana@example.com", "Asunto", "Cuerpo", "<p>Cuerpo</p>") is True
    assert calls[-1] == ("parts", ["text/plain", "text/html"])                     # the text first: clients show the last they can

    def broken(*a, **kw):
        raise OSError("refused")

    monkeypatch.setattr(mailer.smtplib, "SMTP", broken)
    assert mailer.send("ana@example.com", "Asunto", "Cuerpo") is False


# ───────────────────────── the account's e-mail ─────────────────────────
async def test_the_email_needs_the_password_and_the_old_address_is_told(client, outbox) -> None:
    await _user(1, "ana", "vieja@example.com")
    assert await _login(client, "ana") == 200
    assert (await client.get("/api/auth/status")).json()["email"] == "vieja@example.com"
    r = await client.put("/api/auth/email", json={"email": "Nueva@Example.com", "current_password": "no-es-esta-123"})
    assert r.status_code == 403 and outbox == []
    assert (await client.put("/api/auth/email", json={"email": "no-es-un-email", "current_password": PASSWORD})).status_code == 422
    r = await client.put("/api/auth/email", json={"email": "Nueva@Example.com", "current_password": PASSWORD})
    assert r.status_code == 200 and r.json()["email"] == "nueva@example.com"
    assert [m[0] for m in outbox] == ["vieja@example.com"] and "ha cambiado" in outbox[0][1] and "<html" in outbox[0][3]
    r = await client.put("/api/auth/email", json={"email": "", "current_password": PASSWORD})
    assert r.status_code == 200 and r.json()["email"] is None
    assert (await client.get("/api/auth/status")).json() == {**(await client.get("/api/auth/status")).json(), "email": None, "password_reset": True}


async def test_without_a_session_the_email_cannot_be_touched(client, outbox) -> None:
    assert (await client.put("/api/auth/email", json={"email": "a@example.com", "current_password": PASSWORD})).status_code == 401


# ───────────────────────── the links ─────────────────────────
async def test_the_answer_is_the_same_whether_the_account_exists_or_not(client, outbox) -> None:
    await _user(1, "ana", "ana@example.com")
    await _user(2, "luis")                                                       # no address
    await _user(3, "admin", "ana@example.com", role="admin", is_admin=True)        # the console has its own way back
    answers = [await client.post("/api/auth/password-reset", json={"login": login}) for login in ("nadie", "luis", "ANA@example.com ")]
    assert {(r.status_code, r.text) for r in answers} == {(200, '{"status":"ok"}')}
    assert [m[0] for m in outbox] == ["ana@example.com"]
    to, subject, body, html = outbox[0]
    assert "contraseña" in subject and "ana" in body and "30 minutos" in body
    token = _token(body)
    assert f'href="https://evbro.example.com/#reset={token}"' in html                   # the button carries the same link
    assert "https://evbro.example.com/static/icons/icon-192.png" in html and "Elegir contraseña nueva" in html


async def test_the_html_escapes_what_the_user_typed(client, outbox) -> None:
    await _user(1, "<b>ana</b>", "ana@example.com")
    await client.post("/api/auth/password-reset", json={"login": "<b>ana</b>"})
    html = outbox[0][3]
    assert "<b>ana</b>" not in html and "&lt;b&gt;ana&lt;/b&gt;" in html


async def test_a_link_changes_the_password_once_and_closes_every_session(client, outbox) -> None:
    await _user(1, "ana", "ana@example.com")
    assert await _login(client, "ana") == 200
    await client.post("/api/auth/password-reset", json={"login": "ana"})
    token = _token(outbox[-1][2])
    async with async_session_factory() as s:
        stored = (await s.execute(select(PasswordReset.token_hash))).scalars().all()
    assert token not in stored and len(stored) == 1                              # only its hash is kept

    r = await client.post("/api/auth/password-reset/confirm", json={"token": token, "new_password": "corta"})
    assert r.status_code == 422                                                   # the rules refuse it: the link still works
    r = await client.post("/api/auth/password-reset/confirm", json={"token": token, "new_password": NEW})
    assert r.status_code == 200 and r.json()["username"] == "ana"
    async with async_session_factory() as s:
        assert (await s.execute(select(AuthSession).where(AuthSession.user_id == 1))).first() is None
    assert (await client.post("/api/auth/password-reset/confirm", json={"token": token, "new_password": NEW})).status_code == 410
    assert await _login(client, "ana") == 401 and await _login(client, "ana", NEW) == 200


async def test_a_new_link_voids_the_earlier_and_old_links_expire(client, outbox) -> None:
    await _user(1, "ana", "ana@example.com")
    await client.post("/api/auth/password-reset", json={"login": "ana"})
    first = _token(outbox[-1][2])
    await client.post("/api/auth/password-reset", json={"login": "ana"})
    second = _token(outbox[-1][2])
    assert (await client.post("/api/auth/password-reset/confirm", json={"token": first, "new_password": NEW})).status_code == 410
    async with async_session_factory() as s:
        await s.execute(update(PasswordReset).where(PasswordReset.used_at.is_(None))
                        .values(expires_at=datetime.datetime(2020, 1, 1)))
        await s.commit()
    assert (await client.post("/api/auth/password-reset/confirm", json={"token": second, "new_password": NEW})).status_code == 410
    assert (await client.post("/api/auth/password-reset/confirm", json={"token": "inventado", "new_password": NEW})).status_code == 410


async def test_three_links_an_hour_per_account_and_five_requests_per_ip(client, outbox) -> None:
    await _user(1, "ana", "ana@example.com")
    for _ in range(5):
        assert (await client.post("/api/auth/password-reset", json={"login": "ana"})).status_code == 200
    assert len(outbox) == 3                                                       # the rest dropped without saying so
    assert (await client.post("/api/auth/password-reset", json={"login": "ana"})).status_code == 429


async def test_deactivated_accounts_get_no_link(client, outbox) -> None:
    await _user(1, "ana", "ana@example.com", is_active=False)
    await client.post("/api/auth/password-reset", json={"login": "ana"})
    assert outbox == []


async def test_a_server_without_email_does_not_offer_it(client, monkeypatch, data_dir) -> None:
    monkeypatch.setattr(settings, "SMTP_HOST", None)
    assert (await client.get("/api/auth/status")).json()["password_reset"] is False
    assert (await client.post("/api/auth/password-reset", json={"login": "ana"})).status_code == 404
