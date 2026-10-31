"""Passkeys (sign-in with the fingerprint): registration, sign-in, single-use challenges, isolation between accounts and refusals.

A small software authenticator (ES256, attestation "none") produces real WebAuthn responses, so the library's checks (challenge, origin,
RP ID, signature, user verification, sign count) really run.
"""
from __future__ import annotations

import datetime
import hashlib
import json
import os
import struct

import httpx
import pytest
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import ec
from sqlalchemy import select, update
from webauthn.helpers import bytes_to_base64url, encode_cbor

from app.api import routes_auth
from app.db.models import AppUser, AuthSession, PasskeyCredential
from app.db.session import async_session_factory
from app.main import app
from app.security import auth, passwords
from app.security import passkeys as pk
from app.services import tenant

PW = "una-contraseña-larga-1"
BASE = "http://localhost"


class SoftAuthenticator:
    """Enough of a platform authenticator to produce valid registration and assertion responses."""

    def __init__(self) -> None:
        self.key = ec.generate_private_key(ec.SECP256R1())
        self.cred_id = os.urandom(16)
        self.counter = 0
        self.user_handle: str | None = None

    @property
    def id(self) -> str:
        return bytes_to_base64url(self.cred_id)

    def _cose(self) -> bytes:
        n = self.key.public_key().public_numbers()
        return encode_cbor({1: 2, 3: -7, -1: 1, -2: n.x.to_bytes(32, "big"), -3: n.y.to_bytes(32, "big")})

    @staticmethod
    def _client_data(kind: str, challenge: str, origin: str) -> bytes:
        return json.dumps({"type": kind, "challenge": challenge, "origin": origin, "crossOrigin": False}).encode()

    def create(self, options: dict, origin: str = BASE) -> dict:
        self.user_handle = options["user"]["id"]
        auth_data = (hashlib.sha256(options["rp"]["id"].encode()).digest() + bytes([0x45]) + struct.pack(">I", self.counter)
                     + bytes(16) + struct.pack(">H", len(self.cred_id)) + self.cred_id + self._cose())
        att = encode_cbor({"fmt": "none", "attStmt": {}, "authData": auth_data})
        cd = self._client_data("webauthn.create", options["challenge"], origin)
        return {"id": self.id, "rawId": self.id, "type": "public-key", "clientExtensionResults": {},
                "response": {"clientDataJSON": bytes_to_base64url(cd), "attestationObject": bytes_to_base64url(att), "transports": ["internal"]}}

    def get(self, options: dict, origin: str = BASE, step: int = 1, uv: bool = True) -> dict:
        self.counter += step
        auth_data = hashlib.sha256(options["rpId"].encode()).digest() + bytes([0x05 if uv else 0x01]) + struct.pack(">I", self.counter)
        cd = self._client_data("webauthn.get", options["challenge"], origin)
        sig = self.key.sign(auth_data + hashlib.sha256(cd).digest(), ec.ECDSA(hashes.SHA256()))
        return {"id": self.id, "rawId": self.id, "type": "public-key", "clientExtensionResults": {},
                "response": {"clientDataJSON": bytes_to_base64url(cd), "authenticatorData": bytes_to_base64url(auth_data),
                             "signature": bytes_to_base64url(sig), "userHandle": self.user_handle}}


@pytest.fixture(autouse=True)
def _clean_state(data_dir):
    routes_auth.ip_throttle._fails.clear()
    routes_auth.pair_throttle._fails.clear()
    pk.challenges.clear()
    yield
    pk.challenges.clear()


@pytest.fixture
async def users(db):
    """ana (manager, 1), beto (manager, 2), mia (member of ana, 3), admin (4)."""
    h = passwords.hash_password(PW)
    async with async_session_factory() as s:
        s.add_all([
            AppUser(id=1, username="ana", role="manager", password_hash=h, vehicle_limit=2, member_limit=2),
            AppUser(id=2, username="beto", role="manager", password_hash=h, vehicle_limit=2, member_limit=2),
            AppUser(id=3, username="mia", role="member", parent_id=1, password_hash=h),
            AppUser(id=4, username="admin", role="admin", is_admin=True, password_hash=h),
        ])
        await s.commit()


def anon() -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url=BASE)


async def signed_in(username: str) -> httpx.AsyncClient:
    c = anon()
    r = await c.post("/api/auth/login", json={"username": username, "password": PW})
    assert r.status_code == 200, r.text
    return c


