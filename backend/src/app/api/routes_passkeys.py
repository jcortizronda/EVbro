"""Passkeys (sign-in with the fingerprint): registering this device and managing the account's own passkeys.

Registering needs an app session (so an admin, who never has one, cannot register). The sign-in itself is public and lives in
`routes_passkey_login.py`. Ownership of `{passkey_id}` is checked by the central rule (`security/access.py`).
"""
from __future__ import annotations

import datetime
import json
import logging
from typing import Any, Dict, Optional

import webauthn
from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession
from webauthn.helpers import bytes_to_base64url, options_to_json
from webauthn.helpers.structs import (
    AuthenticatorSelectionCriteria,
    PublicKeyCredentialDescriptor,
    ResidentKeyRequirement,
    UserVerificationRequirement,
)

from app.db.models import AppUser, PasskeyCredential
from app.db.session import get_db
from app.i18n import _
from app.security import auth
from app.security import passkeys as pk

_LOGGER = logging.getLogger("app.api.passkeys")
router = APIRouter(prefix="/passkeys", tags=["Passkeys"])
MAX_PER_ACCOUNT = 20


class RegisterVerifyRequest(BaseModel):
    challenge_id: str = Field(max_length=100)
    credential: Dict[str, Any]
    label: Optional[str] = Field(default=None, max_length=120)


def _out(row: PasskeyCredential) -> Dict[str, Any]:
    return {"id": row.id, "label": row.label, "created_at": row.created_at.isoformat() if row.created_at else None,
            "last_used_at": row.last_used_at.isoformat() if row.last_used_at else None}


def options_response(challenge_id: str, options) -> Dict[str, Any]:
    return {"challenge_id": challenge_id, "options": json.loads(options_to_json(options))}


@router.get("")
async def list_passkeys(user: AppUser = Depends(auth.require_auth), db: AsyncSession = Depends(get_db)):
    rows = (await db.execute(select(PasskeyCredential).where(PasskeyCredential.user_id == user.id).order_by(PasskeyCredential.id))).scalars().all()
    return [_out(r) for r in rows]


@router.delete("/{passkey_id}")
async def delete_passkey(passkey_id: int, user: AppUser = Depends(auth.require_auth), db: AsyncSession = Depends(get_db)):
    result = await db.execute(delete(PasskeyCredential).where(PasskeyCredential.id == passkey_id, PasskeyCredential.user_id == user.id))
    await db.commit()
    if not result.rowcount:
        raise HTTPException(status_code=404, detail="No encontrado.")
    return {"status": "ok"}


@router.post("/register/options")
async def register_options(request: Request, user: AppUser = Depends(auth.require_auth), db: AsyncSession = Depends(get_db)):
    existing = (await db.execute(select(PasskeyCredential).where(PasskeyCredential.user_id == user.id))).scalars().all()
    if len(existing) >= MAX_PER_ACCOUNT:
        raise HTTPException(status_code=409, detail=_("Ya tienes {p0} dispositivos con huella. Quita alguno antes de añadir otro.", p0=MAX_PER_ACCOUNT))
    rp_id, origins = pk.relying_party(request)
    challenge_id, challenge = pk.challenges.issue("register", rp_id, origins, user_id=user.id)
    options = webauthn.generate_registration_options(
        rp_id=rp_id, rp_name=pk.RP_NAME, user_name=user.username, user_display_name=user.username, user_id=pk.user_handle(user.id),
        challenge=challenge, timeout=pk.CHALLENGE_TTL_S * 1000,
        authenticator_selection=AuthenticatorSelectionCriteria(resident_key=ResidentKeyRequirement.PREFERRED,
                                                               user_verification=UserVerificationRequirement.REQUIRED),
        exclude_credentials=[PublicKeyCredentialDescriptor(id=webauthn.base64url_to_bytes(r.credential_id)) for r in existing],
    )
    return options_response(challenge_id, options)


@router.post("/register/verify")
async def register_verify(req: RegisterVerifyRequest, request: Request, user: AppUser = Depends(auth.require_auth),
                          db: AsyncSession = Depends(get_db)):
    pending = pk.challenges.consume(req.challenge_id, "register", user_id=user.id)
    if pending is None:
        raise HTTPException(status_code=400, detail="La solicitud ha caducado. Vuelve a intentarlo.")
    try:
        verified = webauthn.verify_registration_response(
            credential=req.credential, expected_challenge=pending.challenge, expected_rp_id=pending.rp_id,
            expected_origin=list(pending.origins), require_user_verification=True)
    except Exception as exc:                                        # InvalidRegistrationResponse, malformed JSON...
        _LOGGER.warning("Passkey registration rejected for '%s': %s", user.username, exc)
        raise HTTPException(status_code=400, detail="No se pudo verificar la huella de este dispositivo.") from None

    credential_id = bytes_to_base64url(verified.credential_id)
    if (await db.execute(select(PasskeyCredential.id).where(PasskeyCredential.credential_id == credential_id))).first():
        raise HTTPException(status_code=409, detail="Este dispositivo ya está registrado.")
    transports = req.credential.get("response", {}).get("transports") if isinstance(req.credential.get("response"), dict) else None
    label = (req.label or "").strip() or pk.label_from_user_agent(request.headers.get("user-agent", ""))
    row = PasskeyCredential(user_id=user.id, credential_id=credential_id, public_key=verified.credential_public_key,
                            sign_count=verified.sign_count, label=label[:120],
                            transports=[str(t)[:20] for t in transports][:10] if isinstance(transports, list) else None,
                            created_at=datetime.datetime.utcnow())
    db.add(row)
    await db.commit()
    await db.refresh(row)
    _LOGGER.info("Passkey #%s registered for '%s' (%s).", row.id, user.username, label)
    return _out(row)
