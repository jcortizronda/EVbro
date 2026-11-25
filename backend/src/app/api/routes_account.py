"""Routes to manage and authenticate Ebro / Chery accounts from the web interface."""
from __future__ import annotations

import asyncio
import hmac
import logging
import re
import time
from typing import Any, Dict, List, Literal, Optional

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.routes_auth import _check_throttle, ip_throttle
from app.config import settings
from app.core import ebro_account, ebro_login, ebro_reset
from app.core.errors import CommandError
from app.db.models import AppUser, EbroAccount, MqttLink, Vehicle
from app.db.session import get_db
from app.i18n import _
from app.security import auth, passwords
from app.security.access import foreign_vins, is_root, space_owner_id, within_vehicle_limit
from app.security.throttle import LoginThrottle
from app.services import command_policy, exit_survey, mqtt_hub, storage, tenant, vehicle_profiles
from app.services.accounts import (
    account_ctx,
    change_account_password,
    change_account_pin,
    fetch_account_vehicles,
    reset_captcha,
    reset_password_with_code,
    reset_pin_with_code,
    reset_send_sms,
)
from app.services.car_events import CarEventHub
from app.services.command_policy import pin_throttle, pin_throttle_key
from app.services.ebro_sessions import account_session_path
from app.services.mqtt_hub import MqttHub, MqttLinkConfig
from app.services.poller import VehiclePoller
from app.timeutil import utcnow

_LOGGER = logging.getLogger("app.api.account")
router = APIRouter(prefix="/account", tags=["Account"])
HOME_WIDGET_KEY = re.compile(r"[a-z][a-z_]{0,23}")


class EbroLoginRequest(BaseModel):
    phone: str = Field(max_length=32)
    password: str = Field(max_length=128)
    pin: Optional[str] = Field(default=None, max_length=32)
    area_code: str = Field(default="34", max_length=5)


def mask_phone(phone: str) -> str:
    """Masks the phone number for visual privacy (e.g. +34 6** *** 890)."""
    clean = phone.strip()
    if len(clean) >= 9:
        return f"{clean[:3]} {'*' * (len(clean) - 6)} {clean[-3:]}"
    return clean


@router.get("/status")
async def get_account_status(session: AsyncSession = Depends(get_db), user: AppUser = Depends(auth.require_auth)):
    """Returns the connection status of this user's official Ebro account."""
    stmt = select(EbroAccount).where(EbroAccount.is_active == True, EbroAccount.user_id == user.id).order_by(EbroAccount.id.desc())  # noqa: E712
    account = (await session.execute(stmt)).scalars().first()

    # Only the cars this account owns that come from the Ebro account (cars added by hand are not part of it)
    v_stmt = select(Vehicle).where(Vehicle.owner_id == user.id, Vehicle.is_manual == False)  # noqa: E712
    vehicles = (await session.execute(v_stmt)).scalars().all()

    if account:
        return {
            "connected": True,
            # False while the cloud rejects the stored session and password: the manager has to connect the account again
            "session_ok": not VehiclePoller.get_instance().session_lost(account.id, account.last_login),
            "source": "web_login",
            "phone": mask_phone(account.phone),
            "area_code": account.area_code,
            "has_pin": bool(account.pin) or bool(settings.EBRO_PIN),
            "pin_policy": user.pin_policy or command_policy.DEFAULT_POLICY,
            "last_login": account.last_login.isoformat() if account.last_login else None,
            "vehicles_count": len(vehicles),
            "vehicles": [{"vin": v.vin, "name": v.name, "model": v.model} for v in vehicles],
        }

    # if there is none in the DB, check whether it was set through an environment variable (the server account belongs to the administrator)
    if user.role == "admin" and settings.EBRO_PHONE and settings.EBRO_PASSWORD:
        return {
            "connected": True,
            "source": "env_variables",
            "phone": mask_phone(settings.EBRO_PHONE),
            "area_code": settings.EBRO_AREA_CODE,
            "has_pin": bool(settings.EBRO_PIN),
            "pin_policy": user.pin_policy or command_policy.DEFAULT_POLICY,
            "last_login": None,
            "vehicles_count": len(vehicles),
            "vehicles": [{"vin": v.vin, "name": v.name, "model": v.model} for v in vehicles],
        }

    return {
        "connected": False, "pin_policy": user.pin_policy or command_policy.DEFAULT_POLICY,
        "source": None,
        "phone": None,
        "vehicles_count": len(vehicles),
        "vehicles": [{"vin": v.vin, "name": v.name, "model": v.model} for v in vehicles],
    }


@router.get("/storage")
async def get_storage(session: AsyncSession = Depends(get_db), user: AppUser = Depends(auth.require_auth)):
    """Space taken by the files (documents, invoices, photos) of the manager's cars and their limit in MB (-1 = unlimited).
    A member sees the numbers of their manager, whose space they upload to."""
    owner_id = space_owner_id(user)
    owner = await session.get(AppUser, owner_id) if owner_id is not None else None
    if owner is None or not is_root(owner):
        return {"used_bytes": 0, "limit_mb": 0}
    return {"used_bytes": await storage.storage_usage(session, owner.id), "limit_mb": storage.effective_limit_mb(owner)}


def cloud_refusal(text: str) -> str:
    """The reason the Ebro cloud gives for refusing a login, without the «HTTP 401: » prefix and without anything that could be a
    token (the text is shown to the manager)."""
    reason = text.split("access_token")[0].strip()
    if reason.upper().startswith("HTTP ") and ":" in reason:
        reason = reason.split(":", 1)[1].strip()
    return (reason or "credenciales rechazadas")[:160]


