"""Anonymous usage statistics and error reports, so the project can see what is used and fix what breaks (docs/telemetry.md).

Two roles, both in every installation:

* **Sender.** Off until the administrator turns it on in the console (`telemetry_enabled` in `app_settings`; absent = not decided
  yet, and the console asks). Once a day it sends ONE report to `TELEMETRY_URL` + `REPORT_PATH`: version, sizes in ranges, car
  models, which features are in use (yes/no), poll and command outcomes, and the errors and warnings logged since the last report.
  The administrator can see the exact report before turning it on (`build_report`). An empty `TELEMETRY_URL` disables it.
* **Receiver** (only with `TELEMETRY_RECEIVER`, the project's own server): stores the reports (`receive`) and aggregates them for
  its console (`summary`). It never stores the sender's IP; the reverse proxy in front of it should not log that host either.

What a report never carries: names, usernames, e-mails, VINs, plates, positions, addresses, geofences, trips, times of use,
kilometres, amounts, tokens or anything a manager wrote. Error messages are scrubbed (`scrub`) and only frames of EVBro's own code
travel, as `file:line function`.
"""
from __future__ import annotations

import asyncio
import datetime
import hashlib
import json
import logging
import os
import platform
import re
import sys
import threading
import time
import traceback
import uuid
from typing import Any, Dict, List, Optional, Tuple

import aiohttp
from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app import __version__, build_commit
from app.config import settings

_LOGGER = logging.getLogger("app.telemetry")

FORMAT = "evbro-telemetry/1"
REPORT_PATH = "/api/telemetry/v1/report"
SETTING_ENABLED = "telemetry_enabled"          # "1" | "0"; absent = the administrator has not decided yet
SETTING_INSTALL_ID = "telemetry_install_id"
SETTING_LAST = "telemetry_last"                # JSON {"at", "ok", "status", "error"}
SEND_EVERY = datetime.timedelta(hours=24)
FIRST_SEND_DELAY_S = 10 * 60                   # after a start-up, so a crash loop does not send a report per start
FLUSH_EVERY_S = 5 * 60
TIMEOUT_S = 20
MAX_LOCAL_GROUPS = 300                         # distinct errors kept waiting for a report
MAX_REPORT_ERRORS = 150
MAX_FRAMES = 12
MAX_MESSAGE = 300
MAX_REPORT_BYTES = 256 * 1024
COMMAND_WINDOW_MAX = datetime.timedelta(days=7)

_STARTED = time.time()
_APP_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))       # .../app


def _now() -> datetime.datetime:
    return datetime.datetime.utcnow()


# ───────────────────────── scrubbing ─────────────────────────
_RE_EMAIL = re.compile(r"[\w.+-]+@[\w-]+(\.[\w-]+)+")
_RE_URL = re.compile(r"\b(?:https?|wss?|mqtts?)://\S+", re.I)
_RE_VIN = re.compile(r"\b[A-HJ-NPR-Z0-9]{17}\b")
_RE_TOKEN = re.compile(r"\b[A-Za-z0-9_\-+/=]{20,}\b")
_RE_IP = re.compile(r"\b\d{1,3}(?:\.\d{1,3}){3}\b")
_RE_COORD = re.compile(r"-?\d{1,3}\.\d{3,}")
_RE_NUMBER = re.compile(r"\d+(?:[.,]\d+)*")
_RE_QUOTED = re.compile(r"(['\"«])(.*?)(['\"»])")


def scrub(text: Optional[str]) -> Optional[str]:
    """A message with nothing personal left: addresses, e-mails, VINs, tokens, IPs, coordinates and numbers are replaced, and
    quoted text is kept only when it looks like an identifier (`'vin'`, `"battery_level"`), not like something a person wrote."""
    if not text:
        return None
    t = str(text)
    t = _RE_URL.sub("<url>", t)
    t = _RE_EMAIL.sub("<email>", t)
    t = _RE_VIN.sub("<vin>", t)
    t = _RE_IP.sub("<ip>", t)
    t = _RE_TOKEN.sub("<id>", t)
    t = _RE_COORD.sub("<n>", t)
    t = _RE_NUMBER.sub("<n>", t)

    def _quoted(m: re.Match) -> str:
        inner = m.group(2)
        return m.group(0) if re.fullmatch(r"[A-Za-z_][\w.\-]{0,40}|<\w+>", inner or "") else f"{m.group(1)}<text>{m.group(3)}"

    t = _RE_QUOTED.sub(_quoted, t)
    t = " ".join(t.split())
    return t[:MAX_MESSAGE] or None


