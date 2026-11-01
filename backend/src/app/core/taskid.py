#!/usr/bin/env python3
"""The taskId: how it is obtained, and why asking for it needs care.

No command goes out without a `taskId` validated by `checkPassword`, and `checkPassword` verifies
the command PIN against Chery's backend. **Every failed verification increments an
error counter on the Chery side**, and past the threshold the account gets locked — a lock
that cannot be undone from EVBro. Hence this module does three things before
asking:

1. if the PIN is empty, it fails BEFORE touching the backend: an unconfigured PIN is not a
   wrong attempt and must not use up the threshold;
2. it reuses the cached taskId while it is still alive, because minting it is the slow part of every
   command — so most of them turn into a single signed POST;
3. it serialises the whole attempt (guard + network call + counter update) inside
   `PinLockout.attempt()`, so the race condition cannot be expressed.

It used to be half of `commands.py`, mixed with the catalogue and the sending. It has its own file
here because it is what deserves the most care in the whole component.
"""
import hashlib
import logging
import os
import re
import time

from . import ebro_auth as A, routing, wake
from .errors import CommandError
from .http import bff_post
from .pin_lockout import PinLockedError

_LOGGER = logging.getLogger(__name__)

# lockout: stops after N consecutive failed checkPassword calls within a window, so as not to
# trip the PIN lock of the Chery ACCOUNT (every wrong PIN increments the errors on
# their side, and that lock cannot be undone from EVBro).
#
# The state and its lock now live inside `PinLockout` (core/pin_lockout.py): the only way
# to mint is `attempt()`, which serialises guard + POST + counter update.
# The counter lives in `ctx.lockout` — one per vehicle. Otherwise, with two cars
# configured, one car's PIN errors would block the other's commands.
#
# OVERLAPPING mints are counted in `ctx.state` (see context.py): it is per-vehicle
# state, not per-process.
# checkPassword's classification BY CODE now lives in `routing.py` (single table). It used
# to be «anything that does not yield a taskId = wrong PIN», which is false and does two kinds of harm: it offers the
# user the wrong remedy and counts towards the lockout an error that has nothing to do
# with the PIN.


def reset_pin_lockout(ctx) -> None:
    """Reset the wrong-PIN lockout counter (and the cached taskId).

    The state lives in memory and is only reset when the server restarts, so without
    this call commands would stay blocked until the window expires after fixing the
    PIN. Invoked on every PIN reconfiguration (Ebro account settings)."""
    ctx.reset_pin_lockout()


def _mint_taskid(ctx, tuid):
    """Mint a taskId. With the monitor off it is a straight pass-through to `_mint_taskid_impl`.

    With the monitor on it observes TWO things the log does not show: concurrent
    mints (`pin_fail_concurrent`, the race that brings the account lockout closer) and the
    real checkPassword outcome with the backend's RAW code — the only way to
    tell a genuinely wrong PIN from a rejection due to permissions/parameters. The PIN is never
    logged, in any form."""
    if ctx.diag_hook is None:
        return _mint_taskid_impl(ctx, tuid)
    with ctx.state.mint_inflight_lock:
        ctx.state.mint_inflight += 1
        inflight = ctx.state.mint_inflight
    if inflight > 1:
        ctx.diag("pin_fail_concurrent", inflight=inflight)
    try:
        tid = _mint_taskid_impl(ctx, tuid)
        ctx.diag("pin_event", outcome="ok", pin_fail_n=ctx.lockout.failed_attempts)
        return tid
    except CommandError as err:
        # "fail" = the backend answered and rejected; "empty" = PIN not configured;
        # "blocked" = lockout tripped. The last two have NOT queried the backend:
        # telling them apart matters, because only "fail" brings the account lockout closer. It is classified
        # by the message marker, not by the presence of a code: a rejection can also arrive
        # without a code, and would end up confused with a lockout.
        code = getattr(err, "code", None)
        if "checkPassword" in str(err):
            outcome = "fail"
        elif not (ctx.pin or "").strip():
            outcome = "empty"
        else:
            outcome = "blocked"
        ctx.diag("pin_event", outcome=outcome, reason=getattr(err, "reason", None), cp_code=code,
                 pin_fail_n=ctx.lockout.failed_attempts,
                 pin_fail_max=ctx.lockout.max_failures)
        raise
    except Exception as err:
        ctx.diag("pin_event", outcome="error", err_type=type(err).__name__)
        raise
    finally:
        with ctx.state.mint_inflight_lock:
            ctx.state.mint_inflight -= 1


