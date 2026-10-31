"""Web Push: claves VAPID, alta/baja de dispositivos, secretos cifrados y envío (sin red)."""
from __future__ import annotations

import base64

import httpx
import pytest
from sqlalchemy import select, text

from app.db.models import AppUser, NotificationLog, PushSubscription
from app.db.session import async_session_factory
from app.main import app
from app.security import auth, crypto, passwords, vapid
from app.services.notification_service import NotificationService
from tests.paths import FRONTEND, SRC

SUB = {"endpoint": "https://fcm.googleapis.com/fcm/send/abc123",
       "keys": {"p256dh": "BPkey", "auth": "authkey"}, "label": "Chrome en Android"}


class _Req:
    client = type("C", (), {"host": "127.0.0.1"})()
    headers = {"user-agent": "t"}


@pytest.fixture(autouse=True)
def _vapid_aislado(data_dir):
    vapid.reset_cache()
    yield
    vapid.reset_cache()


async def _web():
    async with async_session_factory() as s:
        user = AppUser(id=1, role="manager", username="juan", password_hash=passwords.hash_password("una-contraseña-larga-1"))
        s.add(user)
        await s.commit()
        token = await auth.create_session(s, user, _Req())
    c = httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://testserver")
    c.cookies.set(auth.COOKIE_NAME, token)
    return c


def test_clave_vapid_es_estable_y_tiene_formato_de_navegador(data_dir) -> None:
    pub = vapid.public_key_b64()
    raw = base64.urlsafe_b64decode(pub + "=" * (-len(pub) % 4))
    assert len(raw) == 65 and raw[0] == 4               # punto P-256 sin comprimir
    vapid.reset_cache()
    assert vapid.public_key_b64() == pub                # se recarga del disco, no se regenera
    assert (data_dir / "vapid_private.pem").stat().st_mode & 0o777 == 0o600


async def test_las_rutas_de_push_exigen_sesion(db) -> None:
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://testserver") as c:
        for method, url in [("get", "/api/notifications/push/public-key"), ("get", "/api/notifications/push/devices"),
                            ("post", "/api/notifications/push/subscribe"), ("delete", "/api/notifications/push/devices/1")]:
            assert (await getattr(c, method)(url)).status_code == 401, url


async def test_alta_idempotente_y_cifrada(db) -> None:
    async with await _web() as c:
        assert (await c.post("/api/notifications/push/subscribe", json=SUB)).status_code == 200
        assert (await c.post("/api/notifications/push/subscribe", json=SUB)).status_code == 200   # mismo endpoint
        devs = (await c.get("/api/notifications/push/devices")).json()
        assert len(devs) == 1 and devs[0]["label"] == "Chrome en Android"
        assert "endpoint" not in devs[0] and "keys" not in devs[0]
        assert (await c.post("/api/notifications/push/status", json={"endpoint": SUB["endpoint"]})).json() == {"subscribed": True}
    async with db.connect() as conn:
        raw = (await conn.execute(text("SELECT subscription FROM push_subscriptions"))).scalar_one()
    assert crypto.is_encrypted(raw) and "fcm.googleapis.com" not in raw


async def test_alta_rechaza_endpoints_no_https_o_sin_claves(db) -> None:
    async with await _web() as c:
        assert (await c.post("/api/notifications/push/subscribe", json={**SUB, "endpoint": "http://x/y"})).status_code == 422
        assert (await c.post("/api/notifications/push/subscribe", json={**SUB, "keys": {}})).status_code == 422


async def test_baja_por_endpoint_y_por_id(db) -> None:
    async with await _web() as c:
        await c.post("/api/notifications/push/subscribe", json=SUB)
        await c.post("/api/notifications/push/unsubscribe", json={"endpoint": SUB["endpoint"]})
        assert (await c.get("/api/notifications/push/devices")).json() == []
        await c.post("/api/notifications/push/subscribe", json=SUB)
        dev_id = (await c.get("/api/notifications/push/devices")).json()[0]["id"]
        await c.delete(f"/api/notifications/push/devices/{dev_id}")
        assert (await c.get("/api/notifications/push/devices")).json() == []


async def test_prueba_sin_dispositivos_da_un_error_claro(db) -> None:
    async with await _web() as c:
        r = await c.post("/api/notifications/test", json={"channel": "webpush"})
        assert r.status_code == 400 and "suscrito" in r.json()["detail"]


async def test_despacho_envia_registra_y_retira_las_suscripciones_caducadas(db, monkeypatch) -> None:
    enviados = []

    def falso(cls, subscription, payload):
        enviados.append((subscription["endpoint"], payload))
        if subscription["endpoint"].endswith("muerta"):
            return False, "HTTP 410: Gone", True
        return True, None, False

    monkeypatch.setattr(NotificationService, "_webpush_blocking", classmethod(falso))
    async with await _web() as c:
        await c.post("/api/notifications/push/subscribe", json=SUB)
        await c.post("/api/notifications/push/subscribe", json={**SUB, "endpoint": "https://push.example/muerta"})

    res = await NotificationService.dispatch("security", "Alarma", "Puerta abierta", user_id=1)
    assert sorted(r["success"] for r in res if r["channel"] == "webpush") == [False, True]
    assert {e for e, _ in enviados} == {SUB["endpoint"], "https://push.example/muerta"}
    assert enviados[0][1]["title"] == "Alarma" and enviados[0][1]["body"] == "Puerta abierta"

    async with async_session_factory() as s:
        quedan = (await s.execute(select(PushSubscription))).scalars().all()
        assert [q.subscription["endpoint"] for q in quedan] == [SUB["endpoint"]]   # la caducada se borró
        assert quedan[0].last_success is not None
        logs = (await s.execute(select(NotificationLog).where(NotificationLog.channel == "webpush"))).scalars().all()
        assert len(logs) == 2


async def test_un_fallo_que_no_es_de_caducidad_se_anota_pero_no_borra(db, monkeypatch) -> None:
    monkeypatch.setattr(NotificationService, "_webpush_blocking",
                        classmethod(lambda cls, s, p: (False, "HTTP 500: boom", False)))
    async with await _web() as c:
        await c.post("/api/notifications/push/subscribe", json=SUB)
    await NotificationService.dispatch("security", "t", "m", user_id=1)
    async with async_session_factory() as s:
        (sub,) = (await s.execute(select(PushSubscription))).scalars().all()
        assert "500" in sub.last_error


def test_ntfy_ha_desaparecido() -> None:
    for f in [*SRC.rglob("*.py"), *FRONTEND.rglob("*.js"), FRONTEND / "templates" / "index.html"]:
        assert "ntfy" not in f.read_text(encoding="utf-8").lower(), f