def _app_frames(tb) -> List[str]:
    """`file:line function` of the frames that belong to EVBro (innermost last); library frames are left out."""
    frames = []
    for fs in traceback.extract_tb(tb):
        path = os.path.abspath(fs.filename)
        if path.startswith(_APP_DIR + os.sep):
            frames.append(f"app/{os.path.relpath(path, _APP_DIR)}:{fs.lineno} {fs.name}")
    return frames[-MAX_FRAMES:]


def fingerprint(kind: str, error_type: Optional[str], location: Optional[str], frames: Optional[List[str]]) -> str:
    """The same error in the same place of the code gives the same fingerprint, whatever the message (numbers change)."""
    where = [re.sub(r":\d+ ", " ", f) for f in (frames or [])[-4:]] or [re.sub(r":\d+", "", location or "")]
    raw = "|".join([kind, error_type or "", *where])
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:32]


# ───────────────────────── collection (in memory, flushed to the database) ─────────────────────────
class _State:
    def __init__(self) -> None:
        self.enabled = False                       # mirror of the setting, so logging never queries the database
        self.lock = threading.Lock()
        self.errors: Dict[str, Dict[str, Any]] = {}
        self.polls = {"ok": 0, "empty": 0, "error": 0}
        self.polls_since = _now()


_state = _State()


def set_enabled_cache(enabled: bool) -> None:
    _state.enabled = bool(enabled and (settings.TELEMETRY_URL or "").strip())


def is_collecting() -> bool:
    return _state.enabled


def record_error(kind: str, level: str, error_type: Optional[str], message: Optional[str], location: Optional[str],
                 frames: Optional[List[str]]) -> None:
    if not _state.enabled:
        return
    fp = fingerprint(kind, error_type, location, frames)
    now = _now()
    with _state.lock:
        e = _state.errors.get(fp)
        if e is None:
            if len(_state.errors) >= MAX_LOCAL_GROUPS:
                return
            _state.errors[fp] = {"fingerprint": fp, "kind": kind, "level": level, "error_type": (error_type or "")[:120] or None,
                                 "message": scrub(message), "location": (location or "")[:240] or None, "frames": frames or [],
                                 "count": 1, "first_seen": now, "last_seen": now}
        else:
            e["count"] += 1
            e["last_seen"] = now


def count_poll(outcome: str) -> None:
    """`ok` (data stored), `empty` (the cloud answered nothing) or `error` (the read failed)."""
    if _state.enabled and outcome in _state.polls:
        with _state.lock:
            _state.polls[outcome] += 1


class TelemetryLogHandler(logging.Handler):
    """Every WARNING and ERROR logged by the server becomes an error group while the reports are on."""

    def __init__(self) -> None:
        super().__init__(level=logging.WARNING)

    def emit(self, record: logging.LogRecord) -> None:
        if not _state.enabled or record.name.startswith("app.telemetry"):
            return
        try:
            level = "error" if record.levelno >= logging.ERROR else "warning"
            if record.exc_info and record.exc_info[1] is not None:
                exc = record.exc_info[1]
                frames = _app_frames(record.exc_info[2])
                error_type = type(exc).__name__
                message = str(exc)
            else:
                frames = []
                error_type = record.name
                message = record.getMessage()
            path = os.path.abspath(record.pathname or "")
            here = f"app/{os.path.relpath(path, _APP_DIR)}" if path.startswith(_APP_DIR + os.sep) else os.path.basename(path)
            record_error("server", level, error_type, message, f"{here}:{record.lineno} {record.funcName}", frames)
        except Exception:                                   # noqa: BLE001 — logging must never fail because of this
            pass


