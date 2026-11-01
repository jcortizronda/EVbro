"""Password hashing with scrypt (standard library, no extra dependencies).

Stored format: `scrypt$N$r$p$salt_b64$hash_b64`. The parameters live inside the hash, so they
can be raised in the future without invalidating existing hashes.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import os

N = 2 ** 15          # CPU/memory cost: ~32 MB and ~100 ms per check
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
    """True if `password` matches the stored hash. Never raises: an unreadable hash means "no"."""
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
    """Takes the same time as a real check.

    Called when the user does NOT exist: if that case answered instantly, timing the
    response would reveal which users exist."""
    global _dummy_hash
    if _dummy_hash is None:
        _dummy_hash = hash_password(os.urandom(8).hex())
    verify_password(password, _dummy_hash)


def password_problem(password: str, username: str = "") -> str | None:
    """Reason why the password is not valid, or None if it is acceptable."""
    if len(password) < MIN_LENGTH:
        return f"La contraseña debe tener al menos {MIN_LENGTH} caracteres."
    if len(password) > MAX_LENGTH:
        return f"La contraseña no puede superar los {MAX_LENGTH} caracteres."
    if len(set(password)) < 5:
        return "La contraseña es demasiado repetitiva."
    if username and password.lower() == username.lower():
        return "La contraseña no puede ser igual que el usuario."
    return None
