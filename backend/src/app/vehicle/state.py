"""The car's live state, and the lock that protects it.

**Why it exists.** Three different threads touch this data: **paho**'s, which delivers the
MQTT messages; the **executor**, where the probe and the commands run; and the asyncio **event
loop**, from which the API reads. They used to be four private attributes of the
coordinator (`_fields`, `position`, `_last_msg_ts`, `_state_lock`) and every access repeated
`with self._state_lock:` by hand. Forgetting it once is enough to get a half-written read, and
it is the kind of bug that does not show up in tests: it shows up at night, in the user's car.

Here the lock is not optional. There is no way to read or write without going through a method
that already takes it, and everything that comes out is a **copy**: if the live dict were
returned, the paho thread could be mutating it while the server serialises it.
"""
from __future__ import annotations

import threading
import time
from typing import Any


class VehicleState:
    """5A02 telemetry, last position and "when the car last spoke".

    `awake_window` = how many seconds without receiving anything to wait before considering the
    car asleep.
    """

    def __init__(self, awake_window: float) -> None:
        self._lock = threading.Lock()
        self._awake_window = awake_window
        self._fields: dict[str, str] = {}
        self._position: dict[str, Any] | None = None
        self._last_msg_ts: float = 0.0

    # ───────────────────────── writing ─────────────────────────
    def record_message(self, fields: dict[str, str], now: float) -> tuple[dict[str, str], bool]:
        """Apply the fields of a car message. Return `(copy, was_awake)`.

        Both things are done under the SAME lock on purpose: `was_awake` is measured against the
        PREVIOUS instant, so reading it separately would open a window in which another
        message would already have moved the timestamp and the wake-up edge would be lost.
        """
        with self._lock:
            was_awake = self._is_awake_locked(now)
            self._last_msg_ts = now
            self._fields.update(fields)
            return dict(self._fields), was_awake

    def set_position(self, geo: dict[str, Any]) -> dict[str, Any]:
        """Replace the position (1301 push). Return a copy."""
        with self._lock:
            self._position = dict(geo)
            return dict(self._position)

    def merge_position(self, geo: dict[str, Any]) -> dict[str, Any]:
        """Merge a partial position over the known one (realtime probe). Return a copy.

        The probe may bring only some geographic fields, and losing the rest would leave the
        device_tracker worse than it was.
        """
        with self._lock:
            self._position = {**(self._position or {}), **geo}
            return dict(self._position)

    # ───────────────────────── reading ─────────────────────────
    def fields(self) -> dict[str, str]:
        """Copy of the 5A02 telemetry."""
        with self._lock:
            return dict(self._fields)

    def field(self, key: str) -> str | None:
        """One telemetry field, without copying the whole dict."""
        with self._lock:
            return self._fields.get(key)

    @property
    def position(self) -> dict[str, Any] | None:
        """Copy of the last known position, or `None`."""
        with self._lock:
            return dict(self._position) if self._position is not None else None

    def _is_awake_locked(self, now: float) -> bool:
        return bool(self._last_msg_ts) and (now - self._last_msg_ts) < self._awake_window

    def is_awake(self, now: float | None = None) -> bool:
        """The car is publishing NOW.

        Measured by the time elapsed since the last message, not by a flag: a flag has to be
        remembered to be cleared, and when that was forgotten the «Despertar coche» button
        answered "already awake" and sent nothing for days.
        """
        now = time.time() if now is None else now
        with self._lock:
            return self._is_awake_locked(now)

    # ─────────────── seeding for tests and restore ───────────────
    def seed(self, *, fields: dict[str, str] | None = None,
             position: dict[str, Any] | None = None, last_msg_ts: float | None = None) -> None:
        """Set the state in one go. The test harness uses it to simulate a live car."""
        with self._lock:
            if fields is not None:
                self._fields = dict(fields)
            if position is not None:
                self._position = dict(position)
            if last_msg_ts is not None:
                self._last_msg_ts = last_msg_ts
