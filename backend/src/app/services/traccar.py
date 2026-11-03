"""Positions from a Traccar Server (the car has its own GPS tracker reporting to Traccar).

Follows the Home Assistant `traccar_server` integration: one API token per server (Traccar's "account token"), REST only to
validate and to seed (`GET /api/server`, `/api/devices`, `/api/positions`) and a WebSocket (`/api/socket`) for live positions:
Traccar PUSHES every new fix over it, there is no polling. The WebSocket only accepts a session cookie, so the client first
calls `GET /api/session?token=…` (as pytraccar does) and closes the session afterwards.

`TraccarHub` keeps one subscription per server (several cars on the same Traccar share it), maps Traccar `deviceId` to the car
and hands each fix to `gps_positions.handle_fix` (store, map, drives). It reconnects with a growing delay and records the
last error on the link so the car sheet can show it.
"""
from __future__ import annotations

import asyncio
import dataclasses
import datetime
import logging
from typing import Any, Awaitable, Callable, Dict, List, Optional, Tuple

import aiohttp
from sqlalchemy import select

from app.db.models import TraccarLink, Vehicle
from app.db.session import async_session_factory

_LOGGER = logging.getLogger(__name__)
KNOTS_TO_KMH = 1.852
RECONNECT_DELAYS = (10, 30, 60, 120, 300)
SWEEP_EVERY_S = 60                      # how often the GPS-built trips of silent trackers are checked


class TraccarError(Exception):
    pass


@dataclasses.dataclass
class Fix:
    device_id: int
    timestamp: datetime.datetime          # naive UTC (Traccar's fixTime)
    latitude: float
    longitude: float
    altitude: Optional[float] = None
    speed_kmh: Optional[float] = None
    course: Optional[float] = None
    accuracy_m: Optional[float] = None
    ignition: Optional[bool] = None
    motion: Optional[bool] = None
    battery_pct: Optional[float] = None
    traccar_id: Optional[int] = None


def _when(value: Any) -> datetime.datetime:
    try:
        ts = datetime.datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        return ts.astimezone(datetime.timezone.utc).replace(tzinfo=None) if ts.tzinfo else ts
    except (TypeError, ValueError):
        return datetime.datetime.utcnow()


def parse_position(p: Dict[str, Any]) -> Optional[Fix]:
    """A Traccar `Position` object (REST or WebSocket) → Fix. Speed comes in knots; `attributes` carries ignition/motion/battery."""
    try:
        lat, lon = float(p["latitude"]), float(p["longitude"])
    except (KeyError, TypeError, ValueError):
        return None
    if not (-90 <= lat <= 90 and -180 <= lon <= 180) or (lat == 0 and lon == 0):
        return None
    attrs = p.get("attributes") or {}
    speed = p.get("speed")
    battery = attrs.get("batteryLevel", attrs.get("battery"))
    return Fix(device_id=int(p.get("deviceId") or 0), timestamp=_when(p.get("fixTime") or p.get("deviceTime") or p.get("serverTime")),
               latitude=lat, longitude=lon, altitude=p.get("altitude"),
               speed_kmh=round(float(speed) * KNOTS_TO_KMH, 1) if speed is not None else None, course=p.get("course"),
               accuracy_m=p.get("accuracy"), ignition=attrs.get("ignition"), motion=attrs.get("motion"),
               battery_pct=float(battery) if isinstance(battery, (int, float)) else None, traccar_id=p.get("id"))


def base_url(host: str, port: Optional[int], use_ssl: bool) -> str:
    host = (host or "").strip().rstrip("/")
    if host.startswith(("http://", "https://")):           # a full URL pasted in: respect it
        return host + "/api"
    return f"{'https' if use_ssl else 'http'}://{host}:{port or 8082}/api"