_handler: Optional[TelemetryLogHandler] = None


def install_log_handler() -> None:
    global _handler
    if _handler is None:
        _handler = TelemetryLogHandler()
        logging.getLogger().addHandler(_handler)


def record_web_error(data: Dict[str, Any]) -> None:
    """An error of the web app, sent by the browser of a signed-in user (core/error-report.js)."""
    if not _state.enabled or not isinstance(data, dict):
        return
    stack = data.get("stack") if isinstance(data.get("stack"), list) else []
    frames = []
    for f in stack[:MAX_FRAMES]:
        m = re.fullmatch(r"([\w./-]{1,120}\.js):(\d{1,7})(?::\d{1,7})?(?: ([\w$.<>]{1,80}))?", str(f).strip())
        if m:
            name = m.group(1).split("/static/")[-1].split("?")[0]
            frames.append(f"{name}:{m.group(2)}" + (f" {m.group(3)}" if m.group(3) else ""))
    error_type = str(data.get("type") or "Error")[:60]
    if not re.fullmatch(r"[\w$.]{1,60}", error_type):
        error_type = "Error"
    record_error("web", "error", error_type, str(data.get("message") or "")[:1000], frames[0] if frames else None, list(reversed(frames)))


async def flush(session: AsyncSession) -> int:
    """Moves the errors collected in memory into `telemetry_local_errors` (so a restart does not lose them)."""
    from app.db.models import TelemetryLocalError
    with _state.lock:
        pending, _state.errors = _state.errors, {}
    if not pending:
        return 0
    stored = (await session.execute(select(func.count()).select_from(TelemetryLocalError))).scalar_one()
    for fp, e in pending.items():
        row = await session.get(TelemetryLocalError, fp)
        if row is None:
            if stored >= MAX_LOCAL_GROUPS:
                continue
            session.add(TelemetryLocalError(**e))
            stored += 1
        else:
            row.count = (row.count or 0) + e["count"]
            row.last_seen = e["last_seen"]
            row.message = e["message"] or row.message
    await session.commit()
    return len(pending)


# ───────────────────────── settings ─────────────────────────
async def _get(session: AsyncSession, key: str) -> Optional[str]:
    from app.db.models import AppSetting
    row = await session.get(AppSetting, key)
    return row.value if row is not None else None


async def _set(session: AsyncSession, key: str, value: Optional[str]) -> None:
    from app.db.models import AppSetting
    row = await session.get(AppSetting, key)
    if row is None:
        session.add(AppSetting(key=key, value=value))
    else:
        row.value = value
    await session.commit()


async def load_enabled(session: AsyncSession) -> Optional[bool]:
    """True / False, or None while the administrator has not decided. Also refreshes the in-memory switch."""
    raw = await _get(session, SETTING_ENABLED)
    enabled = None if raw is None else raw == "1"
    set_enabled_cache(bool(enabled))
    return enabled


async def set_enabled(session: AsyncSession, enabled: bool) -> None:
    await _set(session, SETTING_ENABLED, "1" if enabled else "0")
    set_enabled_cache(enabled)
    if not enabled:                                      # turning it off forgets what was waiting to be sent
        from app.db.models import TelemetryLocalError
        with _state.lock:
            _state.errors.clear()
        await session.execute(delete(TelemetryLocalError))
        await session.commit()


async def install_id(session: AsyncSession) -> str:
    value = await _get(session, SETTING_INSTALL_ID)
    if not value:
        value = str(uuid.uuid4())
        await _set(session, SETTING_INSTALL_ID, value)
    return value


async def reset_install_id(session: AsyncSession) -> str:
    value = str(uuid.uuid4())
    await _set(session, SETTING_INSTALL_ID, value)
    return value


async def last_result(session: AsyncSession) -> Optional[Dict[str, Any]]:
    raw = await _get(session, SETTING_LAST)
    try:
        return json.loads(raw) if raw else None
    except ValueError:
        return None


