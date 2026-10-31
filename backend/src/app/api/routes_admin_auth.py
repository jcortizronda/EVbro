"""Access to the admin console: first administrator (with the setup code), sign in/out and password. Served only by the admin console app."""
from __future__ import annotations

import asyncio
import hmac
import logging

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app import i18n
from app.db.models import AppUser
from app.db.session import get_db
from app.i18n import _
from app.security import auth, passwords
from app.security.throttle import LoginThrottle
from app.services import admin_audit

_LOGGER = logging.getLogger("app.api.admin_auth")
router = APIRouter(prefix="/auth", tags=["AdminAuth"])

# Own counters: the console and the app are different doors and must not lock each other out.
ip_throttle = LoginThrottle(max_failures=8)
pair_throttle = LoginThrottle(max_failures=5)
GENERIC_LOGIN_ERROR = "Usuario o contraseña incorrectos."


class Credentials(BaseModel):
    username: str
    password: str


class SetupRequest(Credentials):
    setup_code: str


class PasswordChange(BaseModel):
    current_password: str
    new_password: str


def _normalize(raw: str) -> str:
    return (raw or "").strip().lower()


def _check_throttle(*pairs) -> None:
    wait = max((t.retry_after(k) for t, k in pairs), default=0)
    if wait:
        raise HTTPException(status_code=429, headers={"Retry-After": str(wait)},
                            detail=_("Demasiados intentos fallidos. Espera {p0} min antes de reintentar.", p0=max(1, wait // 60)))


@router.get("/status")
async def status(request: Request, db: AsyncSession = Depends(get_db)):
    user = await auth.get_session_user(db, request.cookies.get(auth.ADMIN_COOKIE_NAME), admin=True)
    return {"authenticated": user is not None, "username": user.username if user else None,
            "setup_required": (await auth.admin_count(db)) == 0}


@router.post("/setup")
async def setup_first_admin(req: SetupRequest, request: Request, response: Response, db: AsyncSession = Depends(get_db)):
    """Creates the first administrator. Only works while there is none, and needs the setup code (printed in the log)."""
    ip = auth.client_ip(request)
    _check_throttle((ip_throttle, f"ip:{ip}"))
    if await auth.admin_count(db) > 0:
        raise HTTPException(status_code=409, detail="Ya hay un administrador configurado.")
    if not hmac.compare_digest(req.setup_code.strip().encode(), auth.get_setup_code().encode()):
        ip_throttle.record_failure(f"ip:{ip}")
        raise HTTPException(status_code=403, detail="Código de configuración incorrecto.")
    username = _normalize(req.username)
    if not 3 <= len(username) <= 40:
        raise HTTPException(status_code=422, detail="El usuario debe tener entre 3 y 40 caracteres.")
    problem = passwords.password_problem(req.password, username)
    if problem:
        raise HTTPException(status_code=422, detail=problem)
    user = AppUser(username=username, password_hash=await asyncio.to_thread(passwords.hash_password, req.password), role="admin", is_admin=True,
                   vehicle_limit=0, member_limit=0)        # the administrator owns nothing
    db.add(user)
    try:
        await db.commit()
    except IntegrityError as exc:
        await db.rollback()
        taken = (await db.execute(select(AppUser.id).where(AppUser.username == username, AppUser.is_admin.is_(True)))).first() is not None
        _LOGGER.error("No se pudo crear el administrador '%s': %s", username, exc)
        raise HTTPException(status_code=409, detail="Ya hay un administrador con ese nombre." if taken
                            else "No se pudo crear la cuenta por un conflicto en la base de datos (mira el log del servidor).") from None
    await db.refresh(user)
    token = await auth.create_session(db, user, request)
    auth.set_session_cookie(response, request, token, auth.ADMIN_COOKIE_NAME)
    _LOGGER.info("Administrador '%s' creado desde %s.", username, ip)
    return {"status": "ok", "username": username}


@router.post("/login")
async def login(req: Credentials, request: Request, response: Response, db: AsyncSession = Depends(get_db)):
    ip = auth.client_ip(request)
    username = _normalize(req.username)
    pair_key = f"{ip}|{username}"
    _check_throttle((ip_throttle, f"ip:{ip}"), (pair_throttle, pair_key))
    user = (await db.execute(select(AppUser).where(AppUser.username == username, AppUser.is_admin.is_(True)))).scalar_one_or_none()
    if user is not None:
        ok = await asyncio.to_thread(passwords.verify_password, req.password, user.password_hash)
    else:
        await asyncio.to_thread(passwords.burn_time, req.password)
        ok = False
    if ok and not await auth.account_usable(db, user):
        ok = False
    if not ok:
        ip_throttle.record_failure(f"ip:{ip}")
        pair_throttle.record_failure(pair_key)
        _LOGGER.warning("Intento de acceso fallido a la consola para '%s' desde %s", username[:40], ip)
        raise HTTPException(status_code=401, detail=GENERIC_LOGIN_ERROR)
    pair_throttle.reset(pair_key)
    token = await auth.create_session(db, user, request)
    auth.set_session_cookie(response, request, token, auth.ADMIN_COOKIE_NAME)
    auth.set_language_cookie(response, request, user.language)          # the console reloads in the administrator's language
    await admin_audit.log(db, user, "login", request=request)
    return {"status": "ok", "username": user.username, "language": user.language}


class LanguageRequest(BaseModel):
    language: str


@router.put("/language")
async def update_language(req: LanguageRequest, request: Request, response: Response, db: AsyncSession = Depends(get_db),
                          user: AppUser = Depends(auth.require_admin)):
    """The language of the console for this administrator (app.i18n); the cookie mirrors it like in the app."""
    if req.language not in i18n.SUPPORTED:
        raise HTTPException(status_code=422, detail="Idioma no admitido.")
    user.language = req.language
    await db.commit()
    auth.set_language_cookie(response, request, req.language)
    return {"status": "ok", "language": req.language}


@router.post("/logout")
async def logout(request: Request, response: Response, db: AsyncSession = Depends(get_db)):
    await auth.revoke_session(db, request.cookies.get(auth.ADMIN_COOKIE_NAME))
    auth.clear_session_cookie(response, auth.ADMIN_COOKIE_NAME)
    return {"status": "ok"}


@router.post("/password")
async def change_password(req: PasswordChange, request: Request, db: AsyncSession = Depends(get_db), user: AppUser = Depends(auth.require_admin)):
    ip = auth.client_ip(request)
    _check_throttle((ip_throttle, f"ip:{ip}"))
    if not await asyncio.to_thread(passwords.verify_password, req.current_password, user.password_hash):
        ip_throttle.record_failure(f"ip:{ip}")
        raise HTTPException(status_code=403, detail="La contraseña actual no es correcta.")
    problem = passwords.password_problem(req.new_password, user.username)
    if problem:
        raise HTTPException(status_code=422, detail=problem)
    user.password_hash = await asyncio.to_thread(passwords.hash_password, req.new_password)
    await db.commit()
    await auth.revoke_user_sessions(db, user.id, keep_token=request.cookies.get(auth.ADMIN_COOKIE_NAME))
    return {"status": "ok"}
