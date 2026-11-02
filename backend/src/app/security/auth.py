"""Access to EVBro: cookie sessions, origin check and FastAPI dependencies.

Model:
  * An EVBro user of its own (table `app_users`), distinct from the Ebro/Chery account.
  * Logging in creates a session in `auth_sessions` and the browser gets an `HttpOnly`
    cookie with a random token. The DB only stores its SHA-256: a dump of the DB
    cannot be used to impersonate sessions.
  * A session lasts `SESSION_DAYS` and is renewed with use.
  * Every `/api` route requires a session except `/api/auth/*` (and the WebSocket, which
    authenticates itself). `tests/test_api_auth.py` fails if a new unprotected route appears.
  * Anti-CSRF: the cookie is `SameSite=Lax` and, on top of that, requests that modify data are
    rejected if their `Origin` is not this server.
"""
from __future__ import annotations

import datetime
import hashlib
import logging
import secrets
from urllib.parse import urlparse

from fastapi import Depends, HTTPException, Request, Response, WebSocket
from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app import i18n
from app.config import settings
from app.db.models import AppUser, AuthSession
from app.db.session import async_session_factory, get_db

_LOGGER = logging.getLogger("app.security.auth")

COOKIE_NAME = "evbro_session"                    # the app (managers and members)
ADMIN_COOKIE_NAME = "evbro_admin_session"         # the admin console (a different port, a different cookie)
# How often `last_seen`/`expires_at` are written to the DB (not on every request).
_REFRESH_EVERY = datetime.timedelta(minutes=5)
_UNSAFE_METHODS = {"POST", "PUT", "PATCH", "DELETE"}

_setup_code: str | None = None


def _utcnow() -> datetime.datetime:
    return datetime.datetime.now(datetime.timezone.utc).replace(tzinfo=None)


def _session_lifetime() -> datetime.timedelta:
    return datetime.timedelta(days=max(1, settings.SESSION_DAYS))