async def state(session: AsyncSession) -> Dict[str, Any]:
    from app.db.models import TelemetryLocalError
    enabled = await load_enabled(session)
    pending = (await session.execute(select(func.count()).select_from(TelemetryLocalError))).scalar_one() + len(_state.errors)
    url = (settings.TELEMETRY_URL or "").strip().rstrip("/") or None
    return {"enabled": enabled, "decided": enabled is not None, "url": url, "available": url is not None,
            "install_id": await install_id(session), "last": await last_result(session), "pending_errors": pending,
            "receiver": settings.TELEMETRY_RECEIVER}


# ───────────────────────── the report ─────────────────────────
def bucket(n: int) -> str:
    """Counts travel as ranges: with few installations an exact number could tell who is who."""
    if n <= 0:
        return "0"
    if n == 1:
        return "1"
    if n <= 5:
        return "2-5"
    if n <= 20:
        return "6-20"
    return "21+"


async def _count(session: AsyncSession, model, *where) -> int:
    return (await session.execute(select(func.count()).select_from(model).where(*where))).scalar_one()


async def _features(session: AsyncSession) -> Dict[str, bool]:
    from app.db.models import (
        CatalogRemote,
        ChargePlan,
        ClimateSchedule,
        Expense,
        Geofence,
        Journey,
        MaintenancePlan,
        MqttLink,
        NotificationConfig,
        PasskeyCredential,
        PushSubscription,
        Refuel,
        TraccarLink,
        VehicleDocument,
        VehiclePart,
    )
    catalog = await session.get(CatalogRemote, 1)
    return {
        "server_mqtt": bool(settings.MQTT_ENABLED), "car_mqtt": bool(settings.CAR_MQTT_ENABLED), "geocoding": bool(settings.NOMINATIM_ENABLED),
        "account_mqtt": await _count(session, MqttLink) > 0, "traccar": await _count(session, TraccarLink) > 0,
        "web_push": await _count(session, PushSubscription) > 0,
        "telegram": await _count(session, NotificationConfig, NotificationConfig.telegram_enabled.is_(True)) > 0,
        "pushover": await _count(session, NotificationConfig, NotificationConfig.pushover_enabled.is_(True)) > 0,
        "webhook": await _count(session, NotificationConfig, NotificationConfig.webhook_enabled.is_(True)) > 0,
        "passkeys": await _count(session, PasskeyCredential) > 0,
        "online_catalog": bool(catalog is not None and catalog.source == "remote"),
        "climate_schedules": await _count(session, ClimateSchedule) > 0, "charge_plans": await _count(session, ChargePlan) > 0,
        "geofences": await _count(session, Geofence) > 0, "maintenance_plans": await _count(session, MaintenancePlan) > 0,
        "refuels": await _count(session, Refuel) > 0, "expenses": await _count(session, Expense) > 0,
        "documents": await _count(session, VehicleDocument) > 0, "parts": await _count(session, VehiclePart) > 0,
        "journeys": await _count(session, Journey) > 0,
    }


def _clean_label(value: Optional[str], limit: int = 40) -> Optional[str]:
    """A car model as the Ebro cloud names it («S900 PHEV»): digits stay, anything that could identify someone does not."""
    text = _RE_VIN.sub("", _RE_EMAIL.sub("", _RE_URL.sub("", str(value or ""))))
    text = " ".join(re.sub(r"[^\w .+-]", " ", text).split())
    return text[:limit] or None


async def _commands(session: AsyncSession, since: datetime.datetime) -> List[Dict[str, Any]]:
    """Commands since the last report, by command and car model: how many worked and the failure codes."""
    from app.db.models import CommandAudit, Vehicle
    rows = (await session.execute(
        select(CommandAudit.command, Vehicle.model, CommandAudit.success, CommandAudit.code, func.count())
        .join(Vehicle, Vehicle.vin == CommandAudit.vin)
        .where(CommandAudit.timestamp >= since)
        .group_by(CommandAudit.command, Vehicle.model, CommandAudit.success, CommandAudit.code))).all()
    out: Dict[Tuple[str, Optional[str]], Dict[str, Any]] = {}
    for command, model, success, code, n in rows:
        key = (str(command)[:50], _clean_label(model))
        item = out.setdefault(key, {"command": key[0], "model": key[1], "ok": 0, "failed": 0, "codes": {}})
        if success:
            item["ok"] += n
        else:
            item["failed"] += n
            c = re.sub(r"[^\w-]", "", str(code or "none"))[:20] or "none"
            item["codes"][c] = item["codes"].get(c, 0) + n
    return sorted(out.values(), key=lambda i: (i["command"], i["model"] or ""))