async def register(client: httpx.AsyncClient, authn: SoftAuthenticator, label: str | None = "Chrome en Android") -> dict:
    opts = (await client.post("/api/passkeys/register/options")).json()
    r = await client.post("/api/passkeys/register/verify", json={"challenge_id": opts["challenge_id"], "credential": authn.create(opts["options"]), "label": label})
    assert r.status_code == 200, r.text
    return r.json()


async def login(authn: SoftAuthenticator, **kw) -> httpx.Response:
    c = anon()
    try:
        opts = (await c.post("/api/auth/passkeys/login/options", json={})).json()
        return await c.post("/api/auth/passkeys/login/verify", json={"challenge_id": opts["challenge_id"], "credential": authn.get(opts["options"], **kw)})
    finally:
        await c.aclose()


# ───────────────────────── registration ─────────────────────────
async def test_register_options_need_a_session_and_ask_for_user_verification(users) -> None:
    async with anon() as c:
        assert (await c.post("/api/passkeys/register/options")).status_code == 401
        assert (await c.get("/api/passkeys")).status_code == 401
    ana = await signed_in("ana")
    try:
        body = (await ana.post("/api/passkeys/register/options")).json()
        o = body["options"]
        assert o["rp"] == {"id": "localhost", "name": "EVBro"} and o["user"]["name"] == "ana"
        assert o["authenticatorSelection"]["userVerification"] == "required" and o["authenticatorSelection"]["residentKey"] == "preferred"
        assert body["challenge_id"] and o["challenge"]
    finally:
        await ana.aclose()


async def test_register_list_and_login_creates_a_normal_session(users) -> None:
    authn = SoftAuthenticator()
    ana = await signed_in("ana")
    try:
        created = await register(ana, authn)
        assert created["label"] == "Chrome en Android" and created["last_used_at"] is None
        listed = (await ana.get("/api/passkeys")).json()
        assert [p["id"] for p in listed] == [created["id"]] and "public_key" not in listed[0] and "credential_id" not in listed[0]
        # the same device again: the options exclude it, and a forced second registration is refused
        opts = (await ana.post("/api/passkeys/register/options")).json()
        assert [c["id"] for c in opts["options"]["excludeCredentials"]] == [authn.id]
        r = await ana.post("/api/passkeys/register/verify", json={"challenge_id": opts["challenge_id"], "credential": authn.create(opts["options"])})
        assert r.status_code == 409
    finally:
        await ana.aclose()

    c = anon()
    try:
        opts = (await c.post("/api/auth/passkeys/login/options", json={})).json()
        assert "allowCredentials" not in opts["options"] or not opts["options"]["allowCredentials"]      # discoverable: no username needed
        assert opts["options"]["userVerification"] == "required"
        r = await c.post("/api/auth/passkeys/login/verify", json={"challenge_id": opts["challenge_id"], "credential": authn.get(opts["options"])})
        assert r.status_code == 200 and r.json()["username"] == "ana"
        assert auth.COOKIE_NAME in r.cookies
        status = (await c.get("/api/auth/status")).json()
        assert status["authenticated"] and status["username"] == "ana"
    finally:
        await c.aclose()
    async with async_session_factory() as s:
        row = (await s.execute(select(PasskeyCredential))).scalar_one()
        assert row.sign_count == 1 and row.last_used_at is not None
        assert (await s.execute(select(AuthSession).where(AuthSession.user_id == 1))).scalars().all()


async def test_a_registration_from_another_origin_is_refused(users) -> None:
    ana = await signed_in("ana")
    try:
        opts = (await ana.post("/api/passkeys/register/options")).json()
        cred = SoftAuthenticator().create(opts["options"], origin="https://evil.example")
        r = await ana.post("/api/passkeys/register/verify", json={"challenge_id": opts["challenge_id"], "credential": cred})
        assert r.status_code == 400
        assert (await ana.get("/api/passkeys")).json() == []
    finally:
        await ana.aclose()


async def test_username_in_login_options_lists_only_that_accounts_passkeys(users) -> None:
    authn = SoftAuthenticator()
    ana = await signed_in("ana")
    try:
        await register(ana, authn)
    finally:
        await ana.aclose()
    async with anon() as c:
        o = (await c.post("/api/auth/passkeys/login/options", json={"username": " ANA "})).json()["options"]
        assert [x["id"] for x in o["allowCredentials"]] == [authn.id]
        o = (await c.post("/api/auth/passkeys/login/options", json={"username": "nadie"})).json()["options"]
        assert not o.get("allowCredentials")