async def _own_vins(session: AsyncSession, user_id: int) -> set:
    return {v for (v,) in (await session.execute(select(Vehicle.vin).where(Vehicle.owner_id == user_id))).all()}


@router.post("/login")
async def login_ebro_account(req: EbroLoginRequest, session: AsyncSession = Depends(get_db), user: AppUser = Depends(auth.require_auth)):
    """Logs in with the official Ebro account, stores the credentials in the DB and discovers vehicles."""
    phone_clean = req.phone.strip().replace(" ", "").replace("-", "")
    area = req.area_code.strip().replace("+", "")
    if (req.pin or "").strip() and not ebro_account.pin_format_ok(req.pin.strip()):
        raise HTTPException(status_code=422, detail=_("El PIN de Ebro tiene 6 dígitos."))

    # if the user typed the phone with the +34 prefix in front, normalise it
    if phone_clean.startswith(f"+{area}"):
        phone_clean = phone_clean[len(area) + 1 :]
    elif phone_clean.startswith(area) and len(phone_clean) > 9:
        phone_clean = phone_clean[len(area) :]

    _LOGGER.info(f"Trying to log in to the Ebro cloud for phone: +{area} {mask_phone(phone_clean)}")

    def _do_auth():
        return ebro_login.login_with_password(phone_clean, req.password, area)

    loop = asyncio.get_running_loop()
    try:
        token_data = await loop.run_in_executor(None, _do_auth)
    except RuntimeError as e:
        # The cloud answered and said no (`login_with_password` raises with its text, e.g. «HTTP 401: El nombre de usuario no
        # existe o la contraseña es incorrecta»): a typo of the manager, not a failure of EVBro — it used to come out as a 502
        # «could not connect» and an error in the log (09-11-2026)
        _LOGGER.warning("The Ebro cloud refused the login of user %s: %s", user.id, cloud_refusal(str(e)),
                        extra={"telemetry": False})          # a mistyped password is not a fault to report to the project
        raise HTTPException(
            status_code=401,
            detail=_("No se pudo iniciar sesión: {p0}. Verifica tu teléfono, código de país y contraseña.", p0=cloud_refusal(str(e))),
        ) from e
    except Exception as e:
        _LOGGER.error(f"Network or cryptography error on Ebro login: {e}")
        # The exception text can name internal hosts or library internals: it stays in the log.
        raise HTTPException(
            status_code=502,
            detail="No se pudo conectar con el servidor de Ebro. Inténtalo de nuevo en unos minutos.",
        ) from e

    if not token_data or "access_token" not in token_data:
        err_msg = token_data.get("msg") or token_data.get("error_description") or "Credenciales incorrectas"
        raise HTTPException(
            status_code=401,
            detail=_("No se pudo iniciar sesión: {p0}. Verifica tu teléfono, código de país y contraseña.", p0=err_msg),
        )

    # 1. save or update the account in the database
    acc_stmt = select(EbroAccount).where(EbroAccount.phone == phone_clean)
    existing_acc = (await session.execute(acc_stmt)).scalar_one_or_none()
    if existing_acc is not None and existing_acc.user_id not in (None, user.id):
        raise HTTPException(status_code=409, detail="Esa cuenta de Ebro ya está conectada por otro usuario de este servidor.")

    now = utcnow()
    if existing_acc:
        existing_acc.password = req.password
        existing_acc.pin = req.pin
        existing_acc.area_code = area
        existing_acc.is_active = True
        existing_acc.token_data = token_data
        existing_acc.last_login = now
        existing_acc.user_id = existing_acc.user_id or user.id
        account_obj = existing_acc
    else:
        account_obj = EbroAccount(
            phone=phone_clean,
            password=req.password,
            pin=req.pin,
            area_code=area,
            is_active=True,
            token_data=token_data,
            last_login=now,
            user_id=user.id,
        )
        session.add(account_obj)

    await session.commit()
    await session.refresh(account_obj)

    # 2. discover vehicles with the session just obtained, kept in the account's file that all its cars share (ebro_sessions.py)
    try:
        cars_list = await loop.run_in_executor(None, fetch_account_vehicles, token_data, await foreign_vins(session, user.id),
                                               account_session_path(account_obj.id))
    except Exception as e:
        _LOGGER.error(f"Error listing vehicles after login: {e}")
        cars_list = []

    discovered = []
    poller = VehiclePoller.get_instance()

    for item in cars_list:
        vin = item.get("vin")
        if not vin:
            continue

        nickname = item.get("nickname") or item.get("fullName") or item.get("modelName") or "Ebro"
        model = item.get("fullName") or item.get("modelName") or "Ebro Auto"
        brand = item.get("brandName") or "Ebro"
        car_tok = item.get("carToken") or item.get("tboxSn")

        v_stmt = select(Vehicle).where(Vehicle.vin == vin)
        v_exist = (await session.execute(v_stmt)).scalar_one_or_none()

        if v_exist is not None and v_exist.owner_id not in (None, account_obj.user_id):
            continue                                              # somebody else's car: never touched from this account
        if v_exist is None and not await within_vehicle_limit(session, account_obj.user_id):
            continue                                              # the manager reached the vehicle limit
        if not v_exist:
            v_new = Vehicle(
                owner_id=account_obj.user_id,
                vin=vin,
                name=nickname,
                model=model,
                brand=brand,
                car_token=car_tok,
            )
            vehicle_profiles.fill_defaults(v_new)            # battery, tank and label of the model; the owner can change them
            session.add(v_new)
            discovered.append({"vin": vin, "name": nickname, "model": model, "is_new": True})
        else:
            v_exist.owner_id = v_exist.owner_id or account_obj.user_id
            v_exist.name = nickname
            v_exist.model = model
            v_exist.car_token = car_tok
            discovered.append({"vin": vin, "name": nickname, "model": model, "is_new": False})

        v_ctx = poller._get_or_create_ctx(vin)
        # the PIN of the newly connected account replaces the previous one (empty if none is given) and the
        # anti-lockout is reset: these are new credentials, not retries of the old ones.
        v_ctx.pin = (req.pin or "").strip()
        v_ctx.reset_pin_lockout()

    # if real vehicles were discovered, remove the dummy demo vehicle if it existed
    if discovered:
        dummy_stmt = select(Vehicle).where(Vehicle.vin == "LSJA8888888888888")
        dummy_v = (await session.execute(dummy_stmt)).scalar_one_or_none()
        if dummy_v and dummy_v.owner_id in (None, account_obj.user_id):
            await session.delete(dummy_v)

    await session.commit()

    # every car of the account on its session before anything reads the cloud with it
    await poller.bind_sessions()
    # new account: the car's MQTT channel reconnects with it (in the background: it takes a while)
    asyncio.create_task(CarEventHub.get_instance().restart_owner(account_obj.user_id, poller))

    # if there are vehicles, trigger an initial poll
    if discovered:
        asyncio.create_task(_first_read(poller, discovered[0]["vin"]))

    return {
        "status": "ok",
        "message": f"Sesión iniciada con éxito. Se han vinculado {len(discovered)} vehículo(s).",
        "account": {
            "phone": mask_phone(phone_clean),
            "area_code": area,
            "has_pin": bool(req.pin),
        },
        "vehicles": discovered,
    }