async def build_report(session: AsyncSession, include_errors: bool = True) -> Dict[str, Any]:
    """Exactly what would be sent now. The console shows it before the administrator turns the reports on."""
    from app.db.models import AppUser, EbroAccount, TelemetryLocalError, Vehicle
    now = _now()
    last = await last_result(session)
    since = now - COMMAND_WINDOW_MAX
    if last and last.get("ok") and last.get("at"):
        try:
            since = max(since, datetime.datetime.fromisoformat(last["at"]))
        except ValueError:
            pass

    models: Dict[Tuple[Optional[str], str], int] = {}
    for model, fuel_type in (await session.execute(select(Vehicle.model, Vehicle.fuel_type))).all():
        key = (_clean_label(model), str(fuel_type or "")[:10])
        models[key] = models.get(key, 0) + 1
    languages = sorted({str(lang)[:5] for (lang,) in (await session.execute(select(AppUser.language).where(AppUser.role != "admin"))).all() if lang})
    dialect = session.bind.dialect.name if session.bind is not None else "unknown"

    report: Dict[str, Any] = {
        "format": FORMAT,
        "install_id": await install_id(session),
        "sent_at": now.isoformat(timespec="seconds"),
        "server": {"version": __version__, "commit": (build_commit() or "")[:12] or None, "python": platform.python_version(),
                   "arch": platform.machine()[:20], "os": sys.platform, "database": dialect,
                   "uptime_hours": round((time.time() - _STARTED) / 3600, 1), "languages": languages},
        "size": {"managers": bucket(await _count(session, AppUser, AppUser.role == "manager")),
                 "members": bucket(await _count(session, AppUser, AppUser.role == "member")),
                 "cars": bucket(await _count(session, Vehicle, Vehicle.is_manual.is_(False))),
                 "manual_cars": bucket(await _count(session, Vehicle, Vehicle.is_manual.is_(True))),
                 "ebro_accounts": bucket(await _count(session, EbroAccount, EbroAccount.is_active.is_(True)))},
        "models": [{"model": m, "fuel_type": f, "cars": bucket(n)} for (m, f), n in sorted(models.items(), key=lambda i: (i[0][0] or "", i[0][1]))],
        "features": await _features(session),
        "polls": {**_state.polls, "since": _state.polls_since.isoformat(timespec="seconds")},
        "commands": await _commands(session, since),
        "errors": [],
    }
    if include_errors:
        await flush(session)
        rows = (await session.execute(select(TelemetryLocalError).order_by(TelemetryLocalError.count.desc()).limit(MAX_REPORT_ERRORS))).scalars().all()
        report["errors"] = [{"fingerprint": r.fingerprint, "kind": r.kind, "level": r.level, "type": r.error_type, "message": r.message,
                             "location": r.location, "frames": r.frames or [], "count": r.count,
                             "first_seen": r.first_seen.isoformat(timespec="seconds"), "last_seen": r.last_seen.isoformat(timespec="seconds")}
                            for r in rows]
    return report


async def _http(url: str, payload: Dict[str, Any]) -> Tuple[int, Any]:
    """One POST to the receiver. Replaced in tests."""
    timeout = aiohttp.ClientTimeout(total=TIMEOUT_S)
    async with aiohttp.ClientSession(timeout=timeout, headers={"User-Agent": f"EVBro/{__version__}"}) as s:
        async with s.post(url, json=payload) as r:
            try:
                body = await r.json(content_type=None)
            except Exception:                               # noqa: BLE001 — a proxy error page
                body = None
            return r.status, body