def _mint_taskid_impl(ctx, tuid):
    """Mint a taskId with the app's BFF chain (queryList→setVecDefault→checkPassword).
       FIX (2026-06-20): scene=0 (NOT 2) → the minted taskId is accepted by tspconsole
       (airControl A00079). scene=2 gave A00089; scene=1 A00089; scene>=3 A00546.

       Refuses to mint if the PIN is empty (does NOT call checkPassword with nothing) and
       locks itself after too many consecutive wrong PINs to avoid the account lockout.

       On failure it ALWAYS raises CommandError with a `reason` ("pin" or "reauth") so that
       the interface can route the right remedy (reconfigure PIN vs re-authenticate)."""
    # Missing PIN: fail BEFORE entering the lockout. An unconfigured PIN is not a
    # wrong attempt — it must not use up the threshold or touch the backend.
    if not (ctx.pin or "").strip():
        raise CommandError(
            "PIN de comandos no configurado — configúralo en los ajustes de la cuenta Ebro",
            reason="pin")

    # `attempt()` takes the lock, applies the guard and holds it for the whole network call.
    # It is the only way to mint: the race condition can no longer be expressed.
    try:
        with ctx.lockout.attempt() as attempt:
            return _checkpassword(ctx, tuid, attempt)
    except PinLockedError as locked:
        raise CommandError(
            f"PIN de comandos bloqueado temporalmente ({locked.attempts} intentos erróneos) — "
            "vuelve a configurar el PIN en los ajustes de la cuenta Ebro y reinténtalo",
            reason="pin") from None


def _checkpassword(ctx, tuid, attempt):
    """The BFF chain proper. Runs with the lockout lock already held; declares
    the outcome on `attempt` ONLY when it is really attributable to the PIN."""
    from . import vehicles

    access = wake._access_token(ctx)
    extra = {"Authorization": f"Bearer {access}",
             "Content-Type": "application/json; charset=UTF-8",
             "Accept": "application/json, text/plain, */*"}

    def bff(path, body):
        return bff_post(ctx, path, body, headers=A.headers_post(path, extra=extra, ctx=ctx))

    # The app's exact chain: list the vehicles, set ours as the default and
    # only then verify the PIN. Skipping the first two steps makes checkPassword
    # answer without a taskId.
    vehicles.query_list(ctx)
    bff("/tsp/v1/app/vmc/setVecDefault", {"vin": ctx.vin})
    plain = hashlib.md5(ctx.pin.encode()).hexdigest()
    password = A.sm4_code(plain, "padRight32")
    # The PROVEN body, the same since the prototype that predates the repository. We tried
    # adding `passwordType` and the five client-identification fields the app sends
    # (deviceId, platform, source, terminalType, appVersion): the server answered exactly
    # the same, `code=1 'A07908'`, with all of them and with none.
    #
    # They are removed instead of being left «just in case». This request is what locks
    # someone's car, and adding made-up fields that have NEVER been seen working is not caution:
    # it is risk with no upside. They remain in the history in case new information turns up one day.
    cuerpo = {"vin": ctx.vin, "tUserId": str(tuid), "channelId": ctx.channel_id,
              "password": password, "needDecode": 0, "scene": 0, "type": 0}
    j = bff("/tsp/v1/app/cpm/checkPassword", cuerpo)
    data = j.get("data") if isinstance(j.get("data"), dict) else {}
    tid = data.get("taskId") or j.get("taskId")
    if tid:
        attempt.success()      # the PIN is correct → the threshold goes back to zero
        return tid

    # no taskId: tell the CAUSE apart to route the right remedy.
    code = j.get("code")
    # DIAGNOSTICS (2026-07-06): checkPassword's RAW code/message is the ONLY way to
    # know whether it is really a wrong PIN or another cause (vehicle permissions, scene/
    # channelId parameters, backend). Until now it was NOT logged → in the log the lockout only said
    # "wrong PINs", which is our INFERENCE. Now we log the real code + message (non-sensitive
    # fields).
    cp_msg = str(j.get("message") or j.get("msg") or "").strip()
    detail = f"code={code}" + (f" '{cp_msg[:100]}'" if cp_msg else "")
    _LOGGER.warning("[taskId] checkPassword NO devolvió un taskId → %s "
                    "(respuesta cruda del servidor; si no es un PIN erróneo, la causa está aquí)", detail)

    # a single table lookup decides the remedy AND whether to count towards the lockout. They used to be
    # two `if`s on separate sets plus a default branch: three places to keep
    # aligned by hand, and that is where the classification had gone wrong.
    # Classify by the MOST specific datum. This backend answers `code=1` —its generic
    # rejection, the same one a malformed queryList returns— and puts the real code in the
    # `message`. Classifying by the `1` sent everything to the default rule, which blames the PIN and
    # spends a lockout attempt: that is how a correct PIN was accused for hours.
    especifico = cp_msg if re.fullmatch(r"A\d{5}", cp_msg) else code
    outcome = routing.classify(especifico, routing.CONTEXT_CHECKPASSWORD)
    if outcome.counts_for_lockout:
        attempt.record_failure()
    raise CommandError(_checkpassword_message(outcome, detail),
                       code=str(code) if code else None, reason=outcome.reason)


