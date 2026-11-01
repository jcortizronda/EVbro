#!/usr/bin/env python3
"""Command PIN lockout guard — encapsulated state, with an internal lock.

**The risk this module exists to contain.** The PIN for remote commands
is verified by the Chery backend (`checkPassword`). Every failed verification increments
an error counter ON THE CHERY SIDE: past the threshold the account gets locked — and that
lock cannot be undone from EVBro. That is why the component limits itself: after
`max_fail` consecutive rejections within `window_s` it stops querying the backend and asks the
user to reconfigure the PIN.

Here the lock is not optional: the only way in is `attempt()`, which is a context manager and
serialises the WHOLE ATTEMPT — guard, network call, counter update. There is no
way to use this API that reopens the race condition.

**Note.** The class is instantiable on purpose: with the per-call context each
vehicle/account has its own instance, instead of sharing a process-wide counter (with
two cars configured, one car's errors would block the other's commands).
"""
from __future__ import annotations

import contextlib
import threading
import time


class PinLockedError(Exception):
    """The lockout has tripped: the backend was NOT contacted.

    Distinct from a real backend rejection precisely because no attempt has been spent
    on the Chery side here — it is the protection working, not a new error."""

    def __init__(self, attempts: int, message: str | None = None) -> None:
        self.attempts = attempts
        super().__init__(message or f"PIN bloqueado tras {attempts} intentos erróneos")


class _Intento:
    """Outcome of a single attempt, declared explicitly by the caller.

    The outcome is deliberately NOT inferred from a possible exception: a network error or a
    rejection due to vehicle permissions is not a wrong PIN and must not bring the account
    lockout closer. Whoever mints the taskId decides, case by case, whether the attempt is "the PIN's fault".
    """

    __slots__ = ("_resultado",)

    def __init__(self) -> None:
        self._resultado: str | None = None

    def success(self) -> None:
        """taskId obtained → the counter goes back to zero."""
        self._resultado = "ok"

    def record_failure(self) -> None:
        """The backend rejected AND it is attributable to the PIN → counts towards the lockout."""
        self._resultado = "ko"


class PinLockout:
    """Lockout counter with an internal lock.

    Usage::

        with lockout.attempt() as attempt:        # raises PinLockedError if locked
            response = call_backend()             # serialised: one attempt at a time
            if response.taskid:
                attempt.success()
            elif is_pin_fault(response):
                attempt.record_failure()
            # no declaration = the attempt does not count (network, permissions, session)
    """

    def __init__(self, max_failures: int = 2, window_s: int = 600) -> None:
        self.max_failures = max_failures
        self.window_s = window_s
        # RLock: `attempt()` can call `reset()` without deadlocking itself.
        self._lock = threading.RLock()
        self._n = 0
        self._ts = 0.0

    # ───────────────────────── query ─────────────────────────
    @property
    def failed_attempts(self) -> int:
        with self._lock:
            return self._n

    def is_locked(self) -> bool:
        """True if a new attempt would be rejected without contacting the backend."""
        with self._lock:
            return self._is_locked()

    def _is_locked(self) -> bool:
        """Call with the lock already held."""
        if self._n < self.max_failures:
            return False
        # the window slides: once `window_s` has passed since the last error it resets
        return (time.time() - self._ts) < self.window_s

    # ───────────────────────── mutation ─────────────────────────
    def reset(self) -> None:
        """Reset the lockout.

        Must be called when the user makes an explicit remedial gesture (reconfigures the PIN
        from the Ebro account settings) EVEN IF they re-enter the same PIN: the lockout
        might not have been the PIN's fault, and without a reset the user would be stuck — with no
        signal at all — until the window expires. The state lives in memory only: it is reset when
        the server restarts, not when the screen reloads."""
        with self._lock:
            self._n = 0
            self._ts = 0.0

    @contextlib.contextmanager
    def attempt(self):
        """Serialise an attempt and apply the guard. Raises `PinLockedError` if locked.

        The lock stays held for the whole `with` block — network call
        included. It is intentional and non-negotiable: releasing it before the response would reopen
        exactly the race condition this class exists to close."""
        with self._lock:
            if self._is_locked():
                raise PinLockedError(self._n)
            attempt = _Intento()
            # `finally` is NOT a detail: the caller declares a failure and right after RAISES
            # (CommandError with the remedy for the user). Without `finally` the exception would
            # skip the counter update and the lockout would never trip — that is,
            # the account protection would be silently disabled.
            try:
                yield attempt
            finally:
                if attempt._resultado == "ok":
                    self._n = 0
                    self._ts = 0.0
                elif attempt._resultado == "ko":
                    self._n += 1
                    self._ts = time.time()