async def send(session: AsyncSession, force: bool = False) -> Dict[str, Any]:
    """Sends one report when the reports are on (or `force`, from the console button). Records the outcome either way."""
    url = (settings.TELEMETRY_URL or "").strip().rstrip("/")
    enabled = await load_enabled(session)
    if not url or not (enabled or force):
        return {"ok": False, "status": None, "error": "Los informes están desactivados."}
    report = await build_report(session)
    while len(json.dumps(report)) > MAX_REPORT_BYTES and report["errors"]:
        report["errors"] = report["errors"][: len(report["errors"]) // 2]
    now = _now()
    try:
        status, body = await _http(url + REPORT_PATH, report)
        ok = status == 200
        error = None if ok else (body.get("detail") if isinstance(body, dict) and isinstance(body.get("detail"), str) else f"HTTP {status}")
    except Exception as ex:                                 # noqa: BLE001 — a network error is recorded, never raised
        status, ok, error = None, False, f"No se pudo conectar: {type(ex).__name__}"
    if ok:
        from app.db.models import TelemetryLocalError
        sent = [e["fingerprint"] for e in report["errors"]]
        if sent:
            await session.execute(delete(TelemetryLocalError).where(TelemetryLocalError.fingerprint.in_(sent)))
        with _state.lock:
            _state.polls = {"ok": 0, "empty": 0, "error": 0}
            _state.polls_since = now
    result = {"at": now.isoformat(timespec="seconds"), "ok": ok, "status": status, "error": (error or "")[:200] or None,
              "errors_sent": len(report["errors"]) if ok else 0}
    await _set(session, SETTING_LAST, json.dumps(result))
    if not ok:
        _LOGGER.info(f"Telemetry report not accepted: {result['error']}")
    return result


async def due(session: AsyncSession) -> bool:
    last = await last_result(session)
    if not last or not last.get("at"):
        return True
    try:
        at = datetime.datetime.fromisoformat(last["at"])
    except ValueError:
        return True
    # a failed send is retried after a few hours, not at once
    return _now() - at >= (SEND_EVERY if last.get("ok") else datetime.timedelta(hours=3))


class TelemetryService:
    """Background task: flushes the collected errors every few minutes and sends the daily report."""
    _instance: Optional["TelemetryService"] = None

    def __init__(self) -> None:
        self._task: Optional[asyncio.Task] = None

    @classmethod
    def get_instance(cls) -> "TelemetryService":
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance

    async def start(self) -> None:
        install_log_handler()
        try:
            from app.db.session import async_session_factory
            async with async_session_factory() as session:
                await load_enabled(session)
        except Exception as ex:                             # noqa: BLE001
            _LOGGER.warning(f"Telemetry: could not read the setting: {ex}")
        self._task = asyncio.create_task(self._loop())

    async def stop(self) -> None:
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except (asyncio.CancelledError, Exception):     # noqa: BLE001
                pass
            self._task = None
        try:
            from app.db.session import async_session_factory
            async with async_session_factory() as session:
                await flush(session)
        except Exception:                                   # noqa: BLE001 — shutting down
            pass

    async def _loop(self) -> None:
        from app.db.session import async_session_factory
        started = time.time()
        while True:
            try:
                await asyncio.sleep(FLUSH_EVERY_S)
                async with async_session_factory() as session:
                    enabled = await load_enabled(session)
                    await flush(session)
                    if enabled and time.time() - started >= FIRST_SEND_DELAY_S and await due(session):
                        await send(session)
            except asyncio.CancelledError:
                break
            except Exception as ex:                         # noqa: BLE001 — the loop must survive
                _LOGGER.warning(f"Telemetry loop: {ex}")


# ───────────────────────── receiver (TELEMETRY_RECEIVER) ─────────────────────────
class ReportError(Exception):
    def __init__(self, status_code: int, detail: str):
        super().__init__(detail)
        self.status_code, self.detail = status_code, detail


_RE_INSTALL = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}")
MIN_REPORT_INTERVAL = datetime.timedelta(minutes=30)
RETENTION = datetime.timedelta(days=180)
KEEP_VERSIONS = 8


