"""The online catalogue: one EVBro server publishes its approved catalogue, other installations mirror it and send proposals.

Two roles, both in every installation:

* **Online server** (the owner's): issues *catalogue keys* from its console (`create_key`; only the hash is stored and the key is shown
  once as a link `https://server#key`), serves its approved entries to a valid key (`/api/catalog/online/export`) and receives
  proposals from other installations into its own review queue (`/api/catalog/online/propose`, `receive_proposal`).
* **Self-hosted server**: its administrator chooses in the console whether the catalogue is its own or online (`CatalogRemote`, one row).
  With a link, `sync` downloads the approved entries once a day and keeps them as a read-only mirror (`catalog_entries.origin = remote`),
  and every proposal of its users is also forwarded (`maybe_forward`). Without a valid link, nothing changes: the server keeps working
  with its own catalogue and the console shows the error.

The HTTP calls go through `_http`, a small function the tests replace to talk to the application in process.
"""
from __future__ import annotations

import datetime
import logging
import secrets
from typing import Any, Dict, List, Optional, Tuple

import aiohttp
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.db.models import AppSetting, CatalogEntry, CatalogFitment, CatalogKey, CatalogRemote
from app.security.auth import hash_token
from app.services import catalog
from app.services.parts import CATEGORY_BY_KEY, POSITIONS

_LOGGER = logging.getLogger(__name__)

KEY_PREFIX = "ebc_"
PUBLIC_URL_SETTING = "catalog_public_url"
EXPORT_PATH = "/api/catalog/online/export"
PROPOSE_PATH = "/api/catalog/online/propose"
SOURCES = ("own", "remote")
TIMEOUT_S = 30
MAX_PROPOSALS_PER_KEY_DAY = 200


def _now() -> datetime.datetime:
    return datetime.datetime.utcnow()


class RemoteError(Exception):
    def __init__(self, status_code: int, detail: str):
        super().__init__(detail)
        self.status_code, self.detail = status_code, detail


# ───────────────────────── online server: keys ─────────────────────────
def key_state(k: CatalogKey, now: Optional[datetime.datetime] = None) -> str:
    now = now or _now()
    if k.revoked:
        return "revoked"
    if k.expires_at and k.expires_at <= now:
        return "expired"
    return "active"


def make_link(public_url: str, key: str) -> str:
    return f"{public_url.rstrip('/')}#{key}"


def parse_link(link: str) -> Tuple[str, str]:
    """`https://server#key` → (base url, key). A bare key belongs to `CATALOG_DEFAULT_URL`. Raises RemoteError otherwise."""
    text = (link or "").strip()
    if text.startswith(KEY_PREFIX) and "#" not in text:
        default = (settings.CATALOG_DEFAULT_URL or "").strip().rstrip("/")
        if not default:
            raise RemoteError(422, "Este servidor no tiene catálogo en línea por defecto: pega el enlace completo (https://servidor#clave).")
        text = f"{default}#{text}"
    url, _, key = text.partition("#")
    url, key = url.strip().rstrip("/"), key.strip()
    if not url.startswith(("http://", "https://")) or not key.startswith(KEY_PREFIX) or len(key) < 20:
        raise RemoteError(422, "Pega la clave del catálogo (empieza por ebc_) o el enlace completo (https://servidor#clave).")
    return url, key


def default_server() -> Optional[str]:
    return (settings.CATALOG_DEFAULT_URL or "").strip().rstrip("/") or None


def serialize_key(k: CatalogKey) -> Dict[str, Any]:
    return {"id": k.id, "note": k.note, "state": key_state(k), "created_at": k.created_at.isoformat() if k.created_at else None,
            "expires_at": k.expires_at.isoformat() if k.expires_at else None, "last_used_at": k.last_used_at.isoformat() if k.last_used_at else None,
            "downloads": k.downloads or 0, "proposals": k.proposals or 0}


async def get_public_url(session: AsyncSession) -> Optional[str]:
    row = await session.get(AppSetting, PUBLIC_URL_SETTING)
    return (row.value or "").strip() or None if row else None