FIRST_READ_RETRY_S = 20


async def _first_read(poller: VehiclePoller, vin: str) -> None:
    """The first read of a car just connected. A token the cloud has just issued is sometimes refused for a few seconds (A00000 on
    the three endpoints 2 s after connecting, fine 20 s later; 08-11-2026): an empty first read is tried once more."""
    if await poller.poll_vehicle(vin, force=True) is None:
        await asyncio.sleep(FIRST_READ_RETRY_S)
        await poller.poll_vehicle(vin, force=True)


class UpdatePinRequest(BaseModel):
    pin: str = Field(max_length=32)


@router.post("/pin")
async def update_account_pin(req: UpdatePinRequest, session: AsyncSession = Depends(get_db), user: AppUser = Depends(auth.require_auth)):
    """Saves or updates the remote control PIN of the active account."""
    stmt = select(EbroAccount).where(EbroAccount.is_active == True, EbroAccount.user_id == user.id).order_by(EbroAccount.id.desc())  # noqa: E712
    account = (await session.execute(stmt)).scalars().first()
    if not account:
        raise HTTPException(status_code=404, detail="No hay ninguna cuenta Ebro conectada.")

    clean_pin = req.pin.strip() if req.pin else None
    if clean_pin and not ebro_account.pin_format_ok(clean_pin):
        raise HTTPException(status_code=422, detail=_("El PIN de Ebro tiene 6 dígitos."))
    account.pin = clean_pin
    await session.commit()

    # changing the PIN is an explicit remedy: the anti-lockout and the cached taskId (tied to the
    # previous PIN) are reset, just as the integration did when the PIN was reconfigured.
    poller = VehiclePoller.get_instance()
    own = await _own_vins(session, user.id)
    for vin, ctx in poller._contexts.items():
        if vin in own:                                       # a PIN only reaches the cars of the account it belongs to
            ctx.pin = clean_pin or ""
            ctx.reset_pin_lockout()
    for vin in own:                                          # the retry limit is per car: only this account's cars are released
        pin_throttle.reset(pin_throttle_key(vin))

    return {
        "status": "ok",
        "has_pin": bool(clean_pin),
        "message": "PIN de control remoto actualizado con éxito."
    }


class EbroPasswordChange(BaseModel):
    current_password: str = Field(max_length=128)
    new_password: str = Field(max_length=128)


# Failed changes per Ebro account: the cloud blocks the account after a few wrong passwords (5 minutes after five, docs/security.md)
ebro_password_throttle = LoginThrottle(max_failures=2, window_s=600)