# ───────────────────────── challenges ─────────────────────────
async def test_a_challenge_is_single_use(users) -> None:
    authn = SoftAuthenticator()
    ana = await signed_in("ana")
    try:
        opts = (await ana.post("/api/passkeys/register/options")).json()
        cred = authn.create(opts["options"])
        assert (await ana.post("/api/passkeys/register/verify", json={"challenge_id": opts["challenge_id"], "credential": cred})).status_code == 200
        again = await ana.post("/api/passkeys/register/verify", json={"challenge_id": opts["challenge_id"], "credential": cred})
        assert again.status_code == 400
    finally:
        await ana.aclose()
    async with anon() as c:
        opts = (await c.post("/api/auth/passkeys/login/options", json={})).json()
        body = {"challenge_id": opts["challenge_id"], "credential": authn.get(opts["options"])}
        assert (await c.post("/api/auth/passkeys/login/verify", json=body)).status_code == 200
        body["credential"] = authn.get(opts["options"])                  # a fresh signature over the same, already used challenge
        assert (await c.post("/api/auth/passkeys/login/verify", json=body)).status_code == 401


async def test_a_failed_attempt_also_burns_the_challenge(users) -> None:
    authn = SoftAuthenticator()
    ana = await signed_in("ana")
    try:
        await register(ana, authn)
    finally:
        await ana.aclose()
    async with anon() as c:
        opts = (await c.post("/api/auth/passkeys/login/options", json={})).json()
        bad = authn.get(opts["options"], origin="https://evil.example")
        assert (await c.post("/api/auth/passkeys/login/verify", json={"challenge_id": opts["challenge_id"], "credential": bad})).status_code == 401
        good = authn.get(opts["options"])
        assert (await c.post("/api/auth/passkeys/login/verify", json={"challenge_id": opts["challenge_id"], "credential": good})).status_code == 401


async def test_an_expired_challenge_is_refused(users, monkeypatch) -> None:
    authn = SoftAuthenticator()
    ana = await signed_in("ana")
    try:
        opts = (await ana.post("/api/passkeys/register/options")).json()
        real = pk.time.monotonic
        monkeypatch.setattr(pk.time, "monotonic", lambda: real() + pk.CHALLENGE_TTL_S + 1)
        r = await ana.post("/api/passkeys/register/verify", json={"challenge_id": opts["challenge_id"], "credential": authn.create(opts["options"])})
        assert r.status_code == 400
    finally:
        await ana.aclose()


def test_challenges_are_bound_to_purpose_and_account() -> None:
    store = pk.ChallengeStore()
    cid, _ = store.issue("register", "localhost", ["http://localhost"], user_id=1)
    assert store.consume(cid, "register", user_id=2) is None                       # another account
    assert store.consume(cid, "register", user_id=1) is None                       # ...and the attempt burned it
    cid, _ = store.issue("login", "localhost", ["http://localhost"])
    assert store.consume(cid, "register", user_id=None) is None                    # a login challenge is not a registration one
    cid, _ = store.issue("login", "localhost", ["http://localhost"])
    assert store.consume(cid, "login") is not None and store.consume("nope", "login") is None


async def test_another_account_cannot_use_my_registration_challenge(users) -> None:
    ana, beto = await signed_in("ana"), await signed_in("beto")
    try:
        opts = (await ana.post("/api/passkeys/register/options")).json()
        r = await beto.post("/api/passkeys/register/verify", json={"challenge_id": opts["challenge_id"], "credential": SoftAuthenticator().create(opts["options"])})
        assert r.status_code == 400
    finally:
        await ana.aclose()
        await beto.aclose()


# ───────────────────────── isolation ─────────────────────────
async def test_a_passkey_of_one_account_cannot_be_listed_or_deleted_by_another(users) -> None:
    ana, beto = await signed_in("ana"), await signed_in("beto")
    try:
        mine = await register(ana, SoftAuthenticator())
        assert (await beto.get("/api/passkeys")).json() == []
        assert (await beto.delete(f"/api/passkeys/{mine['id']}")).status_code == 404
        assert (await beto.delete("/api/passkeys/9999")).status_code == 404
        assert len((await ana.get("/api/passkeys")).json()) == 1
        assert (await ana.delete(f"/api/passkeys/{mine['id']}")).status_code == 200
        assert (await ana.get("/api/passkeys")).json() == []
    finally:
        await ana.aclose()
        await beto.aclose()