def hash_token(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def client_ip(conn) -> str:
    return conn.client.host if conn.client else "desconocida"


# ───────────────────────── first-start setup code ─────────────────────────
def get_setup_code() -> str:
    """Code that has to be typed to create the first user.

    Without it, whoever reached a new internet-exposed install first would take control of the
    car. If SETUP_CODE is not set, a random one is generated on every start and only printed in
    the server log."""
    global _setup_code
    if _setup_code is None:
        _setup_code = (settings.SETUP_CODE or "").strip() or secrets.token_urlsafe(9)
    return _setup_code


def reset_setup_code() -> None:
    global _setup_code
    _setup_code = None


async def user_count(db: AsyncSession) -> int:
    return (await db.execute(select(func.count(AppUser.id)))).scalar_one()


async def admin_count(db: AsyncSession) -> int:
    return (await db.execute(select(func.count(AppUser.id)).where(AppUser.role == "admin"))).scalar_one()


async def announce_setup_if_needed() -> None:
    """Tells, in the log, how to create the administrator if there is none (from the admin console, with the setup code)."""
    async with async_session_factory() as db:
        if await admin_count(db) > 0:
            return
    where = f"la consola de administración (puerto {settings.ADMIN_PORT})" if settings.ADMIN_PORT else "la consola de administración (ADMIN_PORT está desactivado)"
    if (settings.SETUP_CODE or "").strip():
        _LOGGER.warning("No administrator yet. Open %s and create it with the configured SETUP_CODE.", where)
    else:
        _LOGGER.warning("No administrator yet. Open %s and create it with this setup code (código de configuración): %s", where, get_setup_code())


# ───────────────────────── origin (anti-CSRF) ─────────────────────────
def origin_allowed(request) -> bool:
    """Does this request come from our own web app?

    The host of `Origin` is compared with the request's (not the scheme: behind a TLS proxy the
    scheme the server sees may be http). Without `Origin` (curl, native apps) it is accepted unless
    the browser states that it comes from another site (`Sec-Fetch-Site`)."""
    origin = request.headers.get("origin")
    if origin is None:
        return request.headers.get("sec-fetch-site") in (None, "same-origin", "none")
    if origin == "null":
        return False
    host = request.headers.get("host", "")
    netloc = urlparse(origin).netloc
    if netloc and netloc == host:
        return True
    extra = {o.strip().rstrip("/") for o in settings.ALLOWED_ORIGINS.split(",") if o.strip()}
    return origin.rstrip("/") in extra


async def verify_origin(request: Request) -> None:
    if request.method in _UNSAFE_METHODS and not origin_allowed(request):
        raise HTTPException(status_code=403, detail="Origen no permitido.")


# ───────────────────────── cookie ─────────────────────────
def _cookie_secure(request: Request, admin: bool = False) -> bool:
    """The `Secure` flag of a cookie. The console is reached over plain HTTP on the local network and a "Secure" cookie sent over
    HTTP is silently dropped by the browser, so there it follows the real scheme; COOKIE_SECURE is about the public HTTPS app."""
    if admin or settings.COOKIE_SECURE is None:
        return request.url.scheme == "https"
    return settings.COOKIE_SECURE


def set_session_cookie(response: Response, request: Request, token: str, name: str = COOKIE_NAME) -> None:
    response.set_cookie(
        name, token,
        max_age=int(_session_lifetime().total_seconds()),
        httponly=True, samesite="lax", secure=_cookie_secure(request, admin=name == ADMIN_COOKIE_NAME), path="/",
    )


def clear_session_cookie(response: Response, name: str = COOKIE_NAME) -> None:
    response.delete_cookie(name, path="/")


def set_language_cookie(response: Response, request: Request, language: str, admin: bool = False) -> None:
    """The account's language, so the page itself (served before any API call) comes out in it (app.i18n). Not a secret, so
    the script may read it; same `Secure` rule as the session cookie (`admin=True` from the console)."""
    response.set_cookie(i18n.COOKIE_NAME, i18n.normalize(language), max_age=i18n.COOKIE_MAX_AGE, samesite="lax",
                        secure=_cookie_secure(request, admin), path="/")


# ───────────────────────── sessions ─────────────────────────
async def create_session(db: AsyncSession, user: AppUser, request) -> str:
    """Creates a session and returns the token (the only thing the browser will see)."""
    now = _utcnow()
    await db.execute(delete(AuthSession).where(AuthSession.expires_at < now))   # opportunistic cleanup
    token = secrets.token_urlsafe(32)
    db.add(AuthSession(
        user_id=user.id, token_hash=hash_token(token), created_at=now, last_seen=now,
        expires_at=now + _session_lifetime(), ip=client_ip(request),
        user_agent=(request.headers.get("user-agent") or "")[:255],
    ))
    user.last_login = now
    await db.commit()
    return token


async def account_usable(db: AsyncSession, user: AppUser, now: datetime.datetime | None = None) -> bool:
    """Can this account sign in and keep a session? Not deactivated, not expired, and (for members) its manager's account is usable too."""
    now = now or _utcnow()
    if not user.is_active or (user.expires_at is not None and user.expires_at <= now):
        return False
    if user.role == "member":                   # a member lives and dies with their manager's account
        parent = await db.get(AppUser, user.parent_id) if user.parent_id else None
        return parent is not None and parent.is_active and not (parent.expires_at is not None and parent.expires_at <= now)
    return True


async def get_session_user(db: AsyncSession, token: str | None, admin: bool = False) -> AppUser | None:
    """User of the session, or None if it does not exist/has expired. Renews the session with use.

    `admin` says which door asks: the app only accepts non-admin accounts and the admin console only admin accounts, so a session
    from one door is worthless at the other."""
    if not token:
        return None
    now = _utcnow()
    row = (await db.execute(
        select(AuthSession, AppUser)
        .join(AppUser, AppUser.id == AuthSession.user_id)
        .where(AuthSession.token_hash == hash_token(token))
    )).first()
    if row is None:
        return None
    sess, user = row
    if (user.role == "admin") != admin:
        return None
    if not await account_usable(db, user, now):          # deactivated/expired (or its manager's): no access, even with a valid cookie
        return None
    if sess.expires_at < now:
        await db.delete(sess)
        await db.commit()
        return None
    if now - (sess.last_seen or now) > _REFRESH_EVERY:
        sess.last_seen = now
        sess.expires_at = now + _session_lifetime()
        await db.commit()
    return user


async def revoke_session(db: AsyncSession, token: str | None) -> None:
    if token:
        await db.execute(delete(AuthSession).where(AuthSession.token_hash == hash_token(token)))
        await db.commit()


async def revoke_user_sessions(db: AsyncSession, user_id: int, keep_token: str | None = None) -> None:
    stmt = delete(AuthSession).where(AuthSession.user_id == user_id)
    if keep_token:
        stmt = stmt.where(AuthSession.token_hash != hash_token(keep_token))
    await db.execute(stmt)
    await db.commit()


# ───────────────────────── dependencies ─────────────────────────
async def require_auth(request: Request, db: AsyncSession = Depends(get_db)) -> AppUser:
    user = await get_session_user(db, request.cookies.get(COOKIE_NAME))
    if user is None:
        raise HTTPException(status_code=401, detail="No has iniciado sesión.")
    request.state.user = user
    return user


async def require_admin(request: Request, db: AsyncSession = Depends(get_db)) -> AppUser:
    """Dependency of the admin console: an administrator session, from the console's own cookie."""
    user = await get_session_user(db, request.cookies.get(ADMIN_COOKIE_NAME), admin=True)
    if user is None:
        raise HTTPException(status_code=401, detail="No has iniciado sesión.")
    request.state.user = user
    return user


async def websocket_user(websocket: WebSocket) -> AppUser | None:
    """The account behind a WebSocket, after checking its origin and session (None = reject the connection)."""
    if not origin_allowed(websocket):
        return None
    async with async_session_factory() as db:
        return await get_session_user(db, websocket.cookies.get(COOKIE_NAME))


async def authenticate_websocket(websocket: WebSocket) -> bool:
    """Checks the origin and session of a WebSocket BEFORE accepting it.

    Browsers also attach the cookie to WebSockets opened by other sites, so without the origin
    check any page open in the browser could listen to the telemetry."""
    if not origin_allowed(websocket):
        return False
    async with async_session_factory() as db:
        return await get_session_user(db, websocket.cookies.get(COOKIE_NAME)) is not None
