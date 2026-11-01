#!/usr/bin/env python3
"""The SINGLE routing table for backend codes.

**What this module decides.** The Chery backend always answers HTTP 200 and puts the
real result in a `code` (`A00xxx`). Four independent decisions depend on that
code:

1. did the command succeed?
2. which **remedy** to offer the user (`reason`) — and therefore which action the
   interface offers: log in again, reconfigure the PIN, or just a notice;
3. should the error **count** towards the PIN lockout?
4. is it worth **re-minting the taskId** and retrying?

Having them spread around had a measurable cost: the wake fallback classified as
«wrong PIN» rejections that were about permissions or the session, so it showed the wrong
remedy and — worse — brought the real account lockout closer for a cause that had nothing
to do with the PIN.

**Division of labour.** The DECISIONS live here; the readable TEXTS stay in `codes.py`.
It is the project rule: never decide on a localised string, because
translating or rewording a message must not be able to disable re-authentication.

**Context matters.** The same code can mean different things depending on where it
comes from: `A00567` during `checkPassword` is a malformed request (not the PIN), but
in response to a command it means «invalid taskId» → it is re-minted and retried. That is
why `classify()` always asks for the context.

**The default is deliberately asymmetric.** An unknown code in
`checkPassword` falls into the PIN branch (conservative: better to suggest checking the PIN than to leave
the user without a remedy), while in response to a command it stays non-blocking — we do not
invent a failure the backend has not declared.
"""
from __future__ import annotations

from dataclasses import dataclass, replace

# ───────────────────────── remedies (`reason`) ─────────────────────────
# STABLE values: the interface routes on these, never on the message text.
REASON_PIN = "pin"        # wrong command PIN → ask to reconfigure the PIN
REASON_REAUTH = "reauth"  # dead session/token → log in again with the Ebro account
REASON_CONFIG = "config"  # neither PIN nor session (permissions, malformed request) → notice only
REASON_NONE = None     # rejection by the car (busy / not allowed / asleep) → notice

# ───────────────────────── interface actions ─────────────────────────
ACTION_REAUTH = "reauth"          # asks to log in again with the Ebro account
ACTION_REPAIR_PIN = "repair_pin"  # asks to reconfigure the command PIN
ACTION_NOTICE = "aviso"           # no automatic remedy: just shown

_ACTION_BY_REASON = {
    REASON_REAUTH: ACTION_REAUTH,
    REASON_PIN: ACTION_REPAIR_PIN,
    REASON_CONFIG: ACTION_NOTICE,
    REASON_NONE: ACTION_NOTICE,
}


def action_for_reason(reason: str | None) -> str:
    """Remedy → concrete interface action.

    An unknown `reason` degrades to a notice: never to an invasive action such as forcing
    another login or asking to change a PIN that is not the problem."""
    return _ACTION_BY_REASON.get(reason, ACTION_NOTICE)


# ───────────────────────── contexts ─────────────────────────
CONTEXT_CHECKPASSWORD = "checkpassword"  # taskId minting (PIN verification)
CONTEXT_COMMAND = "comando"              # sending a command to the car


@dataclass(frozen=True)
class Classification:
    """What to do with a code, in a context."""

    code: str | None = None
    reason: str | None = REASON_NONE
    # send outcome: "ok" accepted, "ko" rejected, "unknown" = the backend did not express
    # itself recognisably → non-blocking, out of caution.
    outcome: str = "unknown"
    retryable: bool = False            # retrying as-is makes sense (car busy)
    regenerate_taskid: bool = False       # the taskId must be redone: re-mint and retry ONCE
    counts_for_lockout: bool = False        # increments the PIN lockout

    @property
    def action(self) -> str:
        return action_for_reason(self.reason)

    @property
    def success(self) -> bool:
        return self.outcome == "ok"

    @property
    def failed(self) -> bool:
        return self.outcome == "ko"


def _entry(**kw) -> Classification:
    return Classification(**kw)