class TraccarClient:
    """The few calls EVBro needs, with the token as `Authorization: Bearer` (REST) or as a session (WebSocket)."""

    def __init__(self, host: str, token: str, port: Optional[int] = None, use_ssl: bool = False, verify_ssl: bool = True) -> None:
        self.base = base_url(host, port, use_ssl)
        self.token = token
        self.verify_ssl = verify_ssl

    def _session(self) -> aiohttp.ClientSession:
        return aiohttp.ClientSession(headers={"Authorization": f"Bearer {self.token}"}, connector=aiohttp.TCPConnector(ssl=self.verify_ssl),
                                     timeout=aiohttp.ClientTimeout(total=20))

    async def _get(self, path: str, params: Optional[Dict[str, Any]] = None) -> Any:
        try:
            async with self._session() as s, s.get(self.base + path, params=params) as r:
                if r.status == 401:
                    raise TraccarError("Traccar rechaza el token (401).")
                if r.status >= 400:
                    raise TraccarError(f"Traccar respondió {r.status} a {path}.")
                return await r.json(content_type=None)
        except aiohttp.ClientError as ex:
            raise TraccarError(f"No se pudo conectar con Traccar: {ex}") from ex
        except asyncio.TimeoutError as ex:
            raise TraccarError("Traccar no respondió a tiempo.") from ex

    async def get_server(self) -> Dict[str, Any]:
        return await self._get("/server")

    async def get_devices(self) -> List[Dict[str, Any]]:
        return await self._get("/devices")

    async def get_positions(self) -> List[Fix]:
        return [f for f in (parse_position(p) for p in await self._get("/positions")) if f]

    async def subscribe(self, on_positions: Callable[[List[Fix]], Awaitable[None]], stop: asyncio.Event) -> None:
        """Open the WebSocket and deliver every batch of positions until `stop` is set or the connection drops (raises)."""
        async with aiohttp.ClientSession(connector=aiohttp.TCPConnector(ssl=self.verify_ssl)) as s:
            async with s.get(self.base + "/session", params={"token": self.token}) as r:        # cookie session for the socket
                if r.status != 200:
                    raise TraccarError(f"Traccar rechaza el token para la sesión ({r.status}).")
            try:
                async with s.ws_connect(self.base + "/socket", heartbeat=120) as ws:
                    while not stop.is_set():
                        try:
                            msg = await asyncio.wait_for(ws.receive(), timeout=5)
                        except asyncio.TimeoutError:
                            continue
                        if msg.type in (aiohttp.WSMsgType.CLOSED, aiohttp.WSMsgType.CLOSING, aiohttp.WSMsgType.ERROR):
                            raise TraccarError("Traccar cerró la conexión.")
                        if msg.type != aiohttp.WSMsgType.TEXT:
                            continue
                        try:
                            data = msg.json()
                        except ValueError:
                            continue
                        fixes = [f for f in (parse_position(p) for p in (data or {}).get("positions") or []) if f]
                        if fixes:
                            await on_positions(fixes)
            finally:
                try:
                    await s.delete(self.base + "/session")
                except Exception:                              # noqa: BLE001 — best effort
                    pass


ServerKey = Tuple[str, str, bool]      # base url, token, verify_ssl


