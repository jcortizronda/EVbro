"""Acceso a la API: primer usuario, login, sesiones, CSRF, WebSocket y rutas sin proteger."""
from __future__ import annotations

import asyncio
import datetime
import re

import httpx
import pytest
from sqlalchemy import select, update
from starlette.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from app.api import routes_auth
from app.config import settings
from app.db.models import AuthSession
from app.db.session import async_session_factory
from app.main import app
from app.security import auth, passwords
from tests.paths import SRC

SETUP_CODE = "codigo-de-prueba"
USER = "juan"
PASSWORD = "una-contraseña-larga-1"


@pytest.fixture(autouse=True)
def _estado_limpio(monkeypatch, data_dir):
    monkeypatch.setattr(settings, "SETUP_CODE", SETUP_CODE)
    auth.reset_setup_code()
    routes_auth.ip_throttle._fails.clear()
    routes_auth.pair_throttle._fails.clear()
    yield
    auth.reset_setup_code()


@pytest.fixture
async def client(db):
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as c:
        yield c


async def crear_usuario(client, username=USER, password=PASSWORD):
    """A manager account created straight in the database, signed in through the login (the app has no first-user setup: the
    administrator creates accounts from the admin console)."""
    from app.db.models import AppUser
    async with async_session_factory() as s:
        s.add(AppUser(id=1, username=username, role="manager", password_hash=passwords.hash_password(password), vehicle_limit=2, member_limit=5))
        await s.commit()
    r = await client.post("/api/auth/login", json={"username": username, "password": password})
    assert r.status_code == 200, r.text
    return r


# ───────────────────────── estado y puertas ─────────────────────────
async def test_estado_sin_cuentas_lo_dice(client) -> None:
    r = await client.get("/api/auth/status")
    assert r.json() == {"authenticated": False, "username": None, "role": None, "language": None, "vehicle_limit": None, "member_limit": None,
                        "no_accounts": True, "registration_open": True}


async def test_la_app_ya_no_tiene_setup_ni_rutas_de_administracion(client) -> None:
    assert (await client.post("/api/auth/setup", json={"username": USER, "password": PASSWORD, "setup_code": SETUP_CODE})).status_code in (404, 405)
    await crear_usuario(client)
    for url in ("/api/admin/users", "/api/admin/invites", "/api/settings/retention", "/api/vehicles/polling/config", "/api/server/polling"):
        assert (await client.get(url)).status_code == 404, url          # they only exist in the admin console


async def test_login_correcto_devuelve_el_estado_completo(client) -> None:
    await crear_usuario(client)
    assert (await client.get("/api/auth/status")).json() == {
        "authenticated": True, "username": USER, "role": "manager", "language": "es", "vehicle_limit": 2, "member_limit": 5,
        "no_accounts": False, "registration_open": True}


async def test_rutas_protegidas_sin_sesion_dan_401(client) -> None:
    await crear_usuario(client)
    client.cookies.clear()
    for method, path in [("GET", "/api/vehicles"), ("GET", "/api/account/status"),
                         ("GET", "/api/notifications/config"), ("GET", "/api/geofences"),
                         ("POST", "/api/vehicles/LSJA/commands")]:
        r = await client.request(method, path, json={"command": "bloquear"} if method == "POST" else None)
        assert r.status_code == 401, (method, path, r.status_code)


async def test_login_correcto_y_acceso(client) -> None:
    await crear_usuario(client)
    client.cookies.clear()
    r = await client.post("/api/auth/login", json={"username": "  JUAN ", "password": PASSWORD})
    assert r.status_code == 200
    assert (await client.get("/api/vehicles")).status_code == 200


async def test_login_incorrecto_no_distingue_usuario_de_contrasena(client) -> None:
    await crear_usuario(client)
    mal_pass = await client.post("/api/auth/login", json={"username": USER, "password": "x" * 12})
    mal_user = await client.post("/api/auth/login", json={"username": "nadie", "password": PASSWORD})
    assert mal_pass.status_code == mal_user.status_code == 401
    assert mal_pass.json() == mal_user.json()


