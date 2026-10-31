"""Claves VAPID del servidor para Web Push (en Android, Chrome entrega los avisos vía Google FCM).

La clave privada vive en `DATA_DIR/vapid_private.pem` (0600, volumen de datos, junto a `secret.key`)
y se genera sola la primera vez. **Si se pierde, hay que volver a activar las notificaciones en
cada dispositivo**: las suscripciones quedan ligadas a la clave pública.
"""
from __future__ import annotations

import base64
import logging
import os
import threading

from cryptography.hazmat.primitives import serialization
from py_vapid import Vapid02

from app.config import get_data_dir

_LOGGER = logging.getLogger("app.security.vapid")

KEY_FILENAME = "vapid_private.pem"
_lock = threading.Lock()
_vapid: Vapid02 | None = None


def _key_path() -> str:
    return os.path.join(get_data_dir(), KEY_FILENAME)


def get_vapid() -> Vapid02:
    """Carga (o genera) el par de claves. Se cachea en memoria."""
    global _vapid
    with _lock:
        if _vapid is None:
            path = _key_path()
            if os.path.exists(path):
                _vapid = Vapid02.from_file(path)
            else:
                os.makedirs(os.path.dirname(path), exist_ok=True)
                v = Vapid02()
                v.generate_keys()
                fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
                with os.fdopen(fd, "wb") as fh:
                    fh.write(v.private_pem())
                _LOGGER.info("Generada la clave VAPID para Web Push en %s", path)
                _vapid = v
        return _vapid


def public_key_b64() -> str:
    """Clave pública en el formato que espera `pushManager.subscribe` (base64url sin relleno)."""
    raw = get_vapid().public_key.public_bytes(
        serialization.Encoding.X962, serialization.PublicFormat.UncompressedPoint
    )
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode()


def reset_cache() -> None:
    """Solo para los tests."""
    global _vapid
    with _lock:
        _vapid = None
