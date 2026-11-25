"""Access to EVBro: first user, login and logout, password change."""
from __future__ import annotations

import asyncio
import logging

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Request, Response
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.db.models import AppUser
from app.db.session import async_session_factory, get_db
from app.i18n import _
from app.security import auth, passwords
from app.security.throttle import LoginThrottle
from app.services import invites, mailer, password_reset
from app.timeutil import utcnow

_LOGGER = logging.getLogger("app.api.auth")
router = APIRouter(prefix="/auth", tags=["Auth"])

# Per IP (any user) and per IP+user. There is no "global" lockout per user: that way whoever
# guesses your username cannot lock you out from anywhere.
ip_throttle = LoginThrottle(max_failures=8)
pair_throttle = LoginThrottle(max_failures=5)
# «¿Olvidaste la contraseña?»: every request counts per IP; each account gets three links an hour at most (more are dropped silently)
reset_ip_throttle = LoginThrottle(max_failures=5, window_s=900)
reset_account_throttle = LoginThrottle(max_failures=3, window_s=3600)

GENERIC_LOGIN_ERROR = "Usuario o contraseña incorrectos."


# Bounded on purpose: these routes are public and hash what they receive, so an oversized field must be refused before any work.
class Credentials(BaseModel):
    username: str = Field(max_length=64)
    password: str = Field(max_length=passwords.MAX_LENGTH)


class RegisterRequest(Credentials):
    invite_token: str = Field(max_length=200)
    accept_privacy: bool = False            # the privacy notice (/legal) must be accepted explicitly


class PasswordChange(BaseModel):
    current_password: str = Field(max_length=passwords.MAX_LENGTH)
    new_password: str = Field(max_length=passwords.MAX_LENGTH)


class EmailChange(BaseModel):
    email: str | None = Field(default=None, max_length=mailer.EMAIL_MAX_LENGTH)     # empty or null = take it away
    current_password: str = Field(max_length=passwords.MAX_LENGTH)


class ResetRequest(BaseModel):
    login: str = Field(max_length=mailer.EMAIL_MAX_LENGTH)                          # the username or the account's e-mail


class ResetConfirm(BaseModel):
    token: str = Field(max_length=200)
    new_password: str = Field(max_length=passwords.MAX_LENGTH)


def _normalize_username(raw: str) -> str:
    return (raw or "").strip().lower()