async def test_cinco_fallos_bloquean_ese_usuario_desde_esa_ip(client) -> None:
    await crear_usuario(client)
    for _ in range(5):
        await client.post("/api/auth/login", json={"username": USER, "password": "x" * 12})
    bloqueado = await client.post("/api/auth/login", json={"username": USER, "password": PASSWORD})
    assert bloqueado.status_code == 429
    assert int(bloqueado.headers["Retry-After"]) > 0
    # otro usuario desde la misma IP no queda bloqueado por el par (solo cuenta el límite por IP)
    otro = await client.post("/api/auth/login", json={"username": "otro", "password": "x" * 12})
    assert otro.status_code == 401


async def test_logout_invalida_la_sesion(client) -> None:
    await crear_usuario(client)
    token = client.cookies.get(auth.COOKIE_NAME)
    assert (await client.post("/api/auth/logout")).status_code == 200
    client.cookies.set(auth.COOKIE_NAME, token)                      # reutilizar la cookie vieja
    assert (await client.get("/api/vehicles")).status_code == 401


async def test_sesion_caducada_se_rechaza_y_se_borra(client) -> None:
    await crear_usuario(client)
    async with async_session_factory() as s:
        await s.execute(update(AuthSession).values(
            expires_at=datetime.datetime.utcnow() - datetime.timedelta(minutes=1)))
        await s.commit()
    assert (await client.get("/api/vehicles")).status_code == 401
    async with async_session_factory() as s:
        assert (await s.execute(select(AuthSession))).first() is None


async def test_en_la_bd_solo_esta_el_hash_del_token(client) -> None:
    await crear_usuario(client)
    token = client.cookies.get(auth.COOKIE_NAME)
    async with async_session_factory() as s:
        (sesion,) = (await s.execute(select(AuthSession))).scalars().all()
    assert sesion.token_hash == auth.hash_token(token) and token not in sesion.token_hash


async def test_cambio_de_contrasena_cierra_las_otras_sesiones(client) -> None:
    await crear_usuario(client)
    otro = httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://testserver")
    async with otro:
        assert (await otro.post("/api/auth/login", json={"username": USER, "password": PASSWORD})).status_code == 200
        assert (await otro.get("/api/vehicles")).status_code == 200

        nueva = "otra-contraseña-larga-2"
        mal = await client.post("/api/auth/password", json={"current_password": "x" * 12, "new_password": nueva})
        assert mal.status_code == 403
        ok = await client.post("/api/auth/password", json={"current_password": PASSWORD, "new_password": nueva})
        assert ok.status_code == 200

        assert (await client.get("/api/vehicles")).status_code == 200      # la actual sigue
        assert (await otro.get("/api/vehicles")).status_code == 401        # la otra se cierra
    client.cookies.clear()
    assert (await client.post("/api/auth/login", json={"username": USER, "password": PASSWORD})).status_code == 401
    assert (await client.post("/api/auth/login", json={"username": USER, "password": nueva})).status_code == 200


# ───────────────────────── CSRF / origen ─────────────────────────
async def test_peticion_que_modifica_datos_desde_otro_origen_se_rechaza(client) -> None:
    await crear_usuario(client)
    r = await client.post("/api/vehicles/discover", headers={"Origin": "https://malo.example"})
    assert r.status_code == 403
    r = await client.post("/api/auth/login", json={"username": USER, "password": PASSWORD},
                          headers={"Origin": "https://malo.example"})
    assert r.status_code == 403                                       # «login CSRF»


async def test_origen_propio_y_sec_fetch_site(client) -> None:
    await crear_usuario(client)
    ok = await client.post("/api/auth/logout", headers={"Origin": "http://testserver"})
    assert ok.status_code == 200
    cruzado = await client.post("/api/auth/logout", headers={"Sec-Fetch-Site": "cross-site"})
    assert cruzado.status_code == 403


