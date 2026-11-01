"""Column types that encrypt the value on write and decrypt it on read.

The rest of the code keeps working with plain text or dicts: `account.pin` returns the PIN in
clear and saving it encrypts it. The database only holds `enc:v1:...` (see security/crypto.py).
"""
from __future__ import annotations

import json
import logging

from sqlalchemy import Text
from sqlalchemy.types import TypeDecorator

from app.security import crypto

_LOGGER = logging.getLogger("app.db.types")


class EncryptedText(TypeDecorator):
    """Encrypted text. `context` = "table.column", used as the encryption's authenticated data."""

    impl = Text
    cache_ok = True

    def __init__(self, context: str) -> None:
        super().__init__()
        self.context = context

    def process_bind_param(self, value, dialect):
        if value is None:
            return None
        return crypto.encrypt(str(value), self.context)

    def process_result_value(self, value, dialect):
        if value is None:
            return None
        try:
            return crypto.decrypt(value, self.context)
        except crypto.DecryptionError:
            # with the wrong key, "no value" is returned instead of breaking EVERY query
            # that touches the table: the user will see they have to enter the data again.
            _LOGGER.error("Could not decrypt %s: has ENCRYPTION_KEY or secret.key changed?", self.context)
            return None


class EncryptedJSON(EncryptedText):
    """Encrypted JSON (dict/list). Also accepts plain JSON from before encryption."""

    cache_ok = True

    def process_bind_param(self, value, dialect):
        if value is None:
            return None
        return crypto.encrypt(json.dumps(value, ensure_ascii=False), self.context)

    def process_result_value(self, value, dialect):
        if isinstance(value, (dict, list)):      # the driver had already parsed it (legacy JSON)
            return value
        text = super().process_result_value(value, dialect)
        if text is None:
            return None
        try:
            return json.loads(text)
        except ValueError:
            _LOGGER.error("%s does not hold valid JSON after decrypting", self.context)
            return None