async def set_public_url(session: AsyncSession, url: Optional[str]) -> Optional[str]:
    value = (url or "").strip().rstrip("/")
    if value and not value.startswith(("http://", "https://")):
        raise RemoteError(422, "La URL pública tiene que empezar por https:// (o http://).")
    row = await session.get(AppSetting, PUBLIC_URL_SETTING)
    if row is None:
        session.add(AppSetting(key=PUBLIC_URL_SETTING, value=value or None))
    else:
        row.value = value or None
    await session.commit()
    return value or None


async def list_keys(session: AsyncSession) -> List[Dict[str, Any]]:
    rows = (await session.execute(select(CatalogKey).order_by(CatalogKey.created_at.desc()))).scalars().all()
    return [serialize_key(k) for k in rows]


async def create_key(session: AsyncSession, admin_id: Optional[int], note: Optional[str], days: Optional[int]) -> Tuple[CatalogKey, str, Optional[str]]:
    """New key for one installation. Returns the row, the plain key (shown once) and the link when the public URL is set."""
    key = KEY_PREFIX + secrets.token_urlsafe(24)
    row = CatalogKey(key_hash=hash_token(key), note=(note or "").strip()[:120] or None, created_by=admin_id,
                     expires_at=(_now() + datetime.timedelta(days=days)) if days else None)
    session.add(row)
    await session.commit()
    await session.refresh(row)
    public = await get_public_url(session)
    return row, key, make_link(public, key) if public else None


async def revoke_key(session: AsyncSession, key_id: int) -> bool:
    row = await session.get(CatalogKey, key_id)
    if row is None:
        return False
    row.revoked = True
    await session.commit()
    return True


async def authenticate(session: AsyncSession, bearer: Optional[str]) -> Optional[CatalogKey]:
    """The key behind an `Authorization: Bearer ebc_…` header, if it is active."""
    token = (bearer or "").strip()
    if token.lower().startswith("bearer "):
        token = token[7:].strip()
    if not token.startswith(KEY_PREFIX):
        return None
    row = (await session.execute(select(CatalogKey).where(CatalogKey.key_hash == hash_token(token)))).scalar_one_or_none()
    return row if row is not None and key_state(row) == "active" else None


async def receive_proposal(session: AsyncSession, key: CatalogKey, data: Dict[str, Any]) -> CatalogEntry:
    """A proposal sent by another installation enters this server's queue, tagged with the key's note (never with a person).

    Notes are dropped: an older installation may still send its user's private notes with the proposal."""
    category = str(data.get("category") or "")
    if category not in CATEGORY_BY_KEY:
        raise RemoteError(422, "Categoría desconocida.")
    fits = [f for f in (data.get("fitments") or []) if isinstance(f, dict) and (f.get("model") or "").strip()][:catalog.MAX_FITMENTS]
    if not fits:
        raise RemoteError(422, "La propuesta necesita al menos un modelo de coche.")
    if not (data.get("brand") or data.get("reference") or data.get("spec") or any((f.get("oem_reference") or "").strip() for f in fits)):
        raise RemoteError(422, "Una propuesta necesita al menos la marca, una referencia o la especificación de la pieza.")
    since = _now() - datetime.timedelta(days=1)
    recent = (await session.execute(select(CatalogEntry.id).where(CatalogEntry.remote_note == (key.note or f"clave {key.id}"),
                                                                 CatalogEntry.proposed_at >= since))).scalars().all()
    if len(recent) >= MAX_PROPOSALS_PER_KEY_DAY:
        raise RemoteError(429, "Demasiadas propuestas desde esta instalación hoy.")
    text = lambda k, n: (str(data.get(k) or "").strip()[:n] or None)  # noqa: E731 — short field cleaner
    position = text("position", 10)
    first = fits[0]
    entry = CatalogEntry(model=str(first.get("model")).strip()[:80], make=(str(first.get("make") or "").strip()[:50] or None),
                         years=(str(first.get("years") or "").strip()[:20] or None), oem_reference=(str(first.get("oem_reference") or "").strip()[:120] or None), category=category,
                         position=position if position in CATEGORY_BY_KEY[category].get("positions", []) else None, name=text("name", 150) or CATEGORY_BY_KEY[category].get("label", category),
                         brand=text("brand", 80), reference=text("reference", 120), spec=text("spec", 120),
                         quantity=float(data["quantity"]) if isinstance(data.get("quantity"), (int, float)) and 0 <= data["quantity"] <= 100000 else None,
                         notes=None, source=data.get("source") if data.get("source") in ("manual", "invoice", "photo") else "manual",
                         status="pending", proposed_by=None, proposed_at=_now(), origin="local", remote_note=key.note or f"clave {key.id}")
    session.add(entry)
    await session.flush()
    for f in fits:
        session.add(CatalogFitment(entry_id=entry.id, make=(str(f.get("make") or "").strip()[:50] or None), model=str(f.get("model")).strip()[:80],
                                   years=(str(f.get("years") or "").strip()[:20] or None), oem_reference=(str(f.get("oem_reference") or "").strip()[:120] or None)))
    key.proposals = (key.proposals or 0) + 1
    key.last_used_at = _now()
    await session.commit()
    return entry