async def test_origenes_extra_permitidos_por_configuracion(client, monkeypatch) -> None:
    monkeypatch.setattr(settings, "ALLOWED_ORIGINS", "https://ebro.midominio.es")
    await crear_usuario(client)
    r = await client.post("/api/auth/logout", headers={"Origin": "https://ebro.midominio.es"})
    assert r.status_code == 200


# ───────────────────────── superficie pública ─────────────────────────
# /api/auth/register is public on purpose: it needs a single-use invitation token issued by the administrator
PUBLICAS = {"/api/auth/status", "/api/auth/login", "/api/auth/logout", "/api/auth/register",
           "/api/auth/passkeys/login/options", "/api/auth/passkeys/login/verify"}
_METODOS = ("get", "post", "put", "patch", "delete")


def _rutas_de_la_api() -> list[tuple[str, str]]:
    """(método, ruta) de TODA la API, leídas del esquema OpenAPI (API pública de FastAPI).

    Antes se recorría `app.routes`, pero las versiones nuevas de FastAPI dejan los routers
    incluidos como objetos perezosos y la lista salía vacía: el test pasaba sin comprobar nada."""
    paths = app.openapi()["paths"]
    return [(m.upper(), p) for p, ops in paths.items() if p.startswith("/api") for m in ops if m in _METODOS]


async def test_ninguna_ruta_de_la_api_queda_sin_proteger(client) -> None:
    rutas = _rutas_de_la_api()
    # Guarda contra el pase en vacío: si el esquema se leyera mal, esto falla en vez de aprobar.
    assert len(rutas) >= 40, f"Solo se han encontrado {len(rutas)} rutas; ¿cambió el esquema OpenAPI?"
    assert ("POST", "/api/vehicles/{vin}/commands") in rutas

    sin_proteger = []
    for metodo, ruta in rutas:
        if ruta in PUBLICAS:
            continue
        url = re.sub(r"\{[^}]+\}", "1", ruta)
        kwargs = {} if metodo in ("GET", "DELETE") else {"json": {}}
        r = await client.request(metodo, url, **kwargs)
        if r.status_code != 401:
            sin_proteger.append(f"{metodo} {ruta} -> {r.status_code}")
    assert not sin_proteger, f"Rutas de /api que no exigen sesión: {sin_proteger}"


def test_las_rutas_no_se_ocultan_del_esquema() -> None:
    """El test anterior solo ve lo que sale en OpenAPI: nadie debe sacar rutas de ahí."""
    api_dir = SRC / "api"
    ocultas = [f.name for f in api_dir.glob("*.py") if "include_in_schema" in f.read_text(encoding="utf-8")]
    assert not ocultas, f"Rutas fuera del esquema OpenAPI en {ocultas}: no se pueden verificar"


async def test_la_documentacion_interactiva_esta_desactivada(client) -> None:
    for path in ("/docs", "/redoc", "/openapi.json"):
        assert (await client.get(path)).status_code == 404


async def test_cabeceras_de_seguridad(client) -> None:
    r = await client.get("/api/auth/status")
    assert r.headers["x-content-type-options"] == "nosniff"
    assert r.headers["x-frame-options"] == "DENY"
    assert r.headers["cache-control"] == "no-store"
    # Las teselas de OpenStreetMap exigen Referer: con `same-origin`/`no-referrer` el mapa se llena de «Access blocked»
    assert r.headers["referrer-policy"] == "strict-origin-when-cross-origin"


async def test_health_y_la_web_siguen_siendo_publicos(client) -> None:
    assert (await client.get("/health")).status_code == 200
    assert (await client.get("/")).status_code == 200
    assert (await client.get("/manifest.json")).status_code == 200


