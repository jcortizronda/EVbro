"""Startup migration: encrypts the secrets still stored as plain text in the DB.

The encrypted columns are found automatically by walking the models (every one of type
`EncryptedText`/`EncryptedJSON`), so adding a new secret field does not require touching this.
It is idempotent: whatever already starts with `enc:v1:` is left alone.
"""
from __future__ import annotations

import json
import logging

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection

from app.db.models import Base
from app.db.types import EncryptedJSON, EncryptedText
from app.security import crypto

_LOGGER = logging.getLogger("app.db.secrets_migration")


def encrypted_columns():
    """[(table, pk_column, [encrypted columns])] according to the models."""
    out = []
    for table in Base.metadata.sorted_tables:
        cols = [c for c in table.columns if isinstance(c.type, (EncryptedText, EncryptedJSON))]
        if cols:
            pk = list(table.primary_key.columns)[0]
            out.append((table, pk, cols))
    return out


async def widen_columns_to_text(conn: AsyncConnection) -> None:
    """PostgreSQL: the columns were VARCHAR(n)/JSON and the encrypted value is longer."""
    if conn.dialect.name != "postgresql":
        return
    for table, _pk, cols in encrypted_columns():
        for col in cols:
            using = f' USING "{col.name}"::text'
            await conn.execute(text(f'ALTER TABLE "{table.name}" ALTER COLUMN "{col.name}" TYPE TEXT{using}'))


async def encrypt_legacy_secrets(conn: AsyncConnection) -> int:
    """Encrypts the plain-text values. Returns how many fields it encrypted."""
    done = 0
    for table, pk, cols in encrypted_columns():
        names = ", ".join(f'"{c.name}"' for c in [pk, *cols])
        rows = (await conn.execute(text(f'SELECT {names} FROM "{table.name}"'))).mappings().all()
        for row in rows:
            changes = {}
            for col in cols:
                raw = row[col.name]
                if raw is None or crypto.is_encrypted(raw):
                    continue
                plain = raw if isinstance(raw, str) else json.dumps(raw, ensure_ascii=False)
                changes[col.name] = crypto.encrypt(plain, col.type.context)
            if changes:
                sets = ", ".join(f'"{k}" = :{k}' for k in changes)
                await conn.execute(
                    text(f'UPDATE "{table.name}" SET {sets} WHERE "{pk.name}" = :_pk'),
                    {**changes, "_pk": row[pk.name]},
                )
                done += len(changes)
    if done:
        _LOGGER.info("Encrypted %d secret fields that were in plain text.", done)
    return done
