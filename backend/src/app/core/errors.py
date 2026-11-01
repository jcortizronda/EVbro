#!/usr/bin/env python3
"""The error of a rejected command.

It lives in its own module because both sides of the path share it: `taskid`, which fails
when the PIN is wrong, and `commands`, which fails when the car rejects the send. Keeping it in one
of the two created a circular import — and, more fundamentally, would have suggested the error belongs to
one of the two stages when it describes the outcome of the whole operation.
"""
from . import routing

# Retrying only makes sense for the codes the table marks as transient (car
# busy). Derived from `routing`, not a parallel list.
RETRYABLE_CODES = routing.RETRYABLE_CODES


class CommandError(Exception):
    """Command rejected by the backend/car (NOT executed). `code` = tspconsole code,
    `retryable` = True if retrying makes sense (e.g. car busy). It is let through up to
    the commands API, which returns the real error (and its `reason`) to the interface instead of a
    false success.

    `reason` routes the REMEDY in the interface (routing by cause, not only by code):
      - "pin"    = wrong command PIN / lockout / missing PIN → reconfigure the PIN
                   (Ebro account settings). It is NOT a session problem: the token is
                   valid and telemetry keeps arriving.
      - "reauth" = session/token expired (failed login, code A00000) → log in again
                   with the Ebro account. That does NOT change the PIN: the two channels are distinct.
      - "config" = rejection NOT attributable to the PIN or the session (vehicle permissions, malformed
                   request, taskId minting disabled): no automatic remedy,
                   just a notice. Does not ask to change the PIN and does not count towards the lockout.
      - None     = another rejection by the car (busy, not allowed, asleep): just a notice."""

    def __init__(self, message: str, code: str | None = None, reason: str | None = None) -> None:
        super().__init__(message)
        self.code = code
        self.reason = reason
        self.retryable = code in RETRYABLE_CODES