def _s(value: Any, limit: int) -> Optional[str]:
    return str(value)[:limit] if value is not None and str(value).strip() else None


def _i(value: Any) -> int:
    return value if isinstance(value, int) and 0 <= value < 10**9 else 0


async def receive(session: AsyncSession, report: Dict[str, Any]) -> Dict[str, Any]:
    """Stores one report of another installation. Nothing about who sent it is kept besides its random id."""
    from app.db.models import TelemetryErrorGroup, TelemetryErrorHit, TelemetryInstall
    if not isinstance(report, dict) or report.get("format") != FORMAT:
        raise ReportError(422, "Formato de informe desconocido.")
    iid = str(report.get("install_id") or "").lower()
    if not _RE_INSTALL.fullmatch(iid):
        raise ReportError(422, "Identificador de instalación no válido.")
    now = _now()
    server = report.get("server") if isinstance(report.get("server"), dict) else {}
    version, commit = _s(server.get("version"), 40), _s(server.get("commit"), 40)

    inst = (await session.execute(select(TelemetryInstall).where(TelemetryInstall.install_id == iid))).scalar_one_or_none()
    if inst is not None and now - inst.last_seen < MIN_REPORT_INTERVAL:
        raise ReportError(429, "Demasiados informes seguidos de esta instalación.")
    stored = {k: report.get(k) for k in ("server", "size", "models", "features", "polls", "commands") if k in report}
    if inst is None:
        inst = TelemetryInstall(install_id=iid, first_seen=now)
        session.add(inst)
    inst.last_seen, inst.version, inst.commit, inst.report = now, version, commit, stored
    inst.reports = (inst.reports or 0) + 1

    accepted = 0
    for e in (report.get("errors") if isinstance(report.get("errors"), list) else [])[:MAX_REPORT_ERRORS]:
        if not isinstance(e, dict) or not re.fullmatch(r"[0-9a-f]{32}", str(e.get("fingerprint") or "")):
            continue
        count = max(1, _i(e.get("count")))
        frames = [str(f)[:200] for f in (e.get("frames") if isinstance(e.get("frames"), list) else [])[:MAX_FRAMES]]
        group = (await session.execute(select(TelemetryErrorGroup).where(TelemetryErrorGroup.fingerprint == e["fingerprint"]))).scalar_one_or_none()
        if group is None:
            group = TelemetryErrorGroup(fingerprint=e["fingerprint"], kind=_s(e.get("kind"), 10) or "server", level=_s(e.get("level"), 10) or "error",
                                        error_type=_s(e.get("type"), 120), location=_s(e.get("location"), 240), frames=frames,
                                        total_count=0, versions=[], first_seen=now)
            session.add(group)
            await session.flush()
        group.message = scrub(e.get("message")) or group.message          # scrubbed again: never trust the sender
        group.total_count = (group.total_count or 0) + count
        group.last_seen = now
        if version:
            group.versions = ([v for v in (group.versions or []) if v != version] + [version])[-KEEP_VERSIONS:]
        if group.resolved_at is not None and version and version not in (group.versions or [])[:-1]:
            group.resolved_at = None                                       # it came back in a newer version: reopen it
        hit = (await session.execute(select(TelemetryErrorHit).where(TelemetryErrorHit.group_id == group.id,
                                                                     TelemetryErrorHit.install_id == iid))).scalar_one_or_none()
        if hit is None:
            hit = TelemetryErrorHit(group_id=group.id, install_id=iid, count=0)
            session.add(hit)
        hit.count = (hit.count or 0) + count
        hit.version, hit.last_seen = version, now
        accepted += 1

    old = now - RETENTION
    gone = (await session.execute(select(TelemetryInstall.install_id).where(TelemetryInstall.last_seen < old))).scalars().all()
    if gone:
        await session.execute(delete(TelemetryErrorHit).where(TelemetryErrorHit.install_id.in_(gone)))
        await session.execute(delete(TelemetryInstall).where(TelemetryInstall.install_id.in_(gone)))
    await session.commit()
    return {"ok": True, "errors": accepted}


