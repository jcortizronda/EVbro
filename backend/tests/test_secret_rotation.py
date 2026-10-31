"""Migration 0019 re-encrypts the secrets and files written with the previous salt and associated data, and drops old passkeys."""
from __future__ import annotations

import asyncio
import base64
import os

from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

from app.db import migrate
from app.security import crypto
from app.services import uploads

OLD_SALT = base64.b64decode("ZWJyb21hdGUtc2VjcmV0cy12MQ==")
OLD_AAD = base64.b64decode("ZWJyb21hdGUtZmlsZTo=").decode()
OLD_MAGIC = base64.b64decode("RUJNRU5DMQo=")


def _old_encrypt_file(folder: str, name: str, data: bytes, key: bytes) -> bytes:
    from Crypto.Cipher import AES
    nonce = os.urandom(12)
    cipher = AES.new(key, AES.MODE_GCM, nonce=nonce, mac_len=16)
    cipher.update(f"{OLD_AAD}{folder}/{name}".encode())
    ciphertext, tag = cipher.encrypt_and_digest(data)
    return OLD_MAGIC + nonce + tag + ciphertext


async def test_0019_reencrypts_old_secrets_and_files_and_removes_old_passkeys(tmp_path, db) -> None:
    url = f"sqlite+aiosqlite:///{tmp_path}/rot.db"
    from alembic import command
    await asyncio.to_thread(command.upgrade, migrate.alembic_config(url), "0018_user_language")
    old_key = crypto.derive_with_salt(OLD_SALT)
    old_value = crypto.encrypt("mi-pin-1234", "ebro_accounts.pin", key=old_key)
    current_value = crypto.encrypt("ya-nuevo", "ebro_accounts.password")
    engine = create_async_engine(url)
    try:
        async with engine.begin() as conn:
            await conn.execute(text("INSERT INTO app_users (id, username, is_admin, password_hash, role, is_active, read_only, vehicle_limit, member_limit, pin_policy, language) "
                                    "VALUES (1, 'juan', 0, 'x', 'manager', 1, 0, -1, 0, 'open', 'es')"))
            await conn.execute(text("INSERT INTO ebro_accounts (id, user_id, phone, area_code, password, pin, is_active) VALUES (1, 1, '600', '34', :p, :pin, 1)"),
                               {"p": current_value, "pin": old_value})
            await conn.execute(text("INSERT INTO passkey_credentials (id, user_id, credential_id, public_key, sign_count, created_at) "
                                    "VALUES (1, 1, 'cred', 'pk', 0, '2026-10-01 00:00:00')"))
        # a document encrypted the old way
        folder = uploads.folder_path(uploads.DOCUMENTS_FOLDER)
        os.makedirs(folder, exist_ok=True)
        with open(os.path.join(folder, "old.pdf"), "wb") as fh:
            fh.write(_old_encrypt_file(uploads.DOCUMENTS_FOLDER, "old.pdf", b"%PDF-1.4 hola", old_key))
        await migrate.upgrade_to_head(url)
        async with engine.connect() as conn:
            pin, password = (await conn.execute(text("SELECT pin, password FROM ebro_accounts WHERE id = 1"))).one()
            passkeys = (await conn.execute(text("SELECT count(*) FROM passkey_credentials"))).scalar_one()
    finally:
        await engine.dispose()
    assert crypto.decrypt(pin, "ebro_accounts.pin") == "mi-pin-1234"                  # rotated to the current key
    assert password == current_value                                                   # untouched: it already opened
    assert uploads.load(uploads.DOCUMENTS_FOLDER, "old.pdf") == b"%PDF-1.4 hola"       # file rewritten with the new header
    with open(os.path.join(folder, "old.pdf"), "rb") as fh:
        assert fh.read().startswith(uploads.MAGIC)
    assert passkeys == 0
