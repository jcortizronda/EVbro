"""Routes to configure and test mobile notifications (Android, iOS and Web)."""
from __future__ import annotations

import datetime
from typing import Dict, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy import delete, desc, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import AppUser, NotificationLog, PushSubscription
from app.db.session import get_db
from app.i18n import _
from app.security import auth
from app.services.notification_service import NotificationService, push_endpoint_problem, webhook_url_problem
from app.timeutil import utcnow

router = APIRouter(prefix="/notifications", tags=["Notifications"])

MASK = "••••••••"


def mask_secret(val: Optional[str]) -> str:
    """Hides a secret to show it in the interface. Never returns the full value."""
    if not val:
        return ""
    if len(val) < 12:
        return MASK
    return val[:3] + MASK + val[-3:]


def is_masked(val: Optional[str]) -> bool:
    return bool(val) and "••••" in val


def real_value(sent: Optional[str], stored: Optional[str]) -> Optional[str]:
    """Value to use in a test: the one sent, unless it is empty or masked (then the stored one)."""
    sent = (sent or "").strip()
    return stored if (not sent or is_masked(sent)) else sent


class NotificationConfigUpdate(BaseModel):
    telegram_enabled: Optional[bool] = None
    telegram_bot_token: Optional[str] = None
    telegram_chat_id: Optional[str] = None

    pushover_enabled: Optional[bool] = None
    pushover_user_key: Optional[str] = None
    pushover_api_token: Optional[str] = None

    webhook_enabled: Optional[bool] = None
    webhook_url: Optional[str] = None

    notify_geofence_enter: Optional[bool] = None
    notify_geofence_exit: Optional[bool] = None
    notify_fuel_low: Optional[bool] = None
    notify_vehicle_faults: Optional[bool] = None
    notify_windows_open: Optional[bool] = None
    notify_maintenance_due: Optional[bool] = None
    notify_charge_started: Optional[bool] = None
    notify_charge_completed: Optional[bool] = None
    notify_charge_interrupted: Optional[bool] = None
    notify_tyre_pressure: Optional[bool] = None
    notify_renewals: Optional[bool] = None
    notify_security_alerts: Optional[bool] = None
    notify_low_battery: Optional[bool] = None
    notify_admin_messages: Optional[bool] = None       # off = silenced: kept in the notice centre, not sent


class TestNotificationRequest(BaseModel):
    channel: str  # "webpush", "telegram", "pushover", "webhook", "all"
    bot_token: Optional[str] = None
    chat_id: Optional[str] = None
    pushover_user_key: Optional[str] = None
    pushover_api_token: Optional[str] = None
    webhook_url: Optional[str] = None


class PushSubscribeRequest(BaseModel):
    endpoint: str
    keys: Dict[str, str]
    label: Optional[str] = None


class PushEndpointRequest(BaseModel):
    endpoint: str


@router.get("/push/public-key")
async def push_public_key():
    """Public VAPID key for `pushManager.subscribe` (generated automatically the first time)."""
    from app.security import vapid
    return {"public_key": vapid.public_key_b64()}


@router.post("/push/subscribe")
async def push_subscribe(req: PushSubscribeRequest, session: AsyncSession = Depends(get_db), user: AppUser = Depends(auth.require_auth)):
    """Registers (or updates) this device as a Web Push target."""
    if push_endpoint_problem(req.endpoint) or not req.keys.get("p256dh") or not req.keys.get("auth"):
        raise HTTPException(status_code=422, detail="Suscripción de Web Push no válida.")
    h = NotificationService.endpoint_hash(req.endpoint)
    sub = (await session.execute(select(PushSubscription).where(PushSubscription.endpoint_hash == h))).scalar_one_or_none()
    data = {"endpoint": req.endpoint, "keys": {"p256dh": req.keys["p256dh"], "auth": req.keys["auth"]}}
    if sub is None:
        sub = PushSubscription(endpoint_hash=h, user_id=user.id, subscription=data, label=(req.label or "")[:120] or None)
        session.add(sub)
    else:
        sub.user_id = user.id                      # the browser now belongs to whoever signed in on it last
        sub.subscription = data
        if req.label:
            sub.label = req.label[:120]
    await session.commit()
    return {"status": "ok", "id": sub.id}