@router.post("/ebro-password")
async def change_ebro_password(req: EbroPasswordChange, session: AsyncSession = Depends(get_db),
                               user: AppUser = Depends(auth.require_auth)):
    """Changes the password of the connected Ebro account in the Ebro cloud (the one the official app uses) and keeps the new one,
    with a new session for every car of the account. Tried against the real cloud on 24-11-2026 (docs/ebro-protocol.md)."""
    stmt = select(EbroAccount).where(EbroAccount.is_active == True, EbroAccount.user_id == user.id).order_by(EbroAccount.id.desc())  # noqa: E712
    account = (await session.execute(stmt)).scalars().first()
    if not account:
        raise HTTPException(status_code=404, detail="No hay ninguna cuenta Ebro conectada.")
    key = f"ebro-password:{account.id}"
    _check_throttle((ebro_password_throttle, key))
    if not ebro_account.password_format_ok(req.new_password):
        raise HTTPException(status_code=422, detail=_("La contraseña nueva debe tener de 8 a 16 caracteres, con letras, números y algún carácter especial."))
    if req.new_password == req.current_password:
        raise HTTPException(status_code=422, detail=_("La contraseña nueva es igual que la actual."))
    # The one EVBro keeps is the one that works (the session opens with it): a different one is a typo, refused here without
    # spending one of the attempts the cloud allows before blocking the account
    if account.password and not hmac.compare_digest(req.current_password.encode(), account.password.encode()):
        ebro_password_throttle.record_failure(key)
        raise HTTPException(status_code=422, detail=_("La contraseña actual no coincide con la de la cuenta de Ebro conectada."))

    loop = asyncio.get_running_loop()
    try:
        outcome = await loop.run_in_executor(None, change_account_password, account_session_path(account.id), account.phone,
                                             account.area_code or "34", req.current_password, req.new_password)
    except RuntimeError as e:
        if str(e) != "no_usertoken":
            raise
        raise HTTPException(status_code=424, detail=_("La sesión de la cuenta de Ebro no abre: vuelve a conectarla antes de cambiar la contraseña.")) from e
    except Exception as e:
        _LOGGER.error("Network error changing the Ebro password of account %s: %s", account.id, type(e).__name__)
        raise HTTPException(status_code=502, detail="No se pudo conectar con el servidor de Ebro. Inténtalo de nuevo en unos minutos.") from e

    if not outcome["changed"]:
        ebro_password_throttle.record_failure(key)
        _LOGGER.warning("The Ebro cloud refused the password change of account %s: %s", account.id, outcome["reason"],
                        extra={"telemetry": False})
        raise HTTPException(status_code=424, detail=_("Ebro no ha cambiado la contraseña: {p0}", p0=outcome["reason"] or "sin motivo"))

    ebro_password_throttle.reset(key)
    account.password = req.new_password
    if outcome["token_data"]:
        account.token_data = outcome["token_data"]
        account.last_login = utcnow()
    await session.commit()
    _LOGGER.info("Ebro password changed for account %s (new session: %s)", account.id, bool(outcome["token_data"]))
    if outcome["token_data"]:
        poller = VehiclePoller.get_instance()
        await poller.bind_sessions()
        asyncio.create_task(CarEventHub.get_instance().restart_owner(account.user_id, poller))
    return {"status": "ok", "session_renewed": bool(outcome["token_data"])}


class EbroPinChange(BaseModel):
    vin: str = Field(max_length=32)
    current_pin: str = Field(max_length=8)
    new_pin: str = Field(max_length=8)


# Failed PIN changes per Ebro account (on top of the core's own wrong-PIN lockout, which counts the cloud's refusals per car)
ebro_pin_throttle = LoginThrottle(max_failures=2, window_s=600)


@router.post("/ebro-pin")
async def change_ebro_pin(req: EbroPinChange, request: Request, session: AsyncSession = Depends(get_db),
                          user: AppUser = Depends(auth.require_auth)):
    """Changes the car-control PIN of the connected Ebro account IN THE EBRO CLOUD (the one the official app asks for), through one
    of the account's cars. Not the PIN stored in EVBro (`POST /pin`), which is only EVBro's copy: that copy, if there is one, is
    replaced by the new PIN once the cloud accepts it. Worked against the real cloud on 24-11-2026 (docs/ebro-protocol.md)."""
    stmt = select(EbroAccount).where(EbroAccount.is_active == True, EbroAccount.user_id == user.id).order_by(EbroAccount.id.desc())  # noqa: E712
    account = (await session.execute(stmt)).scalars().first()
    if not account:
        raise HTTPException(status_code=404, detail="No hay ninguna cuenta Ebro conectada.")
    car = (await session.execute(select(Vehicle).where(Vehicle.vin == req.vin, Vehicle.owner_id == user.id,
                                                       Vehicle.is_manual == False))).scalar_one_or_none()  # noqa: E712
    if car is None:
        raise HTTPException(status_code=404, detail=_("Ese coche no es de tu cuenta de Ebro."))
    key = f"ebro-pin:{account.id}"
    _check_throttle((ebro_pin_throttle, key))
    current, new = req.current_pin.strip(), req.new_pin.strip()
    if not ebro_account.pin_format_ok(new):
        raise HTTPException(status_code=422, detail=_("El PIN nuevo tiene que tener 6 dígitos."))
    if not current.isdigit():
        raise HTTPException(status_code=422, detail=_("Escribe el PIN actual de Ebro."))
    if new == current:
        raise HTTPException(status_code=422, detail=_("El PIN nuevo es igual que el actual."))
    # The current PIN is checked by the Ebro cloud (`checkPassword`), as the official app does; EVBro's stored copy is not required
    # to match it (24-11-2026: it was, and it got in the way). The wrong-PIN lockout and the throttle below keep the account safe.

    poller = VehiclePoller.get_instance()
    ctx = poller._get_or_create_ctx(car.vin)
    ip = auth.client_ip(request)
    loop = asyncio.get_running_loop()
    try:
        outcome = await loop.run_in_executor(None, change_account_pin, ctx, current, new)
    except RuntimeError as e:
        if str(e) != "no_usertoken":
            raise
        raise HTTPException(status_code=424, detail=_("La sesión de la cuenta de Ebro no abre: vuelve a conectarla antes de cambiar el PIN.")) from e
    except CommandError as e:
        ebro_pin_throttle.record_failure(key)
        await poller._audit_command(car.vin, "cambiar_pin_ebro", None, False, e.code or e.reason, str(e), ip)
        raise HTTPException(status_code=424, detail=_("Ebro no ha aceptado el PIN actual: {p0}", p0=str(e))) from e
    except Exception as e:
        _LOGGER.error("Network error changing the Ebro PIN of account %s: %s", account.id, type(e).__name__)
        raise HTTPException(status_code=502, detail="No se pudo conectar con el servidor de Ebro. Inténtalo de nuevo en unos minutos.") from e

    if not outcome["changed"]:
        ebro_pin_throttle.record_failure(key)
        await poller._audit_command(car.vin, "cambiar_pin_ebro", None, False, str(outcome["code"]), outcome["reason"], ip)
        if outcome["unconfirmed"]:
            raise HTTPException(status_code=424, detail=_("Ebro respondió sin error, pero todavía no acepta el PIN nuevo. Prueba en unos minutos una orden con el PIN de siempre; EVBro no ha cambiado el PIN guardado."))
        raise HTTPException(status_code=424, detail=_("Ebro no ha cambiado el PIN: {p0}", p0=outcome["reason"] or "sin motivo"))

    ebro_pin_throttle.reset(key)
    await poller._audit_command(car.vin, "cambiar_pin_ebro", None, True, str(outcome["code"]), "PIN de Ebro cambiado", ip)
    stored = await _adopt_new_pin(session, account, user, poller, new)
    _LOGGER.info("Ebro PIN changed for account %s through %s (stored copy updated: %s)", account.id, car.vin[-6:], stored)
    return {"status": "ok", "stored_pin_updated": stored}