# ───────────────────────── the table ─────────────────────────
# Valid in BOTH contexts unless explicitly overridden below.
_TABLE: dict[str, Classification] = {
    # — accepted —
    "000000": _entry(outcome="ok"),
    "A00079": _entry(outcome="ok"),

    # — rejections by the car: no automatic remedy, notice only —
    # the car runs ONE command at a time → transient, retryable
    "A00082": _entry(outcome="ko", retryable=True),
    # permission denied for THAT function (seen live with remoteStart)
    "A00084": _entry(outcome="ko"),
    "A07312": _entry(outcome="ko"),   # wake rate limit
    "A07900": _entry(outcome="ko"),   # car asleep / invalid signature or car_token

    # — taskId to redo: re-minted and retried once —
    "A00089": _entry(outcome="ko", regenerate_taskid=True),
    "A00546": _entry(outcome="ko", regenerate_taskid=True),
    "A00567": _entry(outcome="ko", regenerate_taskid=True),

    # — dead session: the only remedy is re-authenticating (the PIN is irrelevant) —
    "A00000": _entry(outcome="ko", reason=REASON_REAUTH),

    # — not the PIN: vehicle permissions or a badly built request —
    # they do NOT count towards the lockout: counting them would bring the real account lockout closer
    # for a cause that has nothing to do with the PIN (bug P1-2).
    "A00374": _entry(reason=REASON_CONFIG),   # vehicle permissions
    "A00554": _entry(reason=REASON_CONFIG),   # vehicle authorisation
    "A00604": _entry(reason=REASON_CONFIG),   # clientType missing/wrong
    "A00643": _entry(reason=REASON_CONFIG),   # taskId missing from the request
    "A00757": _entry(reason=REASON_CONFIG),   # malformed request
}

# Per-context override. `A00567` is the case to watch: in `checkPassword` it is an
# incomplete request (the PIN may be correct), in response to a command it is a taskId
# to redo. Same code, two different remedies.
_OVERRIDE_CHECKPASSWORD: dict[str, Classification] = {
    "A00567": _entry(reason=REASON_CONFIG),
    # wrong PIN/password: the only ones that should really count towards the lockout.
    "A00285": _entry(reason=REASON_PIN, counts_for_lockout=True),
    "A00282": _entry(reason=REASON_PIN, counts_for_lockout=True),
    # A07908/A07909: wrong PIN, CONFIRMED in the field on 2026-10-01 — the user remembered the right
    # PIN and the command went through first time, on the same account and with the same error code
    # it had been returning all morning.
    #
    # For a few hours they were classified as «not the PIN» on reasoning that looked
    # rock-solid: the app accepted the PIN that failed for us. What was missed is that the
    # app was accepting the NEW PIN of another account. The deduction was flawless and the premise
    # false, and the price of being wrong here is not paid by the software — it is paid by the
    # user's account, because without `counts_for_lockout` the lockout stops braking a genuinely
    # wrong PIN and Chery ends up locking the account.
    #
    # That is why they are explicit and do not rely on the default rule: so it is visible that they are a
    # measurement and not an assumption.
    "A07908": _entry(reason=REASON_PIN, counts_for_lockout=True),
    "A07909": _entry(reason=REASON_PIN, counts_for_lockout=True),
}

# Never-seen code. The asymmetry is intentional — see the module docstring.
_DEFAULT_CHECKPASSWORD = _entry(reason=REASON_PIN, counts_for_lockout=True)
_DEFAULT_COMMAND = _entry(outcome="unknown")


def classify(code, context: str) -> Classification:
    """Backend code → what to do, in the given context.

    `code` may be `None` or a non-string (unreadable response): it is normalised.
    """
    key = str(code) if code is not None else ""

    if context == CONTEXT_CHECKPASSWORD:
        entry_ = _OVERRIDE_CHECKPASSWORD.get(key) or _TABLE.get(key)
        if entry_ is None:
            entry_ = _DEFAULT_CHECKPASSWORD
    else:
        entry_ = _TABLE.get(key, _DEFAULT_COMMAND)

    return replace(entry_, code=key or None)


# ───────────────────────── derived views (compatibility) ─────────────────────────
# Sets derived FROM the table, not hand-written next to it: they used to be parallel lists
# that could silently drift from how the codes were routed.
SUCCESS_CODES = frozenset(c for c, v in _TABLE.items() if v.outcome == "ok")
FAILURE_CODES = frozenset(c for c, v in _TABLE.items() if v.outcome == "ko")
RETRYABLE_CODES = frozenset(c for c, v in _TABLE.items() if v.retryable)
TASKID_INVALID = frozenset(c for c, v in _TABLE.items() if v.regenerate_taskid)