async def mark_download(session: AsyncSession, key: CatalogKey) -> None:
    key.downloads = (key.downloads or 0) + 1
    key.last_used_at = _now()
    await session.commit()


# ───────────────────────── self-hosted server: source and mirror ─────────────────────────
async def get_config(session: AsyncSession) -> CatalogRemote:
    row = await session.get(CatalogRemote, 1)
    if row is None:
        row = CatalogRemote(id=1, source="own")
        session.add(row)
        await session.commit()
        await session.refresh(row)
    return row


def serialize_config(c: CatalogRemote) -> Dict[str, Any]:
    url = None
    if c.link:
        try:
            url, _ = parse_link(c.link)
        except RemoteError:
            url = None
    return {"source": c.source, "has_link": bool(c.link), "server": url, "default_server": default_server(), "last_sync_at": c.last_sync_at.isoformat() if c.last_sync_at else None,
            "last_error": c.last_error, "entries": c.entries or 0}


async def set_config(session: AsyncSession, source: str, link: Optional[str]) -> CatalogRemote:
    """`link` None keeps the stored one; "" removes it."""
    if source not in SOURCES:
        raise RemoteError(422, "Origen no válido: own o remote.")
    c = await get_config(session)
    if link is not None:
        if link.strip():
            url, key = parse_link(link)                        # validates; a bare key is stored with the default server
            c.link = make_link(url, key)
        else:
            c.link = None
    if source == "remote" and not c.link:
        raise RemoteError(422, "Para usar el catálogo en línea pega la clave que te hayan dado (ebc_…) o el enlace completo.")
    c.source = source
    c.last_error = None
    await session.commit()
    return c


async def _http(method: str, url: str, key: str, payload: Optional[Dict[str, Any]] = None) -> Tuple[int, Any]:
    """One request to the online server. Replaced in tests."""
    timeout = aiohttp.ClientTimeout(total=TIMEOUT_S)
    async with aiohttp.ClientSession(timeout=timeout, headers={"Authorization": f"Bearer {key}"}) as s:
        async with s.request(method, url, json=payload) as r:
            try:
                body = await r.json(content_type=None)
            except Exception:                                   # noqa: BLE001 — a proxy error page, for example
                body = None
            return r.status, body


async def _clear_mirror(session: AsyncSession) -> None:
    ids = (await session.execute(select(CatalogEntry.id).where(CatalogEntry.origin == "remote"))).scalars().all()
    if ids:
        await session.execute(delete(CatalogFitment).where(CatalogFitment.entry_id.in_(ids)))     # SQLite does not cascade
        await session.execute(delete(CatalogEntry).where(CatalogEntry.id.in_(ids)))


