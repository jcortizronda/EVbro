"""Rutas de gestión y autenticación de cuentas Ebro / Chery desde la interfaz web."""
from __future__ import annotations

import asyncio
import datetime
import logging
from typing import Any, Dict, Optional

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.core import ebro_login
from app.db.models import AppUser, EbroAccount, MqttLink, Vehicle
from app.db.session import get_db
from app.i18n import _
from app.security import auth
from app.security.access import foreign_vins, is_root, space_owner_id, within_vehicle_limit
from app.services import command_policy, mqtt_hub, storage
from app.services.accounts import fetch_account_vehicles
from app.services.car_events import CarEventHub
from app.services.command_policy import pin_throttle, pin_throttle_key
from app.services.mqtt_hub import MqttHub, MqttLinkConfig
from app.services.poller import VehiclePoller

_LOGGER = logging.getLogger("app.api.account")
router = APIRouter(prefix="/account", tags=["Account"])


class EbroLoginRequest(BaseModel):
    phone: str = Field(max_length=32)
    password: str = Field(max_length=128)
    pin: Optional[str] = Field(default=None, max_length=32)
    area_code: str = Field(default="34", max_length=5)


def mask_phone(phone: str) -> str:
    """Enmascara el teléfono para privacidad visual (ej: +34 6** *** 890)."""
    clean = phone.strip()
    if len(clean) >= 9:
        return f"{clean[:3]} {'*' * (len(clean) - 6)} {clean[-3:]}"
    return clean


@router.get("/status")
async def get_account_status(session: AsyncSession = Depends(get_db), user: AppUser = Depends(auth.require_auth)):
    """Devuelve el estado de conexión de la cuenta oficial Ebro de este usuario."""
    stmt = select(EbroAccount).where(EbroAccount.is_active == True, EbroAccount.user_id == user.id).order_by(EbroAccount.id.desc())  # noqa: E712
    account = (await session.execute(stmt)).scalars().first()

    # Only the cars this account owns that come from the Ebro account (cars added by hand are not part of it)
    v_stmt = select(Vehicle).where(Vehicle.owner_id == user.id, Vehicle.is_manual == False)  # noqa: E712
    vehicles = (await session.execute(v_stmt)).scalars().all()

    if account:
        return {
            "connected": True,
            "source": "web_login",
            "phone": mask_phone(account.phone),
            "area_code": account.area_code,
            "has_pin": bool(account.pin) or bool(settings.EBRO_PIN),
            "pin_policy": user.pin_policy or command_policy.DEFAULT_POLICY,
            "last_login": account.last_login.isoformat() if account.last_login else None,
            "vehicles_count": len(vehicles),
            "vehicles": [{"vin": v.vin, "name": v.name, "model": v.model} for v in vehicles],
        }

    # Si no hay en DB, comprobar si se configuró por variable de entorno (la cuenta del servidor es del administrador)
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


async def _own_vins(session: AsyncSession, user_id: int) -> set:
    return {v for (v,) in (await session.execute(select(Vehicle.vin).where(Vehicle.owner_id == user_id))).all()}