def _checkpassword_message(outcome, detail: str) -> str:
    """Message for the user, consistent with the remedy that will be offered.

    Kept apart from the decision on purpose: the text can be rewritten or translated without touching the
    routing, and vice versa."""
    if outcome.reason == routing.REASON_REAUTH:
        return (f"Sesión caducada [checkPassword {detail}] — vuelve a iniciar sesión con "
                "tu cuenta Ebro")
    if outcome.reason == routing.REASON_CONFIG:
        return (f"Comando rechazado por el servidor [checkPassword {detail}] — no es el PIN: "
                "la cuenta no tiene permiso sobre este coche o la petición fue "
                "rechazada. Reinténtalo más tarde; si persiste se necesitan los logs.")
    return (f"PIN de comandos rechazado por el servidor [checkPassword {detail}] — vuelve a "
            "configurarlo en los ajustes de la cuenta Ebro")


# In-memory taskId cache. Minting it means doing the whole checkPassword (PIN) round trip:
# it is the SLOW part of every command. But the taskId stays valid for a while → we reuse it and
# only re-mint it when the car rejects it (TASKID_INVALID) or the TTL expires. That way most
# commands become a single signed POST instead of PIN + POST.
# the cache lives in `ctx.state` — the taskId is bound to the PIN and the VIN, so it cannot be
# shared between vehicles (it used to be, and that meant a command going to the wrong car).

# Codes with which the car says "this taskId is no good" → re-minted and retried once.
# Derived from the single table, no longer a parallel list.

# Codes with which the car says "this taskId is no good" → re-minted and retried once.
# Derived from the single table, no longer a parallel list.
TASKID_INVALID = routing.TASKID_INVALID


def invalidate_taskid(ctx):
    """Drop the cached taskId (the car rejected it as invalid/expired)."""
    ctx.invalidate_taskid()


def get_taskid(ctx, tuid, emit=lambda m: None, force_mint=False):
    """taskId source, in order: piggyback file → cache → minted via checkPassword.
    `force_mint=True` skips file/cache and mints a new one (used on the retry after a rejection:
    reusing the same rejected source would give the same error again)."""
    if not force_mint:
        try:
            if os.path.exists(ctx.taskid_file):
                with open(ctx.taskid_file) as fh:
                    v = fh.read().strip()
                if v:
                    return v, "file"
        except OSError:
            pass
        if ctx.state.taskid and (time.time() - ctx.state.taskid_ts) < ctx.taskid_ttl:
            return ctx.state.taskid, "cache"
    if ctx.mint_taskid:
        emit("generando taskId (checkPassword)…")
        try:
            tid = _mint_taskid(ctx, tuid)
        except CommandError as e:
            # wrong PIN / lockout / session: publish the detail and PROPAGATE → send() lets it
            # bubble up with its `reason` for remedy routing.
            emit(str(e))
            raise
        except Exception as e:
            emit(f"checkPassword falló: {e}")
            raise CommandError(
                "PIN de comandos no verificable — vuelve a configurarlo en los ajustes "
                f"de la cuenta Ebro ({e})", reason="pin") from e
        if tid:
            ctx.state.taskid = tid
            ctx.state.taskid_ts = time.time()
            return tid, "checkPassword"
    return None, "none"
