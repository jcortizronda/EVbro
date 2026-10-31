"""Signing in to the app with a passkey (fingerprint, face or screen lock of the phone). Public, like `/api/auth/login`.

The session it creates is the same as a password login. Accounts that cannot sign in (deactivated, expired, a member of a frozen
manager) and admin accounts get the same generic error as an unknown passkey, and every failure counts for the per-IP throttle that
the password login uses.
"""
from __future__ import annotations

import datetime
import logging
from typing import Any, Dict, Optional

import webauthn
from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from webauthn.helpers import base64url_to_bytes, bytes_to_base64url
from webauthn.helpers.structs import PublicKeyCredentialDescriptor, UserVerificationRequirement

from app.api.routes_auth import _check_throttle, _normalize_username, ip_throttle
from app.api.routes_passkeys import options_response
from app.db.models import AppUser, PasskeyCredential
from app.db.session import get_db
from app.security import auth
from app.security import passkeys as pk

_LOGGER = logging.getLogger("app.api.passkeys")
router = APIRouter(prefix="/auth/passkeys", tags=["Auth"])
GENERIC_PASSKEY_ERROR = "No se pudo entrar con la huella. Usa tu usuario y contraseña."


class LoginOptionsRequest(BaseModel):
    username: Optional[str] = Field(default=None, max_length=64)


class LoginVerifyRequest(BaseModel):
    challenge_id: str = Field(max_length=100)
    credential: Dict[str, Any]


def _canonical_id(raw: Any) -> Optional[str]:
    try:
        return bytes_to_base64url(base64url_to_bytes(str(raw)))
    except Exception:
        return None


@router.post("/login/options")
async def login_options(request: Request, req: Optional[LoginOptionsRequest] = None, db: AsyncSession = Depends(get_db)):
    ip = auth.client_ip(request)
    _check_throttle((ip_throttle, f"ip:{ip}"))
    rp_id, origins = pk.relying_party(request)
    allow = None
    username = _normalize_username(req.username) if req and req.username else ""
    if username:              # optional: lets authenticators without discoverable credentials answer. Unknown name = same as no name.
        rows = (await db.execute(
            select(PasskeyCredential.credential_id).join(AppUser, AppUser.id == PasskeyCredential.user_id)
            .where(AppUser.username == username, AppUser.is_admin.is_(False), AppUser.role != "admin"))).all()
        allow = [PublicKeyCredentialDescriptor(id=base64url_to_bytes(cid)) for (cid,) in rows] or None
    challenge_id, challenge = pk.challenges.issue("login", rp_id, origins)
    options = webauthn.generate_authentication_options(
        rp_id=rp_id, challenge=challenge, timeout=pk.CHALLENGE_TTL_S * 1000, allow_credentials=allow,
        user_verification=UserVerificationRequirement.REQUIRED)
    return options_response(challenge_id, options)


@router.post("/login/verify")
async def login_verify(req: LoginVerifyRequest, request: Request, response: Response, db: AsyncSession = Depends(get_db)):
    ip = auth.client_ip(request)
    _check_throttle((ip_throttle, f"ip:{ip}"))

    def fail(reason: str) -> HTTPException:
        ip_throttle.record_failure(f"ip:{ip}")
        _LOGGER.warning("Passkey sign-in refused from %s: %s", ip, reason)
        return HTTPException(status_code=401, detail=GENERIC_PASSKEY_ERROR)

    pending = pk.challenges.consume(req.challenge_id, "login")
    if pending is None:
        raise fail("unknown, used or expired challenge")
    credential_id = _canonical_id(req.credential.get("id") or req.credential.get("rawId"))
    row = (await db.execute(select(PasskeyCredential).where(PasskeyCredential.credential_id == credential_id))).scalar_one_or_none() \
        if credential_id else None
    if row is None:
        raise fail("unknown passkey")
    user = await db.get(AppUser, row.user_id)
    if user is None or user.role == "admin" or user.is_admin:
        raise fail("passkey of an admin or missing account")

    response_part = req.credential.get("response")
    handle = response_part.get("userHandle") if isinstance(response_part, dict) else None
    if handle:
        try:
            if base64url_to_bytes(handle) != pk.user_handle(user.id):
                raise fail("user handle does not match the passkey's account")
        except HTTPException:
            raise
        except Exception:
            raise fail("malformed user handle") from None
    try:
        verified = webauthn.verify_authentication_response(
            credential=req.credential, expected_challenge=pending.challenge, expected_rp_id=pending.rp_id,
            expected_origin=list(pending.origins), credential_public_key=row.public_key,
            credential_current_sign_count=row.sign_count or 0, require_user_verification=True)
    except Exception as exc:                                    # bad signature, wrong origin/RP, sign count went backwards...
        raise fail(f"verification failed: {exc}") from None
    if not await auth.account_usable(db, user):
        raise fail("account not usable")

    row.sign_count = verified.new_sign_count
    row.last_used_at = datetime.datetime.utcnow()
    await db.commit()
    token = await auth.create_session(db, user, request)
    auth.set_session_cookie(response, request, token)
    auth.set_language_cookie(response, request, user.language)
    _LOGGER.info("User '%s' signed in with passkey #%s from %s.", user.username, row.id, ip)
    return {"status": "ok", "username": user.username}
