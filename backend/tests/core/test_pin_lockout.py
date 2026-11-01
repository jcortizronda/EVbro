"""Tests for `core/pin_lockout.py` — the command PIN lockout guard.

The module uses `time.time()` for the sliding window, so everything time-related uses the
`freezer` fixture (freezegun), just like in HA core.
"""

from __future__ import annotations

from freezegun.api import FrozenDateTimeFactory
import pytest

from app.core.pin_lockout import PinLockedError, PinLockout


def _falla(lockout: PinLockout) -> None:
    with lockout.attempt() as attempt:
        attempt.record_failure()


def test_starts_unlocked() -> None:
    lockout = PinLockout()
    assert lockout.is_locked() is False
    assert lockout.failed_attempts == 0


def test_locks_after_max_fail() -> None:
    lockout = PinLockout(max_failures=2, window_s=600)

    _falla(lockout)
    assert lockout.failed_attempts == 1
    assert lockout.is_locked() is False

    _falla(lockout)
    assert lockout.failed_attempts == 2
    assert lockout.is_locked() is True


def test_locked_attempt_raises_without_entering_the_block() -> None:
    """When the protection trips the backend is NOT contacted: it is the key difference from a
    real rejection, because no attempt is spent on the Chery side here."""
    lockout = PinLockout(max_failures=1)
    _falla(lockout)

    entrado = False
    with pytest.raises(PinLockedError) as err, lockout.attempt():
        entrado = True

    assert entrado is False
    assert err.value.attempts == 1
    assert "1 intentos" in str(err.value)


def test_success_resets_the_counter() -> None:
    lockout = PinLockout(max_failures=2)
    _falla(lockout)

    with lockout.attempt() as attempt:
        attempt.success()

    assert lockout.failed_attempts == 0
    assert lockout.is_locked() is False


def test_undeclared_attempt_does_not_count() -> None:
    """A network error or a permissions rejection is not a wrong PIN and must not bring the
    account lockout closer. That is why the outcome is declared by hand and not inferred."""
    lockout = PinLockout(max_failures=2)

    with lockout.attempt():
        pass  # no declaration

    assert lockout.failed_attempts == 0


def test_counter_updates_even_if_the_block_raises() -> None:
    """The case that motivates the `finally`.

    The caller declares a failure and right after raises a `CommandError` with the remedy. Without
    `finally`, the exception would skip the counter update and the account protection
    would be silently disabled.
    """
    lockout = PinLockout(max_failures=2)

    with pytest.raises(RuntimeError), lockout.attempt() as attempt:
        attempt.record_failure()
        raise RuntimeError("rechazo del backend")

    assert lockout.failed_attempts == 1


def test_sliding_window_expires(freezer: FrozenDateTimeFactory) -> None:
    """Once `window_s` has passed since the LAST error the lockout lifts on its own."""
    lockout = PinLockout(max_failures=2, window_s=600)
    _falla(lockout)
    _falla(lockout)
    assert lockout.is_locked() is True

    freezer.tick(599)
    assert lockout.is_locked() is True

    freezer.tick(2)  # 601 s > window_s
    assert lockout.is_locked() is False


def test_window_restarts_with_the_next_failure(
    freezer: FrozenDateTimeFactory,
) -> None:
    """It slides, it is not fixed: once the window expires a retry is allowed, and a new failure
    locks again counting from that moment, not from the first one.

    NB: while locked no attempt is possible (`attempt()` raises), so the window restart
    is only observable after it expires.
    """
    lockout = PinLockout(max_failures=2, window_s=600)
    _falla(lockout)
    _falla(lockout)

    freezer.tick(601)  # the window expires → retrying is allowed
    assert lockout.is_locked() is False

    _falla(lockout)  # fails again: locks again, with the clock restarted
    assert lockout.is_locked() is True

    freezer.tick(599)
    assert lockout.is_locked() is True  # 1200 s since the first, only 599 since the last

    freezer.tick(2)
    assert lockout.is_locked() is False


def test_reset_unlocks_immediately() -> None:
    """The user reconfigures the PIN (config flow or Repair) EVEN IF they re-enter the same one: without
    a reset they would be stuck, with no signal, until the window expired."""
    lockout = PinLockout(max_failures=1)
    _falla(lockout)
    assert lockout.is_locked() is True

    lockout.reset()

    assert lockout.is_locked() is False
    assert lockout.failed_attempts == 0


def test_independent_instances() -> None:
    """With two cars configured one car's errors must not block the other's commands:
    that is why the class is instantiable and not a process-wide counter."""
    coche_a = PinLockout(max_failures=1)
    coche_b = PinLockout(max_failures=1)

    _falla(coche_a)

    assert coche_a.is_locked() is True
    assert coche_b.is_locked() is False