async def test_deleting_an_account_deletes_its_passkeys(users) -> None:
    mia = await signed_in("mia")
    try:
        await register(mia, SoftAuthenticator())
    finally:
        await mia.aclose()
    async with async_session_factory() as s:
        await tenant.delete_member(s, 3)
        await s.commit()
        assert (await s.execute(select(PasskeyCredential))).first() is None


# ───────────────────────── refusals ─────────────────────────
async def _registered(username: str) -> SoftAuthenticator:
    authn = SoftAuthenticator()
    c = await signed_in(username)
    try:
        await register(c, authn)
    finally:
        await c.aclose()
    return authn


@pytest.mark.parametrize("change", [
    {"is_active": False},
    {"expires_at": datetime.datetime(2020, 1, 1)},
])
async def test_an_account_that_cannot_sign_in_cannot_use_its_passkey(users, change) -> None:
    authn = await _registered("ana")
    async with async_session_factory() as s:
        await s.execute(update(AppUser).where(AppUser.id == 1).values(**change))
        await s.commit()
    r = await login(authn)
    assert r.status_code == 401 and "huella" in r.json()["detail"]


async def test_a_member_of_a_frozen_manager_cannot_use_its_passkey(users) -> None:
    authn = await _registered("mia")
    assert (await login(authn)).status_code == 200
    async with async_session_factory() as s:
        await s.execute(update(AppUser).where(AppUser.id == 1).values(is_active=False))
        await s.commit()
    assert (await login(authn)).status_code == 401


async def test_an_admin_account_can_never_sign_in_with_a_passkey(users) -> None:
    """The admin never has an app session, so it cannot register one; even a passkey planted in the database is refused."""
    authn = SoftAuthenticator()
    ana = await signed_in("ana")
    try:
        opts = (await ana.post("/api/passkeys/register/options")).json()
        cred = authn.create(opts["options"])
        assert (await ana.post("/api/passkeys/register/verify", json={"challenge_id": opts["challenge_id"], "credential": cred})).status_code == 200
    finally:
        await ana.aclose()
    async with async_session_factory() as s:
        await s.execute(update(PasskeyCredential).values(user_id=4))
        await s.commit()
    authn.user_handle = bytes_to_base64url(pk.user_handle(4))
    r = await login(authn)
    assert r.status_code == 401 and auth.COOKIE_NAME not in r.cookies


async def test_the_user_handle_must_match_the_passkeys_account(users) -> None:
    authn = await _registered("ana")
    authn.user_handle = bytes_to_base64url(pk.user_handle(2))
    assert (await login(authn)).status_code == 401


async def test_sign_count_must_go_forward(users) -> None:
    authn = await _registered("ana")
    assert (await login(authn, step=5)).status_code == 200
    async with async_session_factory() as s:
        assert (await s.execute(select(PasskeyCredential.sign_count))).scalar_one() == 5
    authn.counter = 2                                                       # a cloned authenticator behind the real one
    assert (await login(authn)).status_code == 401


async def test_an_assertion_without_user_verification_is_refused(users) -> None:
    authn = await _registered("ana")
    assert (await login(authn, uv=False)).status_code == 401


async def test_an_unknown_passkey_is_refused(users) -> None:
    assert (await login(SoftAuthenticator())).status_code == 401


async def test_failed_passkey_attempts_are_throttled_per_ip(users) -> None:
    stranger = SoftAuthenticator()
    for _ in range(routes_auth.ip_throttle.max_failures):
        assert (await login(stranger)).status_code == 401
    async with anon() as c:
        r = await c.post("/api/auth/passkeys/login/options", json={})
        assert r.status_code == 429 and "Retry-After" in r.headers
    # ...and the shared counter also blocks the password login from that address
    async with anon() as c:
        assert (await c.post("/api/auth/login", json={"username": "ana", "password": PW})).status_code == 429


async def test_relying_party_follows_the_host_or_the_settings(monkeypatch) -> None:
    from types import SimpleNamespace

    from app.config import settings
    req = SimpleNamespace(headers={"host": "evbro.example.es"})
    assert pk.relying_party(req) == ("evbro.example.es", ["https://evbro.example.es"])
    assert pk.relying_party(SimpleNamespace(headers={"host": "localhost:8111"})) == ("localhost", ["https://localhost:8111", "http://localhost:8111"])
    monkeypatch.setattr(settings, "WEBAUTHN_RP_ID", "example.es")
    monkeypatch.setattr(settings, "WEBAUTHN_ORIGIN", "https://a.example.es, https://b.example.es/")
    assert pk.relying_party(req) == ("example.es", ["https://a.example.es", "https://b.example.es"])
