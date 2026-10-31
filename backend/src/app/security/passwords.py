"""Hash de contraseñas con scrypt (librería estándar, sin dependencias extra).

Formato guardado: `scrypt$N$r$p$salt_b64$hash_b64`. Los parámetros van dentro del hash, así se
pueden subir en el futuro sin invalidar los existentes.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import os

N = 2 ** 15          # coste de CPU/memoria: ~32 MB y ~100 ms por comprobación
R = 8
P = 1
_MAXMEM = 128 * 1024 * 1024
_DKLEN = 32

MIN_LENGTH = 10
MAX_LENGTH = 128


def _derive(password: str, salt: bytes, n: int, r: int, p: int) -> bytes:
    return hashlib.scrypt(password.encode("utf-8"), salt=salt, n=n, r=r, p=p, maxmem=_MAXMEM, dklen=_DKLEN)


def hash_password(password: str) -> str:
    salt = os.urandom(16)
    digest = _derive(password, salt, N, R, P)
    return "scrypt${}${}${}${}${}".format(
        N, R, P, base64.b64encode(salt).decode(), base64.b64encode(digest).decode())


def verify_password(password: str, stored: str | None) -> bool:
    """True si `password` corresponde al hash guardado. Nunca lanza: un hash ilegible es «no»."""
    try:
        scheme, n, r, p, salt_b64, hash_b64 = (stored or "").split("$")
        if scheme != "scrypt":
            return False
        expected = base64.b64decode(hash_b64)
        candidate = _derive(password, base64.b64decode(salt_b64), int(n), int(r), int(p))
        return hmac.compare_digest(candidate, expected)
    except Exception:
        return False


_dummy_hash: str | None = None


def burn_time(password: str) -> None:
    """Gasta el mismo tiempo que una comprobación real.

    Se llama cuando el usuario NO existe: si ese caso respondiera al instante, medir el tiempo
    de respuesta revelaría qué usuarios existen."""
    global _dummy_hash
    if _dummy_hash is None:
        _dummy_hash = hash_password(os.urandom(8).hex())
    verify_password(password, _dummy_hash)


def password_problem(password: str, username: str = "") -> str | None:
    """Motivo por el que la contraseña no vale, o None si es aceptable."""
    if len(password) < MIN_LENGTH:
        return f"La contraseña debe tener al menos {MIN_LENGTH} caracteres."
    if len(password) > MAX_LENGTH:
        return f"La contraseña no puede superar los {MAX_LENGTH} caracteres."
    if len(set(password)) < 5:
        return "La contraseña es demasiado repetitiva."
    if username and password.lower() == username.lower():
        return "La contraseña no puede ser igual que el usuario."
    return None