# ───────────────────────── nada sensible en las respuestas ─────────────────────────
async def test_la_api_no_devuelve_secretos(client, db) -> None:
    from app.db.models import EbroAccount, NotificationConfig
    async with async_session_factory() as s:
        s.add(EbroAccount(phone="600123456", password="pass-ebro", pin="4321", is_active=True))
        s.add(NotificationConfig(user_id=1, telegram_enabled=True, telegram_bot_token="123456789:AAH-token-muy-secreto",
                                 pushover_api_token="pushover-secreto-largo", webhook_url="https://h.example/x/abc"))
        await s.commit()
    await crear_usuario(client)

    cuenta = (await client.get("/api/account/status")).text
    assert "600123456" not in cuenta and "pass-ebro" not in cuenta and "4321" not in cuenta

    notif = await client.get("/api/notifications/config")
    assert "AAH-token-muy-secreto" not in notif.text
    assert "pushover-secreto-largo" not in notif.text
    assert "/x/abc" not in notif.text
    assert notif.json()["telegram_bot_token_set"] is True


async def test_guardar_el_valor_enmascarado_no_borra_el_secreto(client, db) -> None:
    from app.db.models import NotificationConfig
    async with async_session_factory() as s:
        s.add(NotificationConfig(user_id=1, telegram_bot_token="123456789:AAH-token-muy-secreto"))
        await s.commit()
    await crear_usuario(client)
    cfg = (await client.get("/api/notifications/config")).json()
    guardado = await client.put("/api/notifications/config", json={"telegram_bot_token": cfg["telegram_bot_token"]})
    assert guardado.status_code == 200
    async with async_session_factory() as s:
        fila = (await s.execute(select(NotificationConfig))).scalar_one()
        assert fila.telegram_bot_token == "123456789:AAH-token-muy-secreto"


# ───────────────────────── contraseñas ─────────────────────────
def test_hash_y_verificacion_de_contrasenas() -> None:
    h = passwords.hash_password("una-clave-de-prueba")
    assert h.startswith("scrypt$") and "una-clave" not in h
    assert passwords.verify_password("una-clave-de-prueba", h)
    assert not passwords.verify_password("otra", h)
    assert h != passwords.hash_password("una-clave-de-prueba")          # sal distinta
    for roto in (None, "", "texto", "scrypt$1$2", "bcrypt$1$2$3$4$5"):
        assert passwords.verify_password("x", roto) is False


# ───────────────────────── WebSocket ─────────────────────────
@pytest.fixture
def ws_client():
    from app.db.models import Base
    from app.db.session import engine
    from tests.conftest import asegurar_bd_de_test

    asegurar_bd_de_test(engine)

    async def _reset():
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.drop_all)
            await conn.run_sync(Base.metadata.create_all)
    asyncio.run(_reset())
    with TestClient(app) as c:                      # arranca el lifespan (poller incluido)
        yield c
    asyncio.run(engine.dispose())


def test_websocket_sin_sesion_se_rechaza(ws_client) -> None:
    with pytest.raises(WebSocketDisconnect) as exc:
        with ws_client.websocket_connect("/api/ws"):
            pass
    assert exc.value.code == 4401


def test_websocket_con_sesion_conecta_y_con_otro_origen_no(ws_client) -> None:
    import asyncio as _a

    from app.db.models import AppUser
    from app.db.session import async_session_factory as _f

    async def _mk():
        async with _f() as s:
            s.add(AppUser(id=1, username=USER, role="manager", password_hash=passwords.hash_password(PASSWORD), vehicle_limit=2, member_limit=5))
            await s.commit()
    _a.run(_mk())
    r = ws_client.post("/api/auth/login", json={"username": USER, "password": PASSWORD})
    assert r.status_code == 200
    with ws_client.websocket_connect("/api/ws") as ws:
        ws.send_text("ping")
        assert ws.receive_text() == "pong"
    with pytest.raises(WebSocketDisconnect) as exc:
        with ws_client.websocket_connect("/api/ws", headers={"Origin": "https://malo.example"}):
            pass
    assert exc.value.code == 4401
