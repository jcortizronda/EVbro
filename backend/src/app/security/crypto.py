"""Cifrado de secretos en reposo con AES-256-GCM.

Qué protege: la contraseña y el PIN de la cuenta Ebro, sus tokens y los datos de los canales de
notificación, que viven en la base de datos. Un volcado de PostgreSQL (copia de seguridad, disco
robado, inyección SQL) no basta para leerlos: falta la clave, que vive fuera de la BD.

Dónde está la clave, por orden de preferencia:
  1. `ENCRYPTION_KEY` (variable de entorno): la clave está fuera de TODOS los volúmenes.
  2. `DATA_DIR/secret.key` (se genera sola la primera vez, permisos 0600): en el volumen de
     datos, que es distinto del de PostgreSQL. Protege frente a un volcado de la BD, pero no
     frente a quien lea también el volumen de datos.

Formato: `enc:v1:` + base64url(nonce[12] + tag[16] + cifrado). El `context` (p. ej.
`ebro_accounts.pin`) va como dato autenticado: un valor cifrado para un campo no se puede
copiar a otro. Un valor sin el prefijo se considera texto plano heredado (anterior al cifrado)
y se devuelve tal cual hasta que la migración de arranque lo cifra.
"""
from __future__ import annotations

import base64
import logging
import os
import secrets
import threading

from Crypto.Cipher import AES
from Crypto.Hash import SHA256
from Crypto.Protocol.KDF import HKDF

from app.config import get_data_dir, settings

_LOGGER = logging.getLogger("app.security.crypto")

PREFIX = "enc:v1:"
KEY_FILENAME = "secret.key"
MIN_KEY_CHARS = 16
_NONCE_LEN = 12
_TAG_LEN = 16

_key: bytes | None = None
_key_lock = threading.Lock()


class DecryptionError(Exception):
    """El valor no se pudo descifrar: clave distinta a la que lo cifró, o dato corrupto."""


def _derive(master: str) -> bytes:
    # Historical salt: never change it, every stored secret would become unreadable.
    return HKDF(master.encode("utf-8"), 32, b"ebromate-secrets-v1", SHA256, context=b"field-encryption")


def _read_or_create_key_file(path: str) -> str:
    os.makedirs(os.path.dirname(path), mode=0o700, exist_ok=True)
    try:
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except FileExistsError:
        with open(path, encoding="utf-8") as fh:
            return fh.read().strip()
    with os.fdopen(fd, "w", encoding="utf-8") as fh:
        value = secrets.token_urlsafe(48)
        fh.write(value)
    _LOGGER.warning(
        "Clave de cifrado generada en %s. Haz copia de este archivo (o define ENCRYPTION_KEY): "
        "sin ella no se podrán leer las credenciales guardadas.", path)
    return value


def get_key() -> bytes:
    global _key
    if _key is not None:
        return _key
    with _key_lock:
        if _key is None:
            env = (settings.ENCRYPTION_KEY or "").strip()
            if env:
                if len(env) < MIN_KEY_CHARS:
                    raise RuntimeError(
                        f"ENCRYPTION_KEY demasiado corta (mínimo {MIN_KEY_CHARS} caracteres). "
                        "Genera una con: openssl rand -base64 32")
                _key = _derive(env)
            else:
                file_value = _read_or_create_key_file(os.path.join(get_data_dir(), KEY_FILENAME))
                if not file_value:
                    raise RuntimeError(f"El archivo {KEY_FILENAME} está vacío; bórralo para generar uno nuevo.")
                _key = _derive(file_value)
    return _key


def reset_key_cache() -> None:
    """Olvida la clave en memoria (los tests cambian de configuración entre casos)."""
    global _key
    with _key_lock:
        _key = None


def is_encrypted(value: str) -> bool:
    return isinstance(value, str) and value.startswith(PREFIX)


def encrypt(plaintext: str, context: str = "") -> str:
    nonce = os.urandom(_NONCE_LEN)
    cipher = AES.new(get_key(), AES.MODE_GCM, nonce=nonce, mac_len=_TAG_LEN)
    cipher.update(context.encode("utf-8"))
    ciphertext, tag = cipher.encrypt_and_digest(plaintext.encode("utf-8"))
    return PREFIX + base64.urlsafe_b64encode(nonce + tag + ciphertext).decode("ascii")


def decrypt(value: str, context: str = "") -> str:
    """Descifra `value`. Si no lleva el prefijo es texto plano heredado y se devuelve igual."""
    if not is_encrypted(value):
        return value
    try:
        raw = base64.urlsafe_b64decode(value[len(PREFIX):].encode("ascii"))
        nonce, tag, ciphertext = raw[:_NONCE_LEN], raw[_NONCE_LEN:_NONCE_LEN + _TAG_LEN], raw[_NONCE_LEN + _TAG_LEN:]
        cipher = AES.new(get_key(), AES.MODE_GCM, nonce=nonce, mac_len=_TAG_LEN)
        cipher.update(context.encode("utf-8"))
        return cipher.decrypt_and_verify(ciphertext, tag).decode("utf-8")
    except (ValueError, KeyError) as exc:
        raise DecryptionError(f"No se pudo descifrar el campo '{context}'") from exc