async def sync(session: AsyncSession) -> Dict[str, Any]:
    """Downloads the online catalogue and replaces the mirror. Records the outcome in the config row either way."""
    c = await get_config(session)
    if not c.link:
        raise RemoteError(409, "No hay enlace del catálogo en línea.")
    url, key = parse_link(c.link)
    try:
        status, body = await _http("GET", url + EXPORT_PATH, key)
    except Exception as ex:                                     # noqa: BLE001 — network errors are reported, not raised
        c.last_error = f"No se pudo conectar: {str(ex)[:180]}"
        await session.commit()
        raise RemoteError(502, c.last_error) from ex
    if status == 401:
        c.last_error = "La clave no es válida o ha caducado."
    elif status != 200 or not isinstance(body, dict) or body.get("format") != "evbro-catalog/2":
        c.last_error = f"Respuesta inesperada del servidor ({status})."
    else:
        c.last_error = None
    if c.last_error:
        await session.commit()
        raise RemoteError(502 if status != 401 else 401, c.last_error)

    await _clear_mirror(session)
    count = 0
    for d in body.get("entries") or []:
        if not isinstance(d, dict) or d.get("category") not in CATEGORY_BY_KEY:
            continue
        fits = [f for f in (d.get("fitments") or []) if isinstance(f, dict) and (f.get("model") or "").strip()] or [{"model": d.get("model")}]
        if not (fits[0].get("model") or "").strip():
            continue
        e = CatalogEntry(model=str(fits[0]["model"])[:80], make=(str(fits[0].get("make") or "")[:50] or None), years=(str(fits[0].get("years") or "")[:20] or None),
                         oem_reference=(str(fits[0].get("oem_reference") or "")[:120] or None),
                         category=d["category"], position=d.get("position") if d.get("position") in POSITIONS else None,
                         name=str(d.get("name") or CATEGORY_BY_KEY[d["category"]].get("label", d["category"]))[:150],
                         brand=(str(d.get("brand") or "")[:80] or None), reference=(str(d.get("reference") or "")[:120] or None),
                         spec=(str(d.get("spec") or "")[:120] or None),
                         quantity=float(d["quantity"]) if isinstance(d.get("quantity"), (int, float)) else None,
                         notes=(str(d.get("notes") or "")[:2000] or None), source=d.get("source") if d.get("source") in ("manual", "invoice", "photo") else "manual",
                         status="approved", confirmations=int(d.get("confirmations") or 0), reviewed_at=_now(),
                         origin="remote", remote_id=d.get("id") if isinstance(d.get("id"), int) else None)
        session.add(e)
        await session.flush()
        for f in fits[:catalog.MAX_FITMENTS]:
            session.add(CatalogFitment(entry_id=e.id, make=(str(f.get("make") or "")[:50] or None), model=str(f["model"])[:80], years=(str(f.get("years") or "")[:20] or None),
                                       oem_reference=(str(f.get("oem_reference") or "")[:120] or None)))
        count += 1
    c.entries, c.last_sync_at, c.last_error = count, _now(), None
    await session.commit()
    _LOGGER.info(f"Online catalogue synced: {count} entries from {url}")
    return {"entries": count, "server": url}


async def sync_if_enabled(session: AsyncSession) -> None:
    c = await get_config(session)
    if c.source == "remote" and c.link:
        try:
            await sync(session)
        except RemoteError as ex:
            _LOGGER.warning(f"Online catalogue: {ex.detail}")


async def maybe_forward(session: AsyncSession, entry: CatalogEntry) -> None:
    """In online mode a new local proposal is also sent to the online server's queue; a failure never breaks the local proposal."""
    c = await get_config(session)
    if c.source != "remote" or not c.link:
        return
    try:
        url, key = parse_link(c.link)
        payload = {k: v for k, v in (await catalog.serialize_one(session, entry)).items()
                   if k in ("category", "position", "name", "brand", "reference", "oem_reference", "spec", "quantity", "source", "fitments")}
        status, _ = await _http("POST", url + PROPOSE_PATH, key, payload)
        if status == 200:
            entry.forwarded_at = _now()
            await session.commit()
        else:
            _LOGGER.warning(f"The online catalogue rejected proposal {entry.id} ({status}).")
    except Exception as ex:                                     # noqa: BLE001 — forwarding is best effort
        _LOGGER.warning(f"Could not send proposal {entry.id} to the online catalogue: {ex}")
