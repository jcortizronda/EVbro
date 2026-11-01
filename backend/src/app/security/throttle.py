"""Limit on failed login attempts (in memory).

Sliding window per key: if a key piles up `max_failures` failures within `window_s`, it stays
blocked until the oldest failure leaves the window. It is in memory on purpose: restarting the
server resets it, but an attacker does not control restarts.
"""
from __future__ import annotations

import time
from collections import deque


class LoginThrottle:
    def __init__(self, max_failures: int, window_s: int = 900) -> None:
        self.max_failures = max_failures
        self.window_s = window_s
        self._fails: dict[str, deque[float]] = {}

    def _recent(self, key: str, now: float) -> deque[float]:
        q = self._fails.get(key)
        if q is None:
            return deque()
        while q and now - q[0] >= self.window_s:
            q.popleft()
        if not q:
            self._fails.pop(key, None)
        return q

    def retry_after(self, key: str) -> int:
        """Seconds until a retry is allowed (0 = allowed)."""
        now = time.monotonic()
        q = self._recent(key, now)
        if len(q) < self.max_failures:
            return 0
        return max(1, int(q[0] + self.window_s - now) + 1)

    def record_failure(self, key: str) -> None:
        now = time.monotonic()
        self._recent(key, now)
        self._fails.setdefault(key, deque()).append(now)
        if len(self._fails) > 10_000:                       # keeps it from growing without bound under attack
            for k in list(self._fails):
                self._recent(k, now)

    def reset(self, key: str) -> None:
        self._fails.pop(key, None)