async def _adopt_new_pin(session: AsyncSession, account: EbroAccount, user: AppUser, poller: VehiclePoller, new: str) -> bool:
    """The cloud has a new PIN: EVBro's copy (if there is one) becomes it, and the account's cars drop their lockout and the taskId
    minted with the old one. Returns whether there was a copy."""
    stored = bool(account.pin)
    if stored:
        account.pin = new
        await session.commit()
    for vin in await _own_vins(session, user.id):
        own_ctx = poller._contexts.get(vin)
        if own_ctx is not None:
            if stored:
                own_ctx.pin = new
            own_ctx.reset_pin_lockout()
        pin_throttle.reset(pin_throttle_key(vin))
    return stored


# ───────────────────────── Resetting the Ebro password or PIN with a code by SMS ─────────────────────────
# The cloud only sends the SMS after a puzzle captcha (core/ebro_reset.py). The puzzle waiting for an answer is kept here, per
# manager, with its secret key (never sent to the browser); each one is good for one try.
RESET_PURPOSES = {"pin": ebro_reset.MODULE_PIN, "password": ebro_reset.MODULE_PASSWORD}
CAPTCHA_MAX_AGE_S = 300
_pending_captcha: Dict[int, Dict[str, Any]] = {}
ebro_sms_throttle = LoginThrottle(max_failures=3, window_s=600)       # SMS sent per account
ebro_code_throttle = LoginThrottle(max_failures=3, window_s=600)      # wrong SMS codes per account


async def _reset_account(session: AsyncSession, user: AppUser) -> EbroAccount:
    stmt = select(EbroAccount).where(EbroAccount.is_active == True, EbroAccount.user_id == user.id).order_by(EbroAccount.id.desc())  # noqa: E712
    account = (await session.execute(stmt)).scalars().first()
    if not account:
        raise HTTPException(status_code=404, detail="No hay ninguna cuenta Ebro conectada.")
    return account


def _no_session(e: RuntimeError) -> HTTPException:
    if str(e) != "no_usertoken":
        raise e
    return HTTPException(status_code=424, detail=_("La sesión de la cuenta de Ebro no abre: vuelve a conectarla antes de restablecer."))


def _network(e: Exception, what: str) -> HTTPException:
    _LOGGER.error("Network error in the Ebro reset (%s): %s", what, type(e).__name__)
    return HTTPException(status_code=502, detail="No se pudo conectar con el servidor de Ebro. Inténtalo de nuevo en unos minutos.")


class ResetCaptchaIn(BaseModel):
    purpose: Literal["pin", "password"]


@router.post("/ebro-reset/captcha")
async def reset_captcha_route(req: ResetCaptchaIn, session: AsyncSession = Depends(get_db), user: AppUser = Depends(auth.require_auth)):
    """A puzzle from the Ebro cloud: the background, the piece and the width `x` is measured on."""
    account = await _reset_account(session, user)
    _check_throttle((ebro_sms_throttle, f"sms:{account.id}"))
    ctx = account_ctx(account_session_path(account.id))
    try:
        puzzle = await asyncio.get_running_loop().run_in_executor(None, reset_captcha, ctx)
    except RuntimeError as e:
        raise _no_session(e) from e
    except Exception as e:
        raise _network(e, "captcha") from e
    if not puzzle:
        raise HTTPException(status_code=424, detail=_("Ebro no ha dado el captcha. Inténtalo de nuevo en unos minutos."))
    _pending_captcha[user.id] = {"token": puzzle["token"], "secret_key": puzzle["secret_key"], "purpose": req.purpose, "at": time.monotonic()}
    return {"background": puzzle["background"], "piece": puzzle["piece"], "width": ebro_reset.CAPTCHA_WIDTH}


class ResetSmsIn(BaseModel):
    x: float = Field(ge=0, le=ebro_reset.CAPTCHA_WIDTH)