class TraccarHub:
    """One WebSocket per Traccar server; fixes routed to cars by deviceId."""
    _instance: Optional["TraccarHub"] = None

    def __init__(self) -> None:
        self._tasks: Dict[ServerKey, asyncio.Task] = {}
        self._stops: Dict[ServerKey, asyncio.Event] = {}
        self._routes: Dict[ServerKey, Dict[int, str]] = {}         # device id → vin
        self._status: Dict[str, Dict[str, Any]] = {}                # vin → {"connected": bool, "error": str|None}
        self._running = False
        self._sweeper: Optional[asyncio.Task] = None

    @classmethod
    def get_instance(cls) -> "TraccarHub":
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance

    def status(self, vin: str) -> Dict[str, Any]:
        return self._status.get(vin, {"connected": False, "error": None})

    async def start(self) -> None:
        self._running = True
        await self.reload()

    async def stop(self) -> None:
        self._running = False
        for ev in self._stops.values():
            ev.set()
        for t in list(self._tasks.values()):
            t.cancel()
        if self._sweeper is not None:
            self._sweeper.cancel()
        await asyncio.gather(*self._tasks.values(), *([self._sweeper] if self._sweeper else []), return_exceptions=True)
        self._sweeper = None
        self._tasks.clear()
        self._stops.clear()
        self._routes.clear()

    async def reload(self) -> None:
        """Re-read the enabled links and (re)start one subscription per distinct server."""
        running = self._running
        await self.stop()
        self._running = running
        if not running:
            return
        async with async_session_factory() as session:
            rows = (await session.execute(
                select(TraccarLink, Vehicle).join(Vehicle, Vehicle.vin == TraccarLink.vin).where(TraccarLink.enabled.is_(True)))).all()
        for link, _vehicle in rows:
            key: ServerKey = (base_url(link.host, link.port, link.use_ssl), link.api_token or "", bool(link.verify_ssl))
            self._routes.setdefault(key, {})[int(link.device_id)] = link.vin
            self._status[link.vin] = {"connected": False, "error": None}
        for key, devices in self._routes.items():
            self._stops[key] = asyncio.Event()
            self._tasks[key] = asyncio.create_task(self._run(key, devices))
        if rows:
            _LOGGER.info("Traccar: %d server(s), %d car(s).", len(self._routes), len(rows))
        if any(link.build_trips for link, _vehicle in rows):
            self._sweeper = asyncio.create_task(self._sweep())

    async def _sweep(self) -> None:
        """Every minute: close the GPS-built drives whose tracker fell asleep after parking (gps_positions.close_silent_drives)."""
        from app.services import gps_positions
        while True:
            await asyncio.sleep(SWEEP_EVERY_S)
            try:
                async with async_session_factory() as session:
                    await gps_positions.close_silent_drives(session)
            except asyncio.CancelledError:
                raise
            except Exception as ex:                                 # noqa: BLE001 — logged, tried again next minute
                _LOGGER.warning("Traccar: closing silent trips failed: %s", ex)

    def _set_status(self, vins, connected: bool, error: Optional[str]) -> None:
        for vin in vins:
            self._status[vin] = {"connected": connected, "error": error}

    async def _run(self, key: ServerKey, devices: Dict[int, str]) -> None:
        base, token, verify = key
        client = TraccarClient(base[: -len("/api")], token, verify_ssl=verify)
        stop = self._stops[key]
        attempt = 0
        while not stop.is_set():
            try:
                seed = await client.get_positions()                 # the last known fix of every device, before the live feed
                await self._deliver(devices, [f for f in seed if f.device_id in devices])
                self._set_status(devices.values(), True, None)
                attempt = 0
                await client.subscribe(lambda fixes: self._deliver(devices, [f for f in fixes if f.device_id in devices]), stop)
            except asyncio.CancelledError:
                raise
            except Exception as ex:                                 # noqa: BLE001 — logged, retried
                message = str(ex) or ex.__class__.__name__
                self._set_status(devices.values(), False, message)
                _LOGGER.warning("Traccar %s: %s (retrying in %ss)", base, message, RECONNECT_DELAYS[min(attempt, len(RECONNECT_DELAYS) - 1)])
            if stop.is_set():
                break
            await asyncio.sleep(RECONNECT_DELAYS[min(attempt, len(RECONNECT_DELAYS) - 1)])
            attempt += 1

    async def _deliver(self, devices: Dict[int, str], fixes: List[Fix]) -> None:
        if not fixes:
            return
        from app.services import gps_positions
        async with async_session_factory() as session:
            for fix in sorted(fixes, key=lambda f: f.timestamp):
                vin = devices.get(fix.device_id)
                if vin:
                    try:
                        await gps_positions.handle_fix(session, vin, fix)
                    except Exception as ex:                         # noqa: BLE001 — one bad fix must not kill the feed
                        _LOGGER.warning("Traccar: position of %s discarded: %s", vin[-6:], ex)
                        await session.rollback()
