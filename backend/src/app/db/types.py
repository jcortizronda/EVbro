"""Tipos de columna que cifran el valor al guardarlo y lo descifran al leerlo.

El resto del código sigue trabajando con texto o dicts normales: `account.pin` devuelve el PIN
en claro y guardarlo lo cifra. En la base de datos solo hay `enc:v1:...` (ver security/crypto.py).
"""
from __future__ import annotations

import json
import logging

from sqlalchemy import Text
from sqlalchemy.types import TypeDecorator

from app.security import crypto

_LOGGER = logging.getLogger("app.db.types")


class EncryptedText(TypeDecorator):
    """Texto cifrado. `context` = «tabla.columna», va como dato autenticado del cifrado."""

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
            # Con la clave equivocada se devuelve «sin valor» en vez de romper TODA consulta
            # que toque la tabla: el usuario verá que debe volver a introducir el dato.
            _LOGGER.error("No se pudo descifrar %s: ¿ha cambiado ENCRYPTION_KEY o secret.key?", self.context)
            return None


class EncryptedJSON(EncryptedText):
    """JSON (dict/list) cifrado. Acepta también el JSON en claro anterior al cifrado."""

    cache_ok = True

    def process_bind_param(self, value, dialect):
        if value is None:
            return None
        return crypto.encrypt(json.dumps(value, ensure_ascii=False), self.context)

    def process_result_value(self, value, dialect):
        if isinstance(value, (dict, list)):      # el driver ya lo había interpretado (JSON heredado)
            return value
        text = super().process_result_value(value, dialect)
        if text is None:
            return None
        try:
            return json.loads(text)
        except ValueError:
            _LOGGER.error("%s no contiene JSON válido tras descifrar", self.context)
            return None