@router.post("/ebro-reset/sms")
async def reset_sms_route(req: ResetSmsIn, session: AsyncSession = Depends(get_db), user: AppUser = Depends(auth.require_auth)):
    """Where the piece was left: if it fits, Ebro sends the code by SMS to the account's phone. Each puzzle is good for one try."""
    account = await _reset_account(session, user)
    pending = _pending_captcha.pop(user.id, None)
    if not pending or time.monotonic() - pending["at"] > CAPTCHA_MAX_AGE_S:
        raise HTTPException(status_code=409, detail=_("El captcha ha caducado: pide otro."))
    key = f"sms:{account.id}"
    _check_throttle((ebro_sms_throttle, key))
    ctx = account_ctx(account_session_path(account.id))
    try:
        outcome = await asyncio.get_running_loop().run_in_executor(
            None, reset_send_sms, ctx, account.phone, account.area_code or "34", RESET_PURPOSES[pending["purpose"]],
            pending["token"], pending["secret_key"], req.x)
    except RuntimeError as e:
        raise _no_session(e) from e
    except Exception as e:
        raise _network(e, "sms") from e
    if outcome["captcha_failed"]:
        raise HTTPException(status_code=422, detail=_("La pieza no ha encajado. Prueba con otro captcha."))
    ebro_sms_throttle.record_failure(key)                    # every SMS sent counts: three in ten minutes at most
    if not outcome["sent"]:
        raise HTTPException(status_code=424, detail=_("Ebro no ha enviado el SMS: {p0}", p0=outcome["reason"] or "sin motivo"))
    return {"status": "ok", "phone": mask_phone(account.phone)}


class ResetPinIn(BaseModel):
    vin: str = Field(max_length=32)
    code: str = Field(min_length=4, max_length=8)
    new_pin: str = Field(max_length=8)


@router.post("/ebro-reset/pin")
async def reset_pin_route(req: ResetPinIn, request: Request, session: AsyncSession = Depends(get_db), user: AppUser = Depends(auth.require_auth)):
    """The new PIN with the SMS code. Not yet tried against the real cloud (docs/ebro-protocol.md)."""
    account = await _reset_account(session, user)
    car = (await session.execute(select(Vehicle).where(Vehicle.vin == req.vin, Vehicle.owner_id == user.id,
                                                       Vehicle.is_manual == False))).scalar_one_or_none()  # noqa: E712
    if car is None:
        raise HTTPException(status_code=404, detail=_("Ese coche no es de tu cuenta de Ebro."))
    new, code = req.new_pin.strip(), req.code.strip()
    if not ebro_account.pin_format_ok(new):
        raise HTTPException(status_code=422, detail=_("El PIN nuevo tiene que tener 6 dígitos."))
    key = f"code:{account.id}"
    _check_throttle((ebro_code_throttle, key))
    poller = VehiclePoller.get_instance()
    ctx = poller._get_or_create_ctx(car.vin)
    try:
        outcome = await asyncio.get_running_loop().run_in_executor(None, reset_pin_with_code, ctx, account.phone,
                                                                   account.area_code or "34", code, new)
    except RuntimeError as e:
        raise _no_session(e) from e
    except Exception as e:
        raise _network(e, "pin") from e
    ip = auth.client_ip(request)
    if not outcome["changed"]:
        ebro_code_throttle.record_failure(key)
        await poller._audit_command(car.vin, "restablecer_pin_ebro", None, False, None, outcome["reason"], ip)
        if outcome["unconfirmed"]:
            raise HTTPException(status_code=424, detail=_("Ebro respondió sin error, pero todavía no acepta el PIN nuevo. Prueba en unos minutos una orden con el PIN nuevo; EVBro no ha cambiado el PIN guardado."))
        raise HTTPException(status_code=424, detail=_("Ebro no ha restablecido el PIN: {p0}", p0=outcome["reason"] or "sin motivo"))
    ebro_code_throttle.reset(key)
    await poller._audit_command(car.vin, "restablecer_pin_ebro", None, True, None, "PIN de Ebro restablecido", ip)
    stored = await _adopt_new_pin(session, account, user, poller, new)
    _LOGGER.info("Ebro PIN reset for account %s through %s (stored copy updated: %s)", account.id, car.vin[-6:], stored)
    return {"status": "ok", "stored_pin_updated": stored}


class ResetPasswordIn(BaseModel):
    code: str = Field(min_length=4, max_length=8)
    new_password: str = Field(max_length=128)


@router.post("/ebro-reset/password")
async def reset_password_route(req: ResetPasswordIn, session: AsyncSession = Depends(get_db), user: AppUser = Depends(auth.require_auth)):
    """The new password with the SMS code; EVBro keeps it and signs in with it. Not yet tried against the real cloud."""
    account = await _reset_account(session, user)
    if not ebro_account.password_format_ok(req.new_password):
        raise HTTPException(status_code=422, detail=_("La contraseña nueva debe tener de 8 a 16 caracteres, con letras, números y algún carácter especial."))
    key = f"code:{account.id}"
    _check_throttle((ebro_code_throttle, key))
    path = account_session_path(account.id)
    try:
        outcome = await asyncio.get_running_loop().run_in_executor(None, reset_password_with_code, account_ctx(path), path,
                                                                   account.phone, account.area_code or "34", req.code.strip(),
                                                                   req.new_password)
    except RuntimeError as e:
        raise _no_session(e) from e
    except Exception as e:
        raise _network(e, "password") from e
    if not outcome["changed"]:
        ebro_code_throttle.record_failure(key)
        raise HTTPException(status_code=424, detail=_("Ebro no ha restablecido la contraseña: {p0}", p0=outcome["reason"] or "sin motivo"))
    ebro_code_throttle.reset(key)
    account.password = req.new_password
    if outcome["token_data"]:
        account.token_data = outcome["token_data"]
        account.last_login = utcnow()
    await session.commit()
    _LOGGER.info("Ebro password reset for account %s (new session: %s)", account.id, bool(outcome["token_data"]))
    if outcome["token_data"]:
        poller = VehiclePoller.get_instance()
        await poller.bind_sessions()
        asyncio.create_task(CarEventHub.get_instance().restart_owner(account.user_id, poller))
    return {"status": "ok", "session_renewed": bool(outcome["token_data"])}