def _check_throttle(*pairs: tuple[LoginThrottle, str]) -> None:
    wait = max((t.retry_after(k) for t, k in pairs), default=0)
    if wait:
        raise HTTPException(
            status_code=429, headers={"Retry-After": str(wait)},
            detail=_("Demasiados intentos fallidos. Espera {p0} min antes de reintentar.", p0=max(1, wait // 60)))


async def _hash(password: str) -> str:
    return await asyncio.to_thread(passwords.hash_password, password)


@router.get("/status")
async def auth_status(request: Request, db: AsyncSession = Depends(get_db)):
    """Access status, so the web app knows which screen to show."""
    user = await auth.get_session_user(db, request.cookies.get(auth.COOKIE_NAME))
    return {
        "authenticated": user is not None,
        "username": user.username if user else None,
        "role": user.role if user else None,
        "language": user.language if user else None,
        "vehicle_limit": user.vehicle_limit if user and user.role == "manager" else None,
        "member_limit": user.member_limit if user and user.role == "manager" else None,
        # No manager account yet: the login screen says that the administrator creates them (from the admin console)
        "no_accounts": (await db.execute(select(func.count(AppUser.id)).where(AppUser.role != "admin"))).scalar_one() == 0,
        "registration_open": True,                       # always with a single-use invitation token
        "password_reset": mailer.enabled(),              # «¿Olvidaste la contraseña?» only when this server can send e-mail
        "email": user.email if user else None,
    }


@router.post("/register")
async def register(req: RegisterRequest, request: Request, response: Response, db: AsyncSession = Depends(get_db)):
    """Creates a manager account with an invitation token (single use, issued by the administrator). Same throttling as the login, per IP."""
    ip = auth.client_ip(request)
    _check_throttle((ip_throttle, f"ip:{ip}"))

    invite = await invites.find_valid_invite(db, req.invite_token)
    if invite is None:
        ip_throttle.record_failure(f"ip:{ip}")
        raise HTTPException(status_code=403, detail="Invitación no válida, caducada o ya usada.")
    if not req.accept_privacy:
        raise HTTPException(status_code=422, detail="Tienes que aceptar la información sobre tus datos para crear la cuenta.")
    username = _normalize_username(req.username)
    if not 3 <= len(username) <= 40:
        raise HTTPException(status_code=422, detail="El usuario debe tener entre 3 y 40 caracteres.")
    problem = passwords.password_problem(req.password, username)
    if problem:
        raise HTTPException(status_code=422, detail=problem)

    # An invitation creates a manager: the owner of their own cars and of the member accounts they create.
    user = AppUser(username=username, password_hash=await _hash(req.password), role="manager", invited_by=invite.created_by,
                   vehicle_limit=invite.vehicle_limit if invite.vehicle_limit is not None else settings.DEFAULT_VEHICLE_LIMIT,
                   member_limit=invite.member_limit if invite.member_limit is not None else settings.DEFAULT_MEMBER_LIMIT,
                   storage_limit_mb=invite.storage_limit_mb,            # None = follows the server default
                   privacy_accepted_at=utcnow())
    db.add(user)
    try:
        await db.flush()
    except IntegrityError as exc:
        await db.rollback()
        taken = (await db.execute(select(AppUser.id).where(AppUser.username == username, AppUser.is_admin.is_(False)))).first() is not None
        _LOGGER.error("Could not register '%s': %s", username, exc)
        raise HTTPException(status_code=409, detail="Ese nombre de usuario ya está en uso." if taken
                            else "No se pudo crear la cuenta por un conflicto en la base de datos (mira el log del servidor).") from None
    if not await invites.mark_used(db, invite.id, user.id):      # two people raced for the same invitation
        await db.rollback()
        raise HTTPException(status_code=403, detail="Invitación no válida, caducada o ya usada.")
    await db.commit()
    await db.refresh(user)

    token = await auth.create_session(db, user, request)
    auth.set_session_cookie(response, request, token)
    auth.set_language_cookie(response, request, user.language)
    _LOGGER.info("User '%s' registered with an invitation from admin #%s (%s).", username, invite.created_by, ip)
    return {"status": "ok", "username": username}


@router.post("/login")
async def login(req: Credentials, request: Request, response: Response, db: AsyncSession = Depends(get_db)):
    ip = auth.client_ip(request)
    username = _normalize_username(req.username)
    pair_key = f"{ip}|{username}"
    _check_throttle((ip_throttle, f"ip:{ip}"), (pair_throttle, pair_key))

    user = (await db.execute(select(AppUser).where(AppUser.username == username, AppUser.is_admin.is_(False)))).scalar_one_or_none()
    if user is not None:
        ok = await asyncio.to_thread(passwords.verify_password, req.password, user.password_hash)
    else:
        await asyncio.to_thread(passwords.burn_time, req.password)
        ok = False

    if ok and not await auth.account_usable(db, user):
        ok = False                              # deactivated, expired or frozen with its manager: same answer as a wrong password
    if not ok:
        ip_throttle.record_failure(f"ip:{ip}")
        pair_throttle.record_failure(pair_key)
        _LOGGER.warning("Failed login attempt for '%s' from %s", username[:40], ip)
        raise HTTPException(status_code=401, detail=GENERIC_LOGIN_ERROR)

    pair_throttle.reset(pair_key)
    token = await auth.create_session(db, user, request)
    auth.set_session_cookie(response, request, token)
    auth.set_language_cookie(response, request, user.language)       # the page reloads in the account's language
    return {"status": "ok", "username": user.username, "language": user.language}


@router.post("/logout")
async def logout(request: Request, response: Response, db: AsyncSession = Depends(get_db)):
    await auth.revoke_session(db, request.cookies.get(auth.COOKIE_NAME))
    auth.clear_session_cookie(response)
    return {"status": "ok"}


@router.post("/password")
async def change_password(req: PasswordChange, request: Request, db: AsyncSession = Depends(get_db),
                          user: AppUser = Depends(auth.require_auth)):
    """Changes the password and closes every other session (not the current one)."""
    ip = auth.client_ip(request)
    _check_throttle((ip_throttle, f"ip:{ip}"))
    if not await asyncio.to_thread(passwords.verify_password, req.current_password, user.password_hash):
        ip_throttle.record_failure(f"ip:{ip}")
        raise HTTPException(status_code=403, detail="La contraseña actual no es correcta.")
    problem = passwords.password_problem(req.new_password, user.username)
    if problem:
        raise HTTPException(status_code=422, detail=problem)
    user.password_hash = await _hash(req.new_password)
    await db.commit()
    await auth.revoke_user_sessions(db, user.id, keep_token=request.cookies.get(auth.COOKIE_NAME))
    return {"status": "ok"}


# ───────────────────────── the account's e-mail and «¿Olvidaste la contraseña?» ─────────────────────────
@router.put("/email")
async def change_email(req: EmailChange, request: Request, background: BackgroundTasks, db: AsyncSession = Depends(get_db),
                       user: AppUser = Depends(auth.require_auth)):
    """The address the reset links go to. Asks for the password: whoever borrows an open session must not be able to take the
    account over by pointing it at their own address. The old address is told (when this server sends e-mail)."""
    ip = auth.client_ip(request)
    _check_throttle((ip_throttle, f"ip:{ip}"))
    if not await asyncio.to_thread(passwords.verify_password, req.current_password, user.password_hash):
        ip_throttle.record_failure(f"ip:{ip}")
        raise HTTPException(status_code=403, detail=_("La contraseña no es correcta."))
    try:
        email = mailer.normalize_email(req.email)
    except ValueError:
        raise HTTPException(status_code=422, detail=_("Ese email no parece válido.")) from None
    old = user.email
    user.email = email
    await db.commit()
    if old and old != email and mailer.enabled():
        background.add_task(mailer.send, old, *password_reset.compose_email_changed(user, email))
    _LOGGER.info("Account %s %s its recovery e-mail.", user.id, "set" if email else "removed")
    return {"status": "ok", "email": email}


async def _send_reset_links(login: str, ip: str) -> None:
    """After the answer has gone (so its timing says nothing): a link to each account the login names."""
    async with async_session_factory() as db:
        for user in await password_reset.accounts_for(db, login):
            key = f"user:{user.id}"
            if reset_account_throttle.retry_after(key) or not await auth.account_usable(db, user):
                continue
            reset_account_throttle.record_failure(key)
            link = await password_reset.issue(db, user, ip)
            subject, body = password_reset.compose(user, link)
            sent = await asyncio.to_thread(mailer.send, user.email, subject, body)
            _LOGGER.info("Password reset link for account %s %s.", user.id, "sent" if sent else "NOT sent")


@router.post("/password-reset")
async def request_password_reset(req: ResetRequest, request: Request, background: BackgroundTasks):
    """Public. Always the same answer: whoever asks learns nothing about which accounts exist or have an address."""
    if not mailer.enabled():
        raise HTTPException(status_code=404, detail=_("Este servidor no envía correos: pide al administrador que te cambie la contraseña."))
    ip = auth.client_ip(request)
    _check_throttle((reset_ip_throttle, f"ip:{ip}"))
    reset_ip_throttle.record_failure(f"ip:{ip}")
    if req.login.strip():
        background.add_task(_send_reset_links, req.login, ip)
    return {"status": "ok"}


@router.post("/password-reset/confirm")
async def confirm_password_reset(req: ResetConfirm, request: Request, db: AsyncSession = Depends(get_db)):
    """Public. The new password with the link's token; every session of the account is closed."""
    ip = auth.client_ip(request)
    _check_throttle((ip_throttle, f"ip:{ip}"))
    found = await password_reset.find(db, req.token.strip())
    if found is None or not await auth.account_usable(db, found[1]):
        ip_throttle.record_failure(f"ip:{ip}")
        raise HTTPException(status_code=410, detail=_("El enlace no es válido o ha caducado. Pide otro."))
    link, user = found
    problem = passwords.password_problem(req.new_password, user.username)
    if problem:
        raise HTTPException(status_code=422, detail=problem)
    user.password_hash = await _hash(req.new_password)
    link.used_at = utcnow()
    await db.commit()
    await auth.revoke_user_sessions(db, user.id)
    _LOGGER.info("Password of account %s reset with an e-mailed link (%s).", user.id, ip)
    return {"status": "ok", "username": user.username}