@router.post("/push/status")
async def push_status(req: PushEndpointRequest, session: AsyncSession = Depends(get_db), user: AppUser = Depends(auth.require_auth)):
    """Is this device (identified by its endpoint) subscribed?"""
    h = NotificationService.endpoint_hash(req.endpoint)
    sub = (await session.execute(select(PushSubscription).where(PushSubscription.endpoint_hash == h, PushSubscription.user_id == user.id))).scalar_one_or_none()
    return {"subscribed": sub is not None}


@router.post("/push/unsubscribe")
async def push_unsubscribe(req: PushEndpointRequest, session: AsyncSession = Depends(get_db), user: AppUser = Depends(auth.require_auth)):
    h = NotificationService.endpoint_hash(req.endpoint)
    await session.execute(delete(PushSubscription).where(PushSubscription.endpoint_hash == h, PushSubscription.user_id == user.id))
    await session.commit()
    return {"status": "ok"}


@router.get("/push/devices")
async def push_devices(session: AsyncSession = Depends(get_db), user: AppUser = Depends(auth.require_auth)):
    """Subscribed devices of this account (without their endpoint or keys)."""
    subs = (await session.execute(select(PushSubscription).where(PushSubscription.user_id == user.id).order_by(PushSubscription.id))).scalars().all()
    return [
        {
            "id": s.id,
            "label": s.label or f"Dispositivo {s.id}",
            "created_at": s.created_at.isoformat() if s.created_at else None,
            "last_success": s.last_success.isoformat() if s.last_success else None,
            "last_error": s.last_error,
        }
        for s in subs
    ]


@router.delete("/push/devices/{device_id}")
async def push_device_delete(device_id: int, session: AsyncSession = Depends(get_db), user: AppUser = Depends(auth.require_auth)):
    await session.execute(delete(PushSubscription).where(PushSubscription.id == device_id, PushSubscription.user_id == user.id))
    await session.commit()
    return {"status": "ok"}


@router.get("/config")
async def get_notification_config(session: AsyncSession = Depends(get_db), user: AppUser = Depends(auth.require_auth)):
    """Notification settings of THIS account (what it receives and through which channels)."""
    config = await NotificationService.get_or_create_config(session, user.id)

    # secrets do NOT leave the API: the browser only gets a masked version. If it is sent
    # back unchanged on save, it is ignored (see update_notification_config).
    mask = mask_secret

    return {
        "telegram_enabled": config.telegram_enabled,
        "telegram_bot_token": mask(config.telegram_bot_token),
        "telegram_bot_token_set": bool(config.telegram_bot_token),
        "telegram_chat_id": config.telegram_chat_id or "",

        "pushover_enabled": config.pushover_enabled,
        "pushover_user_key": mask(config.pushover_user_key),
        "pushover_api_token": mask(config.pushover_api_token),
        "pushover_set": bool(config.pushover_user_key and config.pushover_api_token),

        "webhook_enabled": config.webhook_enabled,
        "webhook_url": mask(config.webhook_url),
        "webhook_url_set": bool(config.webhook_url),

        "notify_geofence_enter": config.notify_geofence_enter,
        "notify_geofence_exit": config.notify_geofence_exit,
        "notify_fuel_low": getattr(config, "notify_fuel_low", True),
        "notify_vehicle_faults": getattr(config, "notify_vehicle_faults", True),
        "notify_windows_open": getattr(config, "notify_windows_open", True),
        "notify_maintenance_due": config.notify_maintenance_due,
        "notify_charge_started": config.notify_charge_started,
        "notify_charge_completed": config.notify_charge_completed,
        "notify_charge_interrupted": getattr(config, "notify_charge_interrupted", True),
        "notify_tyre_pressure": getattr(config, "notify_tyre_pressure", True),
        "notify_renewals": getattr(config, "notify_renewals", True),
        "notify_security_alerts": config.notify_security_alerts,
        "notify_low_battery": config.notify_low_battery,
        "notify_admin_messages": config.notify_admin_messages is not False,
        "updated_at": config.updated_at.isoformat() if config.updated_at else None,
    }