@router.post("/login")
async def login_ebro_account(req: EbroLoginRequest, session: AsyncSession = Depends(get_db), user: AppUser = Depends(auth.require_auth)):
    """Inicia sesión con la cuenta oficial Ebro, guarda credenciales en BD y descubre vehículos."""
    phone_clean = req.phone.strip().replace(" ", "").replace("-", "")
    area = req.area_code.strip().replace("+", "")

    # Si el usuario introdujo el teléfono con el prefijo +34 delante, normalizarlo
    if phone_clean.startswith(f"+{area}"):
        phone_clean = phone_clean[len(area) + 1 :]
    elif phone_clean.startswith(area) and len(phone_clean) > 9:
        phone_clean = phone_clean[len(area) :]

    _LOGGER.info(f"Intentando login en la nube Ebro para el teléfono: +{area} {mask_phone(phone_clean)}")

    def _do_auth():
        return ebro_login.login_with_password(phone_clean, req.password, area)

    loop = asyncio.get_running_loop()
    try:
        token_data = await loop.run_in_executor(None, _do_auth)
    except Exception as e:
        _LOGGER.error(f"Error de red o criptografía en login Ebro: {e}")
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

    # 1. Guardar o actualizar la cuenta en la Base de Datos
    acc_stmt = select(EbroAccount).where(EbroAccount.phone == phone_clean)
    existing_acc = (await session.execute(acc_stmt)).scalar_one_or_none()
    if existing_acc is not None and existing_acc.user_id not in (None, user.id):
        raise HTTPException(status_code=409, detail="Esa cuenta de Ebro ya está conectada por otro usuario de este servidor.")

    now = datetime.datetime.utcnow()
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

    # 2. Descubrir vehículos automáticamente usando el token obtenido
    try:
        cars_list = await loop.run_in_executor(None, fetch_account_vehicles, token_data, await foreign_vins(session, user.id))
    except Exception as e:
        _LOGGER.error(f"Error al listar vehículos tras login: {e}")
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
            session.add(v_new)
            discovered.append({"vin": vin, "name": nickname, "model": model, "is_new": True})
        else:
            v_exist.owner_id = v_exist.owner_id or account_obj.user_id
            v_exist.name = nickname
            v_exist.model = model
            v_exist.car_token = car_tok
            discovered.append({"vin": vin, "name": nickname, "model": model, "is_new": False})

        v_ctx = poller._get_or_create_ctx(vin)
        # El PIN de la cuenta recién conectada sustituye al anterior (si no trae, queda vacío) y se
        # reinicia el anti-bloqueo: son credenciales nuevas, no reintentos de las viejas.
        v_ctx.pin = (req.pin or "").strip()
        v_ctx.reset_pin_lockout()

    # Si se descubrieron vehículos reales, limpiar vehículo dummy demo si existía
    if discovered:
        dummy_stmt = select(Vehicle).where(Vehicle.vin == "LSJA8888888888888")
        dummy_v = (await session.execute(dummy_stmt)).scalar_one_or_none()
        if dummy_v and dummy_v.owner_id in (None, account_obj.user_id):
            await session.delete(dummy_v)

    await session.commit()

    # Cuenta nueva: el canal MQTT del coche se reconecta con ella (en segundo plano: tarda)
    asyncio.create_task(CarEventHub.get_instance().restart_owner(account_obj.user_id, poller))

    # Si hay vehículos, disparar sondeo inicial
    if discovered:
        first_vin = discovered[0]["vin"]
        asyncio.create_task(poller.poll_vehicle(first_vin, force=True))

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


class UpdatePinRequest(BaseModel):
    pin: str = Field(max_length=32)


@router.post("/pin")
async def update_account_pin(req: UpdatePinRequest, session: AsyncSession = Depends(get_db), user: AppUser = Depends(auth.require_auth)):
    """Guarda o actualiza el PIN de control remoto de la cuenta activa."""
    stmt = select(EbroAccount).where(EbroAccount.is_active == True, EbroAccount.user_id == user.id).order_by(EbroAccount.id.desc())  # noqa: E712
    account = (await session.execute(stmt)).scalars().first()
    if not account:
        raise HTTPException(status_code=404, detail="No hay ninguna cuenta Ebro conectada.")

    clean_pin = req.pin.strip() if req.pin else None
    account.pin = clean_pin
    await session.commit()

    # Cambiar el PIN es un gesto explícito de remedio: se pone a cero el anti-bloqueo y el taskId en
    # caché (ligado al PIN anterior), igual que hacía la integración al reconfigurar el PIN.
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


@router.post("/logout")
async def logout_ebro_account(session: AsyncSession = Depends(get_db), user: AppUser = Depends(auth.require_auth)):
    """Desvincula la cuenta de Ebro activa de este usuario."""
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

