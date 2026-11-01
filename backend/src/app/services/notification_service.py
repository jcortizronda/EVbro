"""Central mobile push notification service for Android and iOS (Web Push, Telegram, Pushover, webhook)."""
from __future__ import annotations

import asyncio
import datetime
import hashlib
import html
import ipaddress
import json
import logging
import socket
from typing import Any, Dict, List, Optional, Tuple, Union
from urllib.parse import urlparse

import aiohttp
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.db.models import AppUser, DeviceAccess, NotificationConfig, NotificationLog, PushSubscription, Vehicle
from app.db.session import async_session_factory
from app.i18n import M, Message, render
from app.security.auth import account_usable

_LOGGER = logging.getLogger(__name__)

# What each category needs switched on in the ACCOUNT's own configuration (every account chooses what it receives).
CATEGORY_FLAG: Dict[str, str] = {
    "geofence_enter": "notify_geofence_enter", "geofence_exit": "notify_geofence_exit", "maintenance": "notify_maintenance_due",
    "charge_started": "notify_charge_started", "charge_completed": "notify_charge_completed", "charge_interrupted": "notify_charge_interrupted",
    "security": "notify_security_alerts", "low_battery": "notify_low_battery", "tyre_pressure": "notify_tyre_pressure",
    "renewals": "notify_renewals", "fuel_low": "notify_fuel_low", "vehicle_fault": "notify_vehicle_faults", "windows_open": "notify_windows_open",
}


def webhook_url_problem(url: Optional[str]) -> Optional[str]:
    """Why a webhook URL must not be used, or None. The server makes the call, so by default it must not be made to reach its own network
    (loopback, private ranges, link-local/metadata); `ALLOW_PRIVATE_WEBHOOKS=true` lets a trusted instance call its LAN (Home Assistant...)."""
    url = (url or "").strip()
    if not url:
        return None
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https") or not parsed.hostname:
        return "La URL del webhook debe empezar por http:// o https://."
    if settings.ALLOW_PRIVATE_WEBHOOKS:
        return None
    try:
        infos = socket.getaddrinfo(parsed.hostname, parsed.port or (443 if parsed.scheme == "https" else 80), proto=socket.IPPROTO_TCP)
    except OSError:
        return "No se pudo resolver el nombre del webhook."
    for info in infos:
        ip = ipaddress.ip_address(info[4][0])
        if ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved or ip.is_multicast or ip.is_unspecified:
            return "El webhook no puede apuntar a una dirección interna."
    return None