@router.put("/config")
async def update_notification_config(
    req: NotificationConfigUpdate, session: AsyncSession = Depends(get_db), user: AppUser = Depends(auth.require_auth)
):
    """Updates the channels and events of THIS account."""
    config = await NotificationService.get_or_create_config(session, user.id)
    data = req.model_dump(exclude_unset=True) if hasattr(req, "model_dump") else req.dict(exclude_unset=True)
    if data.get("webhook_url") and not is_masked(str(data["webhook_url"])):
        problem = webhook_url_problem(data["webhook_url"])
        if problem:
            raise HTTPException(status_code=422, detail=problem)

    for k, v in data.items():
        if v is not None:
            # a masked secret sent back must not overwrite the real one
            if is_masked(str(v)):
                continue
            setattr(config, k, v)

    config.updated_at = utcnow()
    await session.commit()
    await session.refresh(config)
    return {"status": "ok", "message": "Configuración de notificaciones guardada con éxito."}


@router.post("/test")
async def test_notification(
    req: TestNotificationRequest, session: AsyncSession = Depends(get_db), user: AppUser = Depends(auth.require_auth)
):
    """Sends a test notification right away to the requested channel (to this account)."""
    config = await NotificationService.get_or_create_config(session, user.id)
    title = _("Prueba de Notificación EVbro")
    msg = _("¡Conexión establecida con éxito! Tu dispositivo (Android / iOS) recibirá aquí las alertas de geocercas, mantenimiento de fábrica y estado de tu vehículo Ebro.")

    channel = req.channel.lower()
    success = False
    error = None

    if channel == "telegram":
        token = real_value(req.bot_token, config.telegram_bot_token)
        chat_id = req.chat_id or config.telegram_chat_id
        success, error = await NotificationService._send_telegram(token, chat_id, title, msg)

    elif channel == "webpush":
        results = await NotificationService.send_webpush(session, title, msg, "test", user_id=user.id)
        if not results:
            raise HTTPException(status_code=400, detail="Ningún dispositivo suscrito: activa las notificaciones en este dispositivo.")
        for r in results:
            session.add(NotificationLog(user_id=user.id, category="test", title=title, message=msg, channel="webpush",
                                        success=r["success"], error=r["error"]))
        await session.commit()
        if not any(r["success"] for r in results):
            raise HTTPException(status_code=400, detail="Fallo al enviar: " + "; ".join(r["error"] or "?" for r in results))
        return {"status": "ok", "channel": "webpush", "message": f"Enviada a {sum(r['success'] for r in results)} dispositivo(s)."}

    elif channel == "pushover":
        user_key = real_value(req.pushover_user_key, config.pushover_user_key)
        token = real_value(req.pushover_api_token, config.pushover_api_token)
        success, error = await NotificationService._send_pushover(user_key, token, title, msg)

    elif channel == "webhook":
        url = real_value(req.webhook_url, config.webhook_url)
        payload = {
            "event": "test",
            "title": title,
            "message": msg,
            "timestamp": utcnow().isoformat(),
        }
        success, error = await NotificationService._send_webhook(url, payload)

    elif channel == "all":
        results = await NotificationService.dispatch(
            category="test", title=title, message=msg, user_id=user.id
        )
        return {"status": "ok", "results": results}

    else:
        raise HTTPException(status_code=400, detail=_("Canal desconocido: {p0}", p0=channel))

    # record it in the history
    session.add(NotificationLog(
        user_id=user.id,
        category="test",
        title=title,
        message=msg,
        channel=channel,
        success=success,
        error=error,
    ))
    await session.commit()

    if not success:
        raise HTTPException(status_code=400, detail=_("Fallo al enviar notificación a {p0}: {p1}", p0=channel, p1=error))

    return {"status": "ok", "channel": channel, "message": "Notificación enviada correctamente."}


