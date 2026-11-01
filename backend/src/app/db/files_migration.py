"""Startup migration, once: encrypts the uploaded files that are still in plaintext (documents, maintenance invoices, car photos).

Safe and idempotent: a file that already has the magic header is skipped; each file is encrypted into a temporary file in the same folder,
that temporary file is read back and decrypted, and only when it matches the original is it renamed over it. A file that cannot be read
or verified is left as it was (never deleted) and the migration is retried on the next start. It also fills in `Vehicle.photo_size` for
photos uploaded before the size was stored. When everything went well the flag `migration.files_encrypted` is written to `app_settings`.
"""
from __future__ import annotations

import asyncio
import logging
import os
import uuid
from typing import Dict

from sqlalchemy import select, text

from app.config import get_data_dir
from app.db.models import Vehicle
from app.services import uploads

_LOGGER = logging.getLogger("app.db.files_migration")

FLAG = "migration.files_encrypted"


def _encrypt_one(folder: str, folder_dir: str, name: str) -> str:
    """'encrypted', 'skipped' or 'failed' for one file."""
    path = os.path.join(folder_dir, name)
    try:
        with open(path, "rb") as fh:
            original = fh.read()
    except OSError as exc:
        _LOGGER.error("Could not read %s/%s to encrypt it: %s", folder, name, exc)
        return "failed"
    if uploads.is_encrypted(original):
        return "skipped"
    tmp = os.path.join(folder_dir, f"{uploads.TEMP_PREFIX}{uuid.uuid4().hex}")
    try:
        with open(tmp, "wb") as fh:
            fh.write(uploads.encrypt_bytes(folder, name, original))
            fh.flush()
            os.fsync(fh.fileno())
        with open(tmp, "rb") as fh:
            if uploads.decrypt_bytes(folder, name, fh.read()) != original:
                raise ValueError("the encrypted file does not match the original")
        os.replace(tmp, path)
        return "encrypted"
    except Exception as exc:  # noqa: BLE001 — one bad file must not stop the others; the original stays untouched
        _LOGGER.error("Could not encrypt %s/%s (left as it was): %s", folder, name, exc)
        try:
            os.remove(tmp)
        except OSError:
            pass
        return "failed"


def encrypt_folders() -> Dict[str, int]:
    """Encrypts every plaintext file of the upload folders. Returns the counts."""
    counts = {"encrypted": 0, "skipped": 0, "failed": 0}
    for folder in uploads.FOLDERS:
        folder_dir = os.path.join(get_data_dir(), folder)
        if not os.path.isdir(folder_dir):
            continue
        for name in sorted(os.listdir(folder_dir)):
            if name.startswith(".") or not os.path.isfile(os.path.join(folder_dir, name)):
                continue                       # leftovers of an interrupted write (".tmp-…") and anything hidden are not uploads
            counts[_encrypt_one(folder, folder_dir, name)] += 1
    return counts


async def _fill_photo_sizes(session) -> int:
    filled = 0
    rows = (await session.execute(select(Vehicle).where(Vehicle.photo_filename.is_not(None), Vehicle.photo_size.is_(None)))).scalars().all()
    for vehicle in rows:
        try:
            data = await asyncio.to_thread(uploads.load, uploads.PHOTOS_FOLDER, vehicle.photo_filename)
        except Exception:  # noqa: BLE001 — a missing or unreadable photo just stays without a size
            continue
        vehicle.photo_size = len(data)
        filled += 1
    await session.commit()
    return filled


async def encrypt_legacy_files() -> Dict[str, int]:
    """Runs the migration unless its flag is already set. Returns the counts (empty when it had already run)."""
    from app.db.session import async_session_factory, engine

    async with engine.connect() as conn:
        if (await conn.execute(text("SELECT 1 FROM app_settings WHERE key = :k"), {"k": FLAG})).first():
            return {}
    counts = await asyncio.to_thread(encrypt_folders)
    async with async_session_factory() as session:
        counts["photo_sizes"] = await _fill_photo_sizes(session)
    if counts["failed"]:
        _LOGGER.error("Uploaded files: %s encrypted, %s already were, %s failed (will retry on the next start).",
                      counts["encrypted"], counts["skipped"], counts["failed"])
        return counts
    async with engine.begin() as conn:
        await conn.execute(text("INSERT INTO app_settings (key, value) VALUES (:k, '1')"), {"k": FLAG})
    _LOGGER.info("Uploaded files encrypted at rest: %s encrypted now, %s already were; size computed for %s photos.",
                 counts["encrypted"], counts["skipped"], counts["photo_sizes"])
    return counts