class LanguageRequest(BaseModel):
    language: str = Field(pattern="^(es|en|pt)$")


@router.put("/language")
async def update_language(req: LanguageRequest, request: Request, response: Response, session: AsyncSession = Depends(get_db),
                          user: AppUser = Depends(auth.require_auth)):
    """The language of the app and of the notices for this account (Ajustes → Sistema). The cookie mirrors it so the page is
    served in that language from the next load (app.i18n)."""
    db_user = await session.get(AppUser, user.id)
    db_user.language = req.language
    await session.commit()
    auth.set_language_cookie(response, request, req.language)
    return {"status": "ok", "language": req.language}




class HomeLayoutRequest(BaseModel):
    widgets: List[str] = Field(max_length=40)
    page: Literal["overview", "charges", "climate", "dock"] = "overview"      # «dock»: the phone's bottom bar (features/dock.js)


def _with_page(layout: Optional[dict], page: str, widgets: Optional[List[str]]) -> Optional[dict]:
    """A new layout dict with one page set (or back to its default when `widgets` is None); None when every page is the default."""
    out = {"widgets": list(layout["widgets"])} if layout and isinstance(layout.get("widgets"), list) else {}
    pages = dict(layout.get("pages") or {}) if layout else {}
    if page == "overview":
        out.pop("widgets", None)
        if widgets is not None:
            out["widgets"] = widgets
    else:
        pages.pop(page, None)
        if widgets is not None:
            pages[page] = widgets
    if pages:
        out["pages"] = pages
    return out or None


@router.get("/home-layout")
async def get_home_layout(user: AppUser = Depends(auth.require_auth)):
    """The blocks this account shows, in order: `widgets` for Inicio and `pages.charges` / `pages.climate` for Energía and
    Confort; a missing one (or `null` for all) is that page's default layout (features/home-widgets.js)."""
    return {"layout": user.home_layout}


@router.put("/home-layout")
async def put_home_layout(req: HomeLayoutRequest, session: AsyncSession = Depends(get_db), user: AppUser = Depends(auth.require_auth)):
    """A personal choice of every account, also of read-only ones: it changes only how its own pages look."""
    widgets: List[str] = []
    for key in req.widgets:
        if not HOME_WIDGET_KEY.fullmatch(key or ""):
            raise HTTPException(status_code=422, detail="Widget no válido.")
        if key not in widgets:
            widgets.append(key)
    db_user = await session.get(AppUser, user.id)
    db_user.home_layout = _with_page(db_user.home_layout, req.page, widgets)       # a new dict: the JSON column sees the change
    await session.commit()
    return {"layout": db_user.home_layout}


@router.delete("/home-layout")
async def reset_home_layout(page: Literal["overview", "charges", "climate", "dock"] = "overview", session: AsyncSession = Depends(get_db),
                            user: AppUser = Depends(auth.require_auth)):
    """One page back to its default layout; the others keep theirs."""
    db_user = await session.get(AppUser, user.id)
    db_user.home_layout = _with_page(db_user.home_layout, page, None)
    await session.commit()
    return {"layout": db_user.home_layout}


class PinPolicyRequest(BaseModel):
    policy: str = Field(pattern="^(open|always|never)$")


@router.put("/pin-policy")
async def update_pin_policy(req: PinPolicyRequest, session: AsyncSession = Depends(get_db), user: AppUser = Depends(auth.require_auth)):
    """When orders ask for the PIN: only the ones that open the car (default), every order, or none while a PIN is saved."""
    if req.policy == "never":
        stmt = select(EbroAccount).where(EbroAccount.is_active == True, EbroAccount.user_id == user.id).order_by(EbroAccount.id.desc())  # noqa: E712
        account = (await session.execute(stmt)).scalars().first()
        if not (account and (account.pin or "").strip()):
            raise HTTPException(status_code=409, detail="Para no pedir nunca el PIN hay que tener el PIN guardado en la cuenta.")
    db_user = await session.get(AppUser, user.id)
    db_user.pin_policy = req.policy
    await session.commit()
    return {"status": "ok", "pin_policy": req.policy}


# ───────────────────────── the manager's own MQTT broker (Home Assistant) ─────────────────────────
class MqttLinkIn(BaseModel):
    host: str = Field(min_length=1, max_length=255)
    port: Optional[int] = Field(default=None, ge=1, le=65535)
    username: Optional[str] = Field(default=None, max_length=120)
    password: Optional[str] = Field(default=None, max_length=200)     # empty on an existing link: the stored one is kept
    use_tls: bool = False
    topic_prefix: str = Field(default="evbro", min_length=1, max_length=60, pattern=r"^[A-Za-z0-9_-]+$")
    ha_discovery: bool = True
    ha_prefix: str = Field(default="homeassistant", min_length=1, max_length=60, pattern=r"^[A-Za-z0-9_-]+$")
    enabled: bool = True


def _mqtt_out(link: Optional[MqttLink], user_id: int) -> Dict[str, Any]:
    if link is None:
        return {"link": None}
    return {"link": {"host": link.host, "port": link.port, "username": link.username, "has_password": bool(link.password), "use_tls": link.use_tls,
                     "topic_prefix": link.topic_prefix, "ha_discovery": link.ha_discovery, "ha_prefix": link.ha_prefix, "enabled": link.enabled,
                     **(MqttHub.get_instance().status(user_id) or {"connected": False, "last_error": None, "connected_at": None, "cars": []})}}