# How many recent rows are looked at to count the unread notices (one notice is one row per channel it went through)
UNREAD_SCAN = 500


def notice_key(log: NotificationLog) -> tuple:
    """One notice sent through several channels leaves one row per channel, all written in the same instant: this joins them."""
    stamp = log.timestamp.replace(microsecond=0) if log.timestamp else None
    return (log.category, log.title, log.message, stamp)


@router.get("/logs")
async def list_notification_logs(
    limit: int = Query(50, ge=1, le=500), session: AsyncSession = Depends(get_db), user: AppUser = Depends(auth.require_auth)
):
    """Recent history of the notifications sent to THIS account. `unread`: newer than the last time it opened its notification centre."""
    stmt = select(NotificationLog).where(NotificationLog.user_id == user.id).order_by(desc(NotificationLog.timestamp)).limit(limit)
    logs = (await session.execute(stmt)).scalars().all()
    seen = user.notifications_seen_at

    return [
        {
            "id": log.id,
            "timestamp": log.timestamp.isoformat(),
            "category": log.category,
            "title": log.title,
            "message": log.message,
            "channel": log.channel,
            "success": log.success,
            "error": log.error,
            "unread": seen is None or (log.timestamp is not None and log.timestamp > seen),
        }
        for log in logs
    ]


@router.get("/unread")
async def count_unread_notifications(session: AsyncSession = Depends(get_db), user: AppUser = Depends(auth.require_auth)):
    """Notices this account has not seen yet (for the dot of the bell in the header)."""
    stmt = select(NotificationLog).where(NotificationLog.user_id == user.id)
    if user.notifications_seen_at is not None:
        stmt = stmt.where(NotificationLog.timestamp > user.notifications_seen_at)
    rows = (await session.execute(stmt.order_by(desc(NotificationLog.timestamp)).limit(UNREAD_SCAN))).scalars().all()
    return {"count": len({notice_key(r) for r in rows})}


@router.post("/seen")
async def mark_notifications_seen(session: AsyncSession = Depends(get_db), user: AppUser = Depends(auth.require_auth)):
    """The account opened its notification centre: everything up to now counts as seen."""
    user.notifications_seen_at = utcnow()
    await session.commit()
    return {"status": "ok"}


@router.delete("/logs/{log_id}")
async def dismiss_notification(log_id: int, session: AsyncSession = Depends(get_db), user: AppUser = Depends(auth.require_auth)):
    """Removes one notice from this account's history (swiped away in the notification centre): the row given and the rows
    the same notice left on its other channels."""
    log = await session.get(NotificationLog, log_id)
    if log is None or log.user_id != user.id:
        raise HTTPException(status_code=404, detail="Aviso no encontrado.")
    key = notice_key(log)
    start = key[3]
    stmt = select(NotificationLog).where(NotificationLog.user_id == user.id, NotificationLog.category == log.category,
                                         NotificationLog.title == log.title)
    if start is not None:
        stmt = stmt.where(NotificationLog.timestamp >= start, NotificationLog.timestamp < start + datetime.timedelta(seconds=1))
    same = [row.id for row in (await session.execute(stmt)).scalars().all() if notice_key(row) == key]
    await session.execute(delete(NotificationLog).where(NotificationLog.id.in_(same or [log.id])))
    await session.commit()
    return {"status": "ok", "removed": len(same) or 1}


@router.delete("/logs")
async def clear_notification_logs(session: AsyncSession = Depends(get_db), user: AppUser = Depends(auth.require_auth)):
    """Clears this account's notification history."""
    await session.execute(delete(NotificationLog).where(NotificationLog.user_id == user.id))
    await session.commit()
    return {"status": "ok", "message": "Historial de notificaciones borrado."}