class NotificationService:
    """Sends alerts to mobile devices and external channels."""

    @classmethod
    async def get_or_create_config(cls, session: AsyncSession, user_id: int) -> NotificationConfig:
        """The notification configuration of ONE account (what it wants to receive and through which channels), created with defaults."""
        config = (await session.execute(select(NotificationConfig).where(NotificationConfig.user_id == user_id))).scalar_one_or_none()
        if not config:
            config = NotificationConfig(user_id=user_id)
            session.add(config)
            await session.commit()
            await session.refresh(config)
        return config

    @classmethod
    async def recipients(cls, session: AsyncSession, vin: str) -> List[AppUser]:
        """Accounts that can see the car (its owner and the members linked to it) and may still sign in."""
        owner_id = (await session.execute(select(Vehicle.owner_id).where(Vehicle.vin == vin))).scalar_one_or_none()
        if owner_id is None:
            return []
        ids = {owner_id} | {uid for (uid,) in (await session.execute(select(DeviceAccess.user_id).where(DeviceAccess.vin == vin))).all()}
        users = (await session.execute(select(AppUser).where(AppUser.id.in_(ids)))).scalars().all()
        return [u for u in users if await account_usable(session, u)]

    # ------------------------------------------------------------------------
    # Sending through a single channel
    # ------------------------------------------------------------------------

    @classmethod
    async def _send_telegram(
        cls, bot_token: str, chat_id: str, title: str, message: str
    ) -> Tuple[bool, Optional[str]]:
        """Sends a notification through the Telegram Bot API."""
        if not bot_token or not chat_id:
            return False, "Faltan credenciales de Telegram (Bot Token o Chat ID)."

        # Rich HTML format (car and geofence names are user text: escaped, or Telegram rejects the message)
        html_text = f"<b>{html.escape(title)}</b>\n\n{html.escape(message)}"
        url = f"https://api.telegram.org/bot{bot_token.strip()}/sendMessage"
        payload = {
            "chat_id": chat_id.strip(),
            "text": html_text,
            "parse_mode": "HTML",
            "disable_web_page_preview": True,
        }

        try:
            async with aiohttp.ClientSession() as client:
                async with client.post(url, json=payload, timeout=8) as resp:
                    if resp.status == 200:
                        return True, None
                    text = await resp.text()
                    return False, f"HTTP {resp.status}: {text}"
        except Exception as e:
            return False, str(e)

    @staticmethod
    def endpoint_hash(endpoint: str) -> str:
        return hashlib.sha256(endpoint.encode()).hexdigest()

    @classmethod
    def _webpush_blocking(cls, subscription: Dict[str, Any], payload: Dict[str, Any]) -> Tuple[bool, Optional[str], bool]:
        """Blocking send (pywebpush uses `requests`). Returns (ok, error, expired)."""
        from pywebpush import WebPushException, webpush

        from app.security import vapid

        try:
            webpush(
                subscription_info=subscription,
                data=json.dumps(payload, ensure_ascii=False),
                vapid_private_key=vapid.get_vapid(),
                vapid_claims={"sub": settings.VAPID_SUBJECT},
                ttl=24 * 3600,
                timeout=10,
            )
            return True, None, False
        except WebPushException as e:
            status = getattr(getattr(e, "response", None), "status_code", None)
            # 404/410: the device unsubscribed or the browser revoked the subscription
            return False, f"HTTP {status}: {e}" if status else str(e), status in (404, 410)
        except Exception as e:  # noqa: BLE001 - network, keys, etc.: recorded, and it does not break the dispatch
            return False, str(e), False

    @classmethod
    async def send_webpush(
        cls, session: AsyncSession, title: str, message: str, category: str = "info", tag: Optional[str] = None, *, user_id: int
    ) -> List[Dict[str, Any]]:
        """Sends to the devices of ONE account; drops the ones that no longer exist. Does not commit."""
        subs = (await session.execute(select(PushSubscription).where(PushSubscription.user_id == user_id))).scalars().all()
        if not subs:
            return []
        payload = {"title": title, "body": message, "category": category, "tag": tag or category,
                   "url": "/", "ts": datetime.datetime.now(datetime.timezone.utc).isoformat()}       # with zone: the receiver must not guess
        loop = asyncio.get_running_loop()
        results = []
        for sub in subs:
            if not sub.subscription:     # could not be decrypted (key changed): useless
                await session.delete(sub)
                continue
            ok, err, expired = await loop.run_in_executor(None, cls._webpush_blocking, sub.subscription, payload)
            if ok:
                sub.last_success, sub.last_error = datetime.datetime.utcnow(), None
            elif expired:
                await session.delete(sub)
            else:
                sub.last_error = (err or "")[:500]
            results.append({"channel": "webpush", "device": sub.label or f"#{sub.id}", "success": ok, "error": err})
        return results

    @classmethod
    async def _send_pushover(
        cls, user_key: str, api_token: str, title: str, message: str
    ) -> Tuple[bool, Optional[str]]:
        """Sends a push notification through Pushover."""
        if not user_key or not api_token:
            return False, "Faltan User Key o API Token de Pushover."

        url = "https://api.pushover.net/1/messages.json"
        data = {
            "user": user_key.strip(),
            "token": api_token.strip(),
            "title": title,
            "message": message,
        }

        try:
            async with aiohttp.ClientSession() as client:
                async with client.post(url, data=data, timeout=8) as resp:
                    if resp.status == 200:
                        return True, None
                    text = await resp.text()
                    return False, f"HTTP {resp.status}: {text}"
        except Exception as e:
            return False, str(e)

    @classmethod
    async def _send_webhook(cls, webhook_url: str, payload: Dict[str, Any]) -> Tuple[bool, Optional[str]]:
        """Sends the event to a generic webhook (Home Assistant, Discord, etc.)."""
        if not webhook_url:
            return False, "Falta URL del webhook."
        problem = webhook_url_problem(webhook_url)
        if problem:
            return False, problem

        try:
            async with aiohttp.ClientSession() as client:
                # No redirects: a public URL answering 302 to an internal address would get round the check above.
                async with client.post(webhook_url.strip(), json=payload, timeout=8, allow_redirects=False) as resp:
                    if resp.status in (200, 201, 202, 204):
                        return True, None
                    text = await resp.text()
                    return False, f"HTTP {resp.status}: {text}"
        except Exception as e:
            return False, str(e)

    # ------------------------------------------------------------------------
    # Central dispatch, filtered by preferences
    # ------------------------------------------------------------------------

    @classmethod
    async def dispatch(
        cls,
        category: str,
        title: Union[str, Message],
        message: Union[str, Message],
        tags: Optional[List[str]] = None,
        priority: str = "default",
        vin: Optional[str] = None,
        user_id: Optional[int] = None,
    ) -> List[Dict[str, Any]]:
        """Sends a notification to the accounts concerned and through THEIR channels, honouring what each one switched on.

        `vin`: every account that can see that car receives it (owner and linked members). `user_id`: only that account (tests, "send me
        a test"). With neither, nobody receives it: a notification is never broadcast to everyone."""
        results: List[Dict[str, Any]] = []
        async with async_session_factory() as session:
            if user_id is not None:
                targets = [user_id]
            elif vin is not None:
                targets = [u.id for u in await cls.recipients(session, vin)]
            else:
                _LOGGER.warning("Notification «%s» with no car and no recipient: not sent.", category)
                return []
            for uid in targets:
                results.extend(await cls._dispatch_to_user(session, uid, category, title, message))
            await session.commit()
        return results

    @classmethod
    async def _dispatch_to_user(cls, session: AsyncSession, uid: int, category: str, title: Union[str, Message],
                                message: Union[str, Message]) -> List[Dict[str, Any]]:
        results: List[Dict[str, Any]] = []
        config = await cls.get_or_create_config(session, uid)
        # The texts come as `Message`s (Spanish with placeholders): each account reads them in its own language (app.i18n)
        account = await session.get(AppUser, uid)
        lang = account.language if account else None
        title, message = render(title, lang), render(message, lang)

        # 1. Does this account want this kind of notification?
        flag = CATEGORY_FLAG.get(category)
        if flag and not getattr(config, flag, True):
            _LOGGER.debug("Account %s: «%s» switched off in its settings.", uid, category)
            return []

        def log(channel: str, success: bool, error: Optional[str]) -> None:
            session.add(NotificationLog(user_id=uid, category=category, title=title, message=message, channel=channel, success=success, error=error))

        # 2. Send through each of ITS active channels
        # --- Web Push (Google FCM / Mozilla / Apple) ---
        for r in await cls.send_webpush(session, title, message, category, user_id=uid):
            log("webpush", r["success"], f"{r['device']}: {r['error']}" if r["error"] else None)
            results.append(r)

        # --- Telegram ---
        if config.telegram_enabled and config.telegram_bot_token and config.telegram_chat_id:
            ok, err = await cls._send_telegram(config.telegram_bot_token, config.telegram_chat_id, title, message)
            log("telegram", ok, err)
            results.append({"channel": "telegram", "success": ok, "error": err})

        # --- Pushover ---
        if config.pushover_enabled and config.pushover_user_key and config.pushover_api_token:
            ok, err = await cls._send_pushover(config.pushover_user_key, config.pushover_api_token, title, message)
            log("pushover", ok, err)
            results.append({"channel": "pushover", "success": ok, "error": err})

        # --- Webhook ---
        if config.webhook_enabled and config.webhook_url:
            payload = {"event": category, "title": title, "message": message,
                       "timestamp": datetime.datetime.now(datetime.timezone.utc).isoformat()}      # with zone (Home Assistant, Discord… parse it)
            ok, err = await cls._send_webhook(config.webhook_url, payload)
            log("webhook", ok, err)
            results.append({"channel": "webhook", "success": ok, "error": err})
        return results

    # ------------------------------------------------------------------------
    # High-level methods for EVBro events. The texts are `M()` messages (Spanish with placeholders) that `_dispatch_to_user`
    # renders in the language of each recipient (app.i18n); `Message.join` glues parts that are translated one by one.
    # ------------------------------------------------------------------------

    @classmethod
    async def notify_geofence_enter(
        cls,
        vehicle_name: str,
        geofence_name: str,
        battery_pct: Optional[float] = None,
        odometer: Optional[float] = None,
        vin: Optional[str] = None,
    ):
        """Notifies when the vehicle enters a geofence (e.g. home, work, dealer)."""
        details = []
        if battery_pct is not None:
            details.append(M("Batería: {p0}%", p0=round(battery_pct)))
        if odometer is not None:
            details.append(M("Odómetro: {p0} km", p0=Math_round_str(odometer)))
        msg = Message.join("\n", [M("El vehículo acaba de llegar a la geocerca '{p0}'.", p0=geofence_name), Message.join(" | ", details) if details else None])
        await cls.dispatch(
            vin=vin,
            category="geofence_enter",
            title=M("{p0} ha entrado en {p1}", p0=vehicle_name, p1=geofence_name),
            message=msg,
            tags=["round_pushpin", "car"],
            priority="default",
        )

    @classmethod
    async def notify_geofence_exit(
        cls,
        vehicle_name: str,
        geofence_name: str,
        battery_pct: Optional[float] = None,
        odometer: Optional[float] = None,
        vin: Optional[str] = None,
    ):
        """Notifies when the vehicle leaves a geofence."""
        details = []
        if battery_pct is not None:
            details.append(M("Batería: {p0}%", p0=round(battery_pct)))
        if odometer is not None:
            details.append(M("Odómetro: {p0} km", p0=Math_round_str(odometer)))
        msg = Message.join("\n", [M("El vehículo se ha puesto en marcha y abandonó '{p0}'.", p0=geofence_name), Message.join(" | ", details) if details else None])
        await cls.dispatch(
            vin=vin,
            category="geofence_exit",
            title=M("{p0} ha salido de {p1}", p0=vehicle_name, p1=geofence_name),
            message=msg,
            tags=["car", "arrow_right"],
            priority="default",
        )

    @classmethod
    async def notify_renewal(cls, vehicle_name: str, label: str, remaining_days: int, vin: Optional[str] = None):
        """Upcoming or past due date: insurance renewal, road tax or another dated expense."""
        capitalised = f"{label[:1].upper()}{label[1:]}"
        if remaining_days < 0:
            title = M("Vencido: {p0}", p0=label)
            msg = M("{p0} venció hace {p1} días ({p2}).", p0=capitalised, p1=abs(remaining_days), p2=vehicle_name)
            prio = "high"
        elif remaining_days == 0:
            title = M("Vence hoy: {p0}", p0=label)
            msg = M("{p0} vence hoy ({p1}).", p0=capitalised, p1=vehicle_name)
            prio = "high"
        else:
            title = M("Próximo vencimiento: {p0}", p0=label)
            msg = M("Faltan {p0} días para {p1} ({p2}).", p0=remaining_days, p1=label, p2=vehicle_name)
            prio = "default"
        await cls.dispatch(vin=vin, category="renewals", title=title, message=msg, tags=["calendar"], priority=prio)

    @classmethod
    async def notify_tyre_pressure(cls, vehicle_name: str, problems: List[str], vin: Optional[str] = None):
        """Tyres whose pressure is outside the range set on the vehicle."""
        await cls.dispatch(
            vin=vin,
            category="tyre_pressure",
            title=M("Presión de neumáticos: {p0}", p0=vehicle_name),
            message=Message.join("\n", [*problems, M("Revísala en frío; los neumáticos calientes marcan más presión.")]),
            tags=["warning"],
            priority="high",
        )

    @classmethod
    async def notify_maintenance_alert(
        cls,
        vehicle_name: str,
        revision_label: str,
        remaining_km: Optional[float],
        is_overdue: bool,
        replace_items: List[str],
        labor_hours: Optional[float] = None,
        remaining_days: Optional[int] = None,
        vin: Optional[str] = None,
    ):
        """Notifies upcoming and overdue maintenance (official EBRO service or a custom task)."""
        def due_span(overdue: bool) -> Message:
            """Overdue: how far past it is (km and/or days). Upcoming: what is left, whichever comes first."""
            parts = []
            if overdue:
                if remaining_km is not None and remaining_km <= 0:
                    parts.append(M("{p0} km", p0=abs(round(remaining_km))))
                if remaining_days is not None and remaining_days <= 0:
                    parts.append(M("{p0} días", p0=abs(remaining_days)))
            else:
                if remaining_km is not None:
                    parts.append(M("{p0} km", p0=round(remaining_km)))
                if remaining_days is not None:
                    parts.append(M("{p0} días", p0=remaining_days))
            if len(parts) == 2:
                return M("{p0} y {p1}" if overdue else "{p0} o {p1}", p0=parts[0], p1=parts[1])
            return parts[0] if parts else M("")

        extra = []
        if labor_hours:
            extra.append(M("Tiempo MO de taller: {p0} h.", p0=labor_hours))
        if replace_items:
            extra.append(M("Operaciones a realizar: {p0}.", p0=", ".join(replace_items)))
        capitalised = f"{revision_label[:1].upper()}{revision_label[1:]}"
        if is_overdue:
            title = M("Mantenimiento vencido: {p0}", p0=vehicle_name)
            msg = Message.join("\n", [M("{p0} está vencida desde hace {p1}.", p0=capitalised, p1=due_span(True)), *extra])
            prio = "high"
            tags = ["warning", "wrench"]
        else:
            title = M("Próximo mantenimiento: {p0}", p0=vehicle_name)
            whichever_first = M(" (lo que antes llegue)") if remaining_km is not None and remaining_days is not None else ""
            msg = Message.join("\n", [M("Faltan {p0}{p1} para {p2}.", p0=due_span(False), p1=whichever_first, p2=revision_label), *extra])
            prio = "default"
            tags = ["wrench"]

        await cls.dispatch(
            vin=vin,
            category="maintenance",
            title=title,
            message=msg,
            tags=tags,
            priority=prio,
        )

    @classmethod
    async def notify_charging_started(
        cls,
        vehicle_name: str,
        battery_pct: float,
        range_km: Optional[float] = None,
        location: Optional[str] = None,
        vin: Optional[str] = None,
    ):
        """Notifies when a charging session starts."""
        msg = Message.join("\n", [
            M("El vehículo ha comenzado a cargar al {p0}% de batería.", p0=round(battery_pct)),
            M("Autonomía actual: {p0} km.", p0=round(range_km)) if range_km is not None else None,
            M("Ubicación: {p0}", p0=location) if location else None,
        ])
        await cls.dispatch(
            vin=vin,
            category="charge_started",
            title=M("Carga iniciada: {p0}", p0=vehicle_name),
            message=msg,
            tags=["zap", "electric_plug"],
            priority="default",
        )

    @classmethod
    async def notify_charging_completed(
        cls,
        vehicle_name: str,
        battery_pct: float,
        range_km: Optional[float] = None,
        duration_min: Optional[float] = None,
        energy_kwh: Optional[float] = None,
        vin: Optional[str] = None,
    ):
        """Notifies when the battery has finished charging."""
        first = Message.join(" ", [M("Carga completada al {p0}%.", p0=round(battery_pct)),
                                   M("Autonomía eléctrica: {p0} km.", p0=round(range_km)) if range_km is not None else None])
        second = Message.join(" | ", [M("Duración: {p0} min.", p0=round(duration_min)) if duration_min else None,
                                      M("Energía añadida: ~{p0} kWh", p0=f"{energy_kwh:.1f}") if energy_kwh else None])
        await cls.dispatch(
            vin=vin,
            category="charge_completed",
            title=M("Carga finalizada: {p0}", p0=vehicle_name),
            message=Message.join("\n", [first, second if second.params else None]),
            tags=["battery", "white_check_mark"],
            priority="default",
        )

    @classmethod
    async def notify_security_alert(cls, vehicle_name: str, issues: List[str], vin: Optional[str] = None):
        """Notifies when the car was left parked with doors open or unlocked."""
        await cls.dispatch(
            vin=vin,
            category="security",
            title=M("Alerta de Seguridad: {p0}", p0=vehicle_name),
            message=Message.join("\n", [M("El vehículo está estacionado pero se han detectado los siguientes avisos:"), *(f"• {i}" for i in issues)]),
            tags=["lock", "rotating_light"],
            priority="high",
        )

    @classmethod
    async def notify_fuel_low(cls, vehicle_name: str, fuel_pct: float, range_km: Optional[float] = None, vin: Optional[str] = None):
        """Notifies when the fuel drops into the reserve."""
        first = Message.join(" ", [M("Nivel de gasolina al {p0}%.", p0=round(fuel_pct)),
                                   M("Autonomía térmica estimada: {p0} km.", p0=round(range_km)) if range_km is not None else None])
        await cls.dispatch(
            vin=vin,
            category="fuel_low",
            title=M("Reserva de Combustible: {p0}", p0=vehicle_name),
            message=Message.join("\n", [first, M("Se recomienda repostar en la próxima gasolinera.")]),
            tags=["fuelpump", "warning"],
            priority="default",
        )

    @classmethod
    async def notify_vehicle_fault(cls, vehicle_name: str, fault_description: str, vin: Optional[str] = None):
        """Notifies mechanical faults, fault codes (DTCs) or vehicle anomalies."""
        await cls.dispatch(
            vin=vin,
            category="vehicle_fault",
            title=M("Fallo Detectado en el Vehículo: {p0}", p0=vehicle_name),
            message=Message.join("\n", [M("Aviso de diagnosis: {p0}.", p0=fault_description),
                                        M("Por favor, revisa el cuadro de mandos o acude al taller oficial EBRO.")]),
            tags=["warning", "exclamation"],
            priority="high",
        )

    @classmethod
    async def notify_windows_open_alert(cls, vehicle_name: str, issues: List[str], vin: Optional[str] = None):
        """Notifies when the windows or the sunroof were left open after parking."""
        await cls.dispatch(
            vin=vin,
            category="windows_open",
            title=M("Ventanas Olvidadas Abiertas: {p0}", p0=vehicle_name),
            message=Message.join("\n", [M("El vehículo está cerrado pero se detectaron aberturas:"), *(f"• {i}" for i in issues),
                                        M("Puedes cerrarlas de inmediato desde el botón 'Cerrar ventanas' en EVBro.")]),
            tags=["window", "warning"],
            priority="high",
        )

    @classmethod
    async def notify_charge_interrupted(cls, vehicle_name: str, battery_pct: float, reason: Optional[str] = None, vin: Optional[str] = None):
        """Notifies when a charge in progress stops unexpectedly."""
        await cls.dispatch(
            vin=vin,
            category="charge_interrupted",
            title=M("Recarga Interrumpida: {p0}", p0=vehicle_name),
            message=Message.join("\n", [M("La sesión de recarga se ha detenido antes de completarse (batería al {p0}%).", p0=round(battery_pct)),
                                        M("Motivo: {p0}", p0=reason) if reason else None,
                                        M("Comprueba la manguera o el punto de carga.")]),
            tags=["warning", "electric_plug"],
            priority="high",
        )


def Math_round_str(val: float) -> str:
    try:
        return f"{int(round(val)):,}".replace(",", ".")
    except Exception:
        return str(val)