async def _mqtt_link(session: AsyncSession, user_id: int) -> Optional[MqttLink]:
    return (await session.execute(select(MqttLink).where(MqttLink.user_id == user_id))).scalar_one_or_none()


@router.get("/mqtt")
async def get_mqtt_link(session: AsyncSession = Depends(get_db), user: AppUser = Depends(auth.require_auth)):
    """The manager's broker and whether EVBro is connected to it (never the password)."""
    return _mqtt_out(await _mqtt_link(session, user.id), user.id)


@router.put("/mqtt")
async def save_mqtt_link(req: MqttLinkIn, session: AsyncSession = Depends(get_db), user: AppUser = Depends(auth.require_auth)):
    """Saves the broker (password encrypted) and reconnects: from then on this manager's cars are published there."""
    link = await _mqtt_link(session, user.id)
    if link is None:
        link = MqttLink(user_id=user.id, host=req.host.strip())
        session.add(link)
    link.host, link.port, link.username = req.host.strip(), req.port, (req.username or "").strip() or None
    if req.password is not None and req.password != "":
        link.password = req.password
    link.use_tls, link.topic_prefix, link.ha_discovery, link.ha_prefix, link.enabled = req.use_tls, req.topic_prefix, req.ha_discovery, req.ha_prefix, req.enabled
    await session.commit()
    await session.refresh(link)
    await MqttHub.get_instance().reload_user(user.id)
    await asyncio.sleep(0.3)                                   # give the first connection attempt a moment so the answer can say «conectado»
    return _mqtt_out(link, user.id)


@router.delete("/mqtt")
async def delete_mqtt_link(session: AsyncSession = Depends(get_db), user: AppUser = Depends(auth.require_auth)):
    link = await _mqtt_link(session, user.id)
    if link is not None:
        await session.delete(link)
        await session.commit()
    await MqttHub.get_instance().reload_user(user.id)
    return {"link": None}


@router.post("/mqtt/test")
async def test_mqtt_link(req: MqttLinkIn, session: AsyncSession = Depends(get_db), user: AppUser = Depends(auth.require_auth)):
    """Connects once to the broker given (or the stored password when none is typed) and reports the outcome."""
    password = req.password or None
    if not password:
        link = await _mqtt_link(session, user.id)
        password = link.password if link else None
    config = MqttLinkConfig(host=req.host.strip(), port=req.port or (8883 if req.use_tls else 1883), username=(req.username or "").strip() or None,
                            password=password, use_tls=req.use_tls, topic_prefix=req.topic_prefix, client_id=f"evbro_{user.id}")
    return await mqtt_hub.test_connection(config)


class AccountDelete(BaseModel):
    confirm: str = Field(max_length=80)        # the username, typed by hand
    password: str = Field(max_length=256)
    # Optional: why it leaves (services/exit_survey.py). Kept with the username, for the administrator. Any value is taken
    # and the unknown ones are dropped there: a mangled survey must never stop somebody from deleting their account.
    reasons: List[Any] = Field(default_factory=list, max_length=20)
    comment: Optional[str] = Field(default=None, max_length=exit_survey.COMMENT_MAX)


@router.post("/delete")
async def delete_own_account(req: AccountDelete, request: Request, response: Response, session: AsyncSession = Depends(get_db),
                             user: AppUser = Depends(auth.require_auth)):
    """The manager deletes its own account and everything in its space: cars with their history, member accounts, geofences, the
    Ebro account and its session, notification channels and the stored files. It cannot be undone, so it asks twice: the
    username typed by hand (against a slip) and the password (against somebody else at an open session)."""
    user_id, username, created_at = user.id, user.username, user.created_at
    ip = auth.client_ip(request)
    _check_throttle((ip_throttle, f"ip:{ip}"))
    if req.confirm.strip().lower() != username:
        raise HTTPException(status_code=409, detail="Escribe tu nombre de usuario exacto para confirmar.")
    if not await asyncio.to_thread(passwords.verify_password, req.password, user.password_hash):
        ip_throttle.record_failure(f"ip:{ip}")
        raise HTTPException(status_code=403, detail="La contraseña no es correcta.")
    vins = await tenant.vins_of(session, user_id)
    removed = await tenant.delete_manager_space(session, user_id)
    await exit_survey.record(session, username, req.reasons, req.comment, created_at, removed)      # why it leaves, if it said so
    await session.commit()
    await tenant.release_manager_runtime(user_id, vins)
    auth.clear_session_cookie(response)
    _LOGGER.info("Manager #%s deleted its own account from %s.", user_id, ip)
    return {"status": "ok", "removed": removed}


@router.post("/logout")
async def logout_ebro_account(session: AsyncSession = Depends(get_db), user: AppUser = Depends(auth.require_auth)):
    """Unlinks this user's active Ebro account."""
    stmt = select(EbroAccount).where(EbroAccount.is_active == True, EbroAccount.user_id == user.id)  # noqa: E712
    accounts = (await session.execute(stmt)).scalars().all()

    for a in accounts:
        a.is_active = False

    await session.commit()

    await CarEventHub.get_instance().stop_owner(user.id)

    # Without a connected account no PIN may remain in memory (only the cars of this user)
    own = await _own_vins(session, user.id)
    for vin, ctx in VehiclePoller.get_instance()._contexts.items():
        if vin in own:
            ctx.pin = ""
            ctx.reset_pin_lockout()
    return {"status": "ok", "message": "Cuenta Ebro desconectada."}