async def resolve_group(session: AsyncSession, group_id: int, resolved: bool) -> bool:
    from app.db.models import TelemetryErrorGroup
    g = await session.get(TelemetryErrorGroup, group_id)
    if g is None:
        return False
    g.resolved_at = _now() if resolved else None
    await session.commit()
    return True


async def summary(session: AsyncSession, now: Optional[datetime.datetime] = None) -> Dict[str, Any]:
    """What the project's console shows: installations, versions, models, features, polls, commands and the error groups."""
    from app.db.models import TelemetryErrorGroup, TelemetryErrorHit, TelemetryInstall
    now = now or _now()
    d7, d30 = now - datetime.timedelta(days=7), now - datetime.timedelta(days=30)
    installs = (await session.execute(select(TelemetryInstall).where(TelemetryInstall.last_seen >= d30))).scalars().all()

    def tally(values) -> List[Dict[str, Any]]:
        out: Dict[str, int] = {}
        for v in values:
            out[v] = out.get(v, 0) + 1
        return [{"value": k, "installs": n} for k, n in sorted(out.items(), key=lambda i: (-i[1], i[0]))]

    features: Dict[str, int] = {}
    models, polls, commands = [], {"ok": 0, "empty": 0, "error": 0}, {}
    for inst in installs:
        rep = inst.report or {}
        for k, v in (rep.get("features") or {}).items():
            if v is True:
                features[str(k)[:40]] = features.get(str(k)[:40], 0) + 1
        for m in rep.get("models") or []:
            if isinstance(m, dict):
                models.append(f"{m.get('model') or '?'} · {m.get('fuel_type') or '?'}")
        p = rep.get("polls") or {}
        for k in polls:
            polls[k] += _i(p.get(k))
        for c in rep.get("commands") or []:
            if not isinstance(c, dict):
                continue
            key = (str(c.get("command") or "?")[:50], str(c.get("model") or "?")[:40])
            item = commands.setdefault(key, {"command": key[0], "model": key[1], "ok": 0, "failed": 0, "codes": {}})
            item["ok"] += _i(c.get("ok"))
            item["failed"] += _i(c.get("failed"))
            for code, n in (c.get("codes") or {}).items():
                item["codes"][str(code)[:20]] = item["codes"].get(str(code)[:20], 0) + _i(n)

    groups = (await session.execute(select(TelemetryErrorGroup).order_by(TelemetryErrorGroup.last_seen.desc()).limit(300))).scalars().all()
    affected = dict((await session.execute(select(TelemetryErrorHit.group_id, func.count()).group_by(TelemetryErrorHit.group_id))).all())
    return {
        "generated_at": now.isoformat(timespec="seconds"),
        "installs": {"active_7d": sum(1 for i in installs if i.last_seen >= d7), "active_30d": len(installs),
                     "total": (await session.execute(select(func.count()).select_from(TelemetryInstall))).scalar_one()},
        "versions": tally(i.version or "?" for i in installs),
        "databases": tally(str((i.report or {}).get("server", {}).get("database") or "?") for i in installs),
        "architectures": tally(str((i.report or {}).get("server", {}).get("arch") or "?") for i in installs),
        "models": tally(models),
        "features": [{"feature": k, "installs": n} for k, n in sorted(features.items(), key=lambda i: (-i[1], i[0]))],
        "polls": polls,
        "commands": sorted(commands.values(), key=lambda i: (-(i["ok"] + i["failed"]), i["command"])),
        "errors": [{"id": g.id, "kind": g.kind, "level": g.level, "type": g.error_type, "message": g.message, "location": g.location,
                    "frames": g.frames or [], "count": g.total_count, "installs": affected.get(g.id, 0), "versions": g.versions or [],
                    "first_seen": g.first_seen.isoformat(timespec="seconds"), "last_seen": g.last_seen.isoformat(timespec="seconds"),
                    "resolved": g.resolved_at is not None} for g in groups],
    }
