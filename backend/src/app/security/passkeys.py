"""Passkeys (WebAuthn): relying party, single-use challenges and labels.

* The relying party ID is the hostname the browser used (the `Host` header, which the reverse proxy forwards), or `WEBAUTHN_RP_ID`.
  The expected origin is `https://host[:port]` (and `http://` for `localhost`, the only plain-HTTP origin where browsers allow
  WebAuthn), or the list in `WEBAUTHN_ORIGIN`.
* Challenges live in memory (one process), are bound to a purpose ("register"/"login"), to the account that asked for them (when
  registering) and to the relying party, expire after `CHALLENGE_TTL_S` and are removed the first time anybody tries to use them.
"""
from __future__ import annotations

import secrets
import time
from collections import OrderedDict
from dataclasses import dataclass
from typing import List, Optional, Tuple
from urllib.parse import urlparse

from app.config import settings

RP_NAME = "EVBro"
CHALLENGE_TTL_S = 300
_MAX_PENDING = 5000                    # bound memory if somebody floods the options endpoints


@dataclass(frozen=True)
class PendingChallenge:
    challenge: bytes
    purpose: str
    user_id: Optional[int]
    rp_id: str
    origins: Tuple[str, ...]
    expires: float


class ChallengeStore:
    def __init__(self, ttl_s: int = CHALLENGE_TTL_S) -> None:
        self.ttl_s = ttl_s
        self._items: "OrderedDict[str, PendingChallenge]" = OrderedDict()

    def _purge(self, now: float) -> None:
        for key in [k for k, v in self._items.items() if v.expires <= now]:
            self._items.pop(key, None)
        while len(self._items) >= _MAX_PENDING:
            self._items.popitem(last=False)

    def issue(self, purpose: str, rp_id: str, origins: List[str], user_id: Optional[int] = None) -> Tuple[str, bytes]:
        """Returns (challenge_id, challenge)."""
        now = time.monotonic()
        self._purge(now)
        challenge_id = secrets.token_urlsafe(24)
        challenge = secrets.token_bytes(32)
        self._items[challenge_id] = PendingChallenge(challenge, purpose, user_id, rp_id, tuple(origins), now + self.ttl_s)
        return challenge_id, challenge

    def consume(self, challenge_id: str, purpose: str, user_id: Optional[int] = None) -> Optional[PendingChallenge]:
        """The challenge, removed for good, if it exists, is not expired and matches the purpose and account. Otherwise None."""
        item = self._items.pop(challenge_id or "", None)
        if item is None or item.expires <= time.monotonic() or item.purpose != purpose or item.user_id != user_id:
            return None
        return item

    def clear(self) -> None:
        self._items.clear()


challenges = ChallengeStore()


def relying_party(request) -> Tuple[str, List[str]]:
    """(RP ID, accepted origins) for this request."""
    host = (request.headers.get("host") or "").strip()
    hostname = urlparse("//" + host).hostname or ""
    rp_id = (settings.WEBAUTHN_RP_ID or "").strip() or hostname
    configured = [o.strip().rstrip("/") for o in (settings.WEBAUTHN_ORIGIN or "").split(",") if o.strip()]
    if configured:
        return rp_id, configured
    origins = [f"https://{host}"]
    if hostname == "localhost":
        origins.append(f"http://{host}")
    return rp_id, origins


def user_handle(user_id: int) -> bytes:
    """The opaque user handle stored in the passkey (no personal data)."""
    return f"ebromate-{user_id}".encode()      # historical: the handle is stored in the registered authenticators


def label_from_user_agent(ua: str) -> str:
    ua = ua or ""
    os_name = ("Android" if "Android" in ua else "iOS" if ("iPhone" in ua or "iPad" in ua) else "Windows" if "Windows" in ua
               else "macOS" if "Mac" in ua else "Linux" if "Linux" in ua else "dispositivo")
    browser = ("Edge" if "Edg/" in ua else "Firefox" if "Firefox" in ua else "Chrome" if "Chrome" in ua
               else "Safari" if "Safari" in ua else "Navegador")
    return f"{browser} en {os_name}"
