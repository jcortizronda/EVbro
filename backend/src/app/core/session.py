#!/usr/bin/env python3
"""
session.py — Ebro token health (session keep-alive).

The token that makes the command buttons work lives in token.json (wake.TOKEN_PATH).
It can "drop" in two ways:
  1) the access_token expires normally      -> refresh() renews it with the refresh_token;
  2) the official app is opened             -> the session is invalidated and not even refresh is enough
                                               -> the user must authenticate again (password).

This module exposes the session-health primitives EVBro uses:
  - check()   -> (ok, detail, status)  : is the token valid? (tries a BFF login)
  - refresh() -> bool                  : renews the access_token (keep-alive)
"""
import time

# package-relative import
from . import wake  # reuses _bff_login / _refresh_token / TOKEN_PATH

# This package's folder: used by the reduced diagnostic contexts.

# STABLE markers of check()'s result. The caller routes the remedy on these,
# NEVER on the human text (which is localised and may change with every copy tweak).
STATUS_OK = "OK"                # BFF login correct with the current token
STATUS_EXPIRED = "EXPIRED"      # dead token/session → re-authentication needed
STATUS_NET_ERROR = "NET_ERROR"  # network/transient error → NOT an expired session

# How long the reason of the last renewal stays reliable for deciding the remedy.
# The renewal and the session check happen in the same pass (fractions of a second): a
# reason older than this refers to another attempt and is ignored.
_REASON_FRESH_S = 60.0
# Fraction of the token's lifetime after which it is renewed ahead of time (0.8 = at 9h36m of 12h):
# early enough to have margin, late enough not to waste renewals.
RENEWAL_QUOTA = 0.8


def check(ctx):
    """Return (ok: bool, detail: str, status: str).

    `status` is the stable marker (STATUS_OK/EXPIRED/NET_ERROR): it is what the
    poller and the interface use to decide whether to ask for another login. `detail` is for the user only.
    Telling EXPIRED from NET_ERROR is essential: a network outage must NOT ask for
    another login (the user would do it for nothing)."""
    try:
        ut, _tu = wake._bff_login(ctx)
    except Exception as e:
        return False, f"error de red: {type(e).__name__}", STATUS_NET_ERROR
    if ut:
        return True, "Sesión activa ✅", STATUS_OK
    # The login failed. But if it failed because the RENEWAL did not even start (network down,
    # timeout, DNS), the session may well still be alive on the other side: declaring it
    # expired would ask for another login for no reason.
    # The marker is trusted only if it is RECENT: an old one refers to another pass.
    reason = ctx.state.refresh_reason or ""
    fresh = (time.time() - (ctx.state.refresh_ts or 0.0)) < _REASON_FRESH_S
    if fresh and reason.startswith("red:"):
        return False, f"renovación fallida por red ({reason[4:]})", STATUS_NET_ERROR
    return (False,
            "Sesión caducada ❌ — vuelve a iniciar sesión con tu cuenta Ebro",
            STATUS_EXPIRED)


def refresh(ctx):
    """Renew the access_token with the refresh_token. True if it was renewed."""
    try:
        return bool(wake._refresh_token(ctx))
    except Exception:
        return False


def refresh_if_expiring(ctx, quota: float = RENEWAL_QUOTA) -> tuple[bool, str]:
    """PROACTIVE renewal: renew when the access token has used up `quota` of its lifetime,
    instead of waiting until it is already dead.

    Why reactive renewal is not enough: the session check runs every 15 minutes, and the
    reactive one only kicks in AFTER the token expires — that is, up to a quarter of an hour
    late, with the refresh_token window already closing. Renewing ahead of time always does so with
    the session still alive; and if the renewal fails it is found out while there is still time,
    instead of with the session already dead.

    Returns (renewed, reason). `(False, "no_hace_falta")` = not due, no call made.
    Never raises: it is an optimisation, it must not be able to break the keep-alive."""
    try:
        eta, lifetime = wake._eta_token(ctx)
        if eta < 0 or lifetime <= 0:
            return False, "no_determinable"
        if eta < lifetime * quota:
            return False, "no_hace_falta"
        return wake._refresh_token_detail(ctx)
    except Exception as e:
        return False, f"red:{type(e).__name__}"
