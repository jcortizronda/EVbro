"""Rotate what the first versions of this software derived from their original name: the encryption salt of the secret columns,
the magic header and associated data of the encrypted files, and the passkey user handle. Idempotent.

Every encrypted column is read raw: a value that already opens with the current key is left alone; one that only opens with the
previous key is re-encrypted. The same for every file in the data folders. Passkeys carried the old user handle inside the
authenticator, so they are removed: each device registers again from Ajustes → Cuenta (one tap).

The previous constants are kept here base64-encoded, so the old name does not appear in the source tree.

Revision ID: 0019_evbro_secrets
Revises: 0018_user_language
Create Date: 2026-11-01
"""
from __future__ import annotations

import base64
import logging
import os

from alembic import op
from sqlalchemy import text

revision = "0019_evbro_secrets"
down_revision = "0018_user_language"
branch_labels = None
depends_on = None

_LOGGER = logging.getLogger("app.db.migrations.0019")
_OLD_SALT = base64.b64decode("ZWJyb21hdGUtc2VjcmV0cy12MQ==")
_OLD_AAD_PREFIX = base64.b64decode("ZWJyb21hdGUtZmlsZTo=").decode()
_OLD_MAGIC = base64.b64decode("RUJNRU5DMQo=")

# (table, primary key column, encrypted column): the context of each column is "<table>.<column>" (db/types.py)
COLUMNS = [
    ("catalog_remote", "id", "link"), ("mqtt_links", "id", "password"), ("traccar_links", "vin", "api_token"),
    ("ebro_accounts", "id", "password"), ("ebro_accounts", "id", "pin"), ("ebro_accounts", "id", "token_data"),
    ("notification_configs", "id", "telegram_bot_token"), ("notification_configs", "id", "pushover_user_key"),
    ("notification_configs", "id", "pushover_api_token"), ("notification_configs", "id", "webhook_url"),
    ("push_subscriptions", "id", "subscription"),
]


def _rotate_columns(conn, old_key: bytes) -> int:
    from app.security import crypto
    rotated = 0
    for table, pk, column in COLUMNS:
        rows = conn.execute(text(f"SELECT {pk}, {column} FROM {table} WHERE {column} IS NOT NULL")).all()
        context = f"{table}.{column}"
        for key, value in rows:
            if not crypto.is_encrypted(value):
                continue
            try:
                crypto.decrypt(value, context)                 # already readable with the current key
                continue
            except crypto.DecryptionError:
                pass
            try:
                plain = crypto.decrypt(value, context, key=old_key)
            except crypto.DecryptionError:
                _LOGGER.warning("%s %s=%s: unreadable with either key, left as it is", context, pk, key)
                continue
            conn.execute(text(f"UPDATE {table} SET {column} = :v WHERE {pk} = :k"), {"v": crypto.encrypt(plain, context), "k": key})
            rotated += 1
    return rotated


def _rotate_files(old_key: bytes) -> int:
    from app.services import uploads
    rotated = 0
    for folder in (uploads.DOCUMENTS_FOLDER, uploads.INVOICES_FOLDER, uploads.PHOTOS_FOLDER):
        root = uploads.folder_path(folder)
        if not os.path.isdir(root):
            continue
        for name in os.listdir(root):
            path = os.path.join(root, name)
            if not os.path.isfile(path):
                continue
            with open(path, "rb") as fh:
                blob = fh.read()
            if not blob.startswith(_OLD_MAGIC):
                continue                                           # new format, or legacy plaintext (handled elsewhere)
            try:
                plain = uploads.decrypt_bytes(folder, name, blob, key=old_key, aad_prefix=_OLD_AAD_PREFIX, magic=_OLD_MAGIC)
            except Exception as exc:                                # noqa: BLE001 — never break the start because of one file
                _LOGGER.warning("%s/%s: unreadable with the previous key (%s), left as it is", folder, name, exc)
                continue
            uploads.write_atomic(path, uploads.encrypt_bytes(folder, name, plain))
            rotated += 1
    return rotated


def upgrade() -> None:
    from app.security import crypto
    conn = op.get_bind()
    try:
        old_key = crypto.derive_with_salt(_OLD_SALT)
    except Exception as exc:                                        # noqa: BLE001 — no key configured yet (fresh install): nothing to rotate
        _LOGGER.info("No encryption key available (%s): nothing to rotate.", exc)
        old_key = None
    if old_key is not None:
        cols = _rotate_columns(conn, old_key)
        files = _rotate_files(old_key)
        if cols or files:
            _LOGGER.info("Re-encrypted %d column values and %d files with the current key.", cols, files)
    removed = conn.execute(text("DELETE FROM passkey_credentials")).rowcount
    if removed:
        _LOGGER.warning("%d passkey(s) removed: their user handle changed; register the devices again from Ajustes → Cuenta.", removed)


def downgrade() -> None:
    pass
