#!/usr/bin/env python3
"""
wake.py — Ebro Auto "Wake car": EXACT replica of the official app's flow
          (1× smsAwaken → realtime/location polling), meant to be invoked from the
          interface button.

Flow (verified against the real code):
  1) bff_login()  : token.json (access_token) → POST {BFF}/tsp/v1/app/auth/login → userToken/tUserId
  2) smsAwaken    : POST {TSP}/asc/vehicleControl/smsAwaken {vin}, signed with tsp_sign
                    code "000000" = wake accepted; "A07312" = SMS-wake rate limit/quota.
  3) polling ~60s : /asr/manager/realtime + /asc/vehicleControl/queryVehicleLocation every 5s.
                    In parallel the MQTT listener catches the 5A02s → is_awake() turns True.

⚠️  smsAwaken HAS A REAL RATE LIMIT. The button must NOT be hammered:
    - `do_wake` honours a COOLDOWN (`ctx.wake_cooldown_s`, 300 s) between two smsAwaken sent;
    - only one `do_wake` at a time (lock against double taps).

Strictly personal use (the user's own car/account). Do NOT publish token/certificates.
"""
import hashlib
import json
import os
import time

# package-relative imports.
from . import codes, ebro_auth as A

# VIN, TSP_HOST and the token path are NOT module globals rewritten before each
# call: they come from the vehicle's `CoreCtx` (first argument of every function). Same for the
# locks, which used to be per-process: two configured cars fought over them for no reason.




# NB on the wake cooldown: it lives in `ctx.state.last_sms_ts`, i.e. in the vehicle's
# state (in memory). It used to be in a JSON file shared by all vehicles:
# with two cars, waking one blocked the other.


# ───────────────────────── REST calls (patchable in tests) ──────────────────
def _access_token(ctx):
    """Read the access_token from the vehicle's token.json. Defensive: handles both
    {data:{...}} and the flat format, and does not blow up with KeyError if the field is missing
    (returns None). Single point where the token is read: commands/provision use this one."""
    with open(ctx.token_path) as fh:
        tok = json.load(fh)
    if not isinstance(tok, dict):
        return None
    d = tok.get("data", tok)
    if isinstance(d, dict) and d.get("access_token"):
        return d["access_token"]
    return tok.get("access_token")

def _refresh_outcome(ctx, ok: bool, reason: str, *, diag: bool = True, **extra) -> tuple[bool, str]:
    """Record the renewal outcome (per-vehicle state + diagnostic monitor)
    and return it.

    The REASON is a stable marker, not a text for the user: routing is based on it
    (see `session.check`). Only outcome/reason/HTTP status go into diag — NEVER the
    token, which is a credential.

    `diag=False` for outcomes decided locally without contacting the server:
    recording them would fill the diagnostic file with an event every few minutes for
    a whole dead session, burying the only event that matters (the original rejection)."""
    ctx.state.refresh_reason = reason
    ctx.state.refresh_ts = time.time()
    if diag:
        ctx.diag("token_refresh", ok=ok, reason=reason, **extra)
    return ok, reason


def _fingerprint(rt: str) -> str:
    """Fingerprint of the refresh_token: this is what gets compared, never the plain credential."""
    return hashlib.sha256(rt.encode("utf-8")).hexdigest()


def _refresh_token(ctx) -> bool:
    """True if the renewal succeeded. The WHY (stable marker) is in
    `_refresh_token_detail`, which is what `session.check` consults to tell a revoked
    token from a network outage."""
    ok, _reason = _refresh_token_detail(ctx)
    return ok


def _refresh_token_detail(ctx) -> tuple[bool, str]:
    """Renew the access_token with the `refresh_token` grant and rewrite token.json.

    Returns `(ok, reason)`, where `reason` is a STABLE marker:
      ""                  renewal succeeded
      "ausente"           no usable refresh_token → re-authentication needed
      "red:<Type>"        the request did not go out or did not come back → NOT a revocation
      "rechazado:<key>"   the server said no (e.g. `invalid_grant`) → re-authenticate
      "respuesta"         2xx response but no access_token → treated as a rejection

    The distinction between "red:" and "rechazado:" is the whole point of the function: without it,
    an unstable connection would bring up the «Reautenticar» card and the user would re-authenticate
    for nothing over a problem that would have solved itself.

    Protected by the token lock with double-checking. The access_token the
    caller saw is snapshotted (BEFORE the lock); inside the lock token.json is re-read: if on disk the
    access_token has already changed, another thread already renewed → the refresh is NOT redone (redoing it would burn
    the new refresh_token and invalidate the session)."""
    # pre-lock snapshot: the token the caller considered expired
    try:
        with open(ctx.token_path) as fh:
            tok0 = json.load(fh)
    except Exception:
        return _refresh_outcome(ctx, False, "ausente")
    seen_at = (tok0.get("data", tok0) or {}).get("access_token") if isinstance(tok0, dict) else None

    with ctx.state.token_lock:
        # double-check INSIDE the lock: re-read the current state of the file
        try:
            with open(ctx.token_path) as fh:
                tok = json.load(fh)
        except Exception:
            return _refresh_outcome(ctx, False, "ausente")
        d = (tok.get("data", tok) or {}) if isinstance(tok, dict) else {}
        cur_at = d.get("access_token")
        if seen_at and cur_at and cur_at != seen_at:
            # already renewed by another thread: the token on disk is valid, leave it alone
            return _refresh_outcome(ctx, True, "", concurrente=True)
        rt = d.get("refresh_token")
        if not rt:
            return _refresh_outcome(ctx, False, "ausente")
        # BRAKE: if the server already rejected EXACTLY this refresh_token, there is nothing to
        # gain by presenting it again — a revoked token does not become valid on its own,
        # re-authentication is needed. Without this brake every call to the cloud (session check,
        # probe, driving heartbeat) retries: measured on 22/07, 5 identical attempts in 6
        # minutes, and throughout the ~10 hours of dead session the night before. Hammering the
        # authentication endpoint with already rejected credentials is exactly the behaviour
        # gateways penalise.
        # It is retried anyway once every `ctx.retry_refresh_after_s`, so as not to stay
        # blocked forever if that rejection was a server hiccup; and the brake is
        # released on its own as soon as a different refresh_token shows up in the file.
        fingerprint = _fingerprint(rt)
        burned = ctx.state.refresh_burned
        elapsed = time.time() - (ctx.state.refresh_burned_ts or 0.0)
        if fingerprint == burned and elapsed < ctx.retry_refresh_after_s:
            return _refresh_outcome(ctx, False, "rechazado:ya_rechazado", diag=False)
        # VERIFIED recipe (ebro_login): oauth2/token with the refresh_token grant, client
        # legendApp:legendApp, tenant 3000010, NO gateway signature. host = ctx.bff (legend).
        from . import ebro_login as EL
        try:
            ok_r, res = EL.refresh_token(rt, host=ctx.bff)
        except Exception as err:
            return _refresh_outcome(ctx, False, f"red:{type(err).__name__}")
        if not ok_r:
            # rejected by the server (e.g. revoked refresh_token) → a new login is needed
            key = str(res)[:40]
            # arm the brake: this token stays burned until another one arrives
            ctx.state.refresh_burned = fingerprint
            ctx.state.refresh_burned_ts = time.time()
            return _refresh_outcome(ctx, False, f"rechazado:{key}")
        j = res["raw"]
        http = 200
        # atomic write: new token to a temp file and then rename (token.json never corrupt)
        try:
            path = ctx.token_path
            tmp = path + ".tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(j, f, ensure_ascii=False)
            os.chmod(tmp, 0o600)   # the token is a credential: readable by the owner only
            os.replace(tmp, path)
        except Exception as err:
            return _refresh_outcome(ctx, False, f"escritura:{type(err).__name__}", http=http)
        return _refresh_outcome(ctx, True, "", http=http)


def _eta_token(ctx) -> tuple[float, int]:
    """(seconds elapsed since the last renewal, declared lifetime in seconds).

    The age is measured on token.json's mtime, which is rewritten on every renewal: the
    token is opaque (not a JWT), so its issue date cannot be read from inside it.
    Returns (-1, 0) if it cannot be determined."""
    try:
        eta = max(0.0, time.time() - os.stat(ctx.token_path).st_mtime)
        with open(ctx.token_path) as fh:
            tok = json.load(fh)
    except Exception:
        return -1.0, 0
    d = (tok.get("data", tok) or {}) if isinstance(tok, dict) else {}
    try:
        lifetime = int(d.get("expires_in") or 0)
    except (TypeError, ValueError):
        lifetime = 0
    return eta, lifetime

def _bff_login(ctx, _allow_refresh=True):
    """Return (userToken, tUserId). Raises on a network error; (None,None) if rejected.
    If the session has expired it tries ONE automatic refresh_token and retries once."""
    tok = _access_token(ctx)
    H = A.headers_post("/tsp/v1/app/auth/login", ctx=ctx, extra={
        "Authorization": f"Bearer {tok}",
        "Content-Type": "application/json; charset=UTF-8",
        "Accept": "application/json, text/plain, */*"})
    # `bff_post` normalises the real expired-token/424 case, where the BFF returns a
    # body whose top level is a STRING and not an object: it arrives as {} and is treated as an
    # invalid session, instead of blowing up with AttributeError inside an executor.
    from .http import READ_TIMEOUT_S, bff_post

    j = bff_post(ctx, "/tsp/v1/app/auth/login", {"channelId": ctx.channel_id},
                 headers=H, timeout=READ_TIMEOUT_S)
    d = j.get("data", {})
    # the renewal gate is the ABSENCE of a userToken, not just a non-dict `data`.
    # Before, a `data` that WAS a dict but had no userToken (e.g. {} or a structured error
    # body) skipped the renewal entirely → (None,None) was returned and the user was
    # asked to re-authenticate when the silent refresh_token would have been enough.
    ut = d.get("userToken") if isinstance(d, dict) else None
    if not ut:
        # session expired: try ONE automatic token renewal and retry just once
        if _allow_refresh and _refresh_token(ctx):
            return _bff_login(ctx, _allow_refresh=False)
        return None, None
    return ut, d.get("tUserId")

def _signed_post(ctx, ut: str, path: str, params: dict):
    """Signed POST to the TSP. The construction lives in `core/http`; this name is kept
    because `probe`, `commands` and the tests patch it by this name."""
    from .http import signed_post

    return signed_post(ctx, ut, path, params)

def _code_of(j):
    return j.get("code") if isinstance(j, dict) else j

def _payload(j):
    """Useful payload of the tspconsole response: under "data" on some endpoints and under
    "body" on others (e.g. /asr/manager/realtime → "body" with 84 fields). Returns the first
    non-empty dict, or None."""
    if not isinstance(j, dict):
        return None
    for k in ("data", "body"):
        v = j.get(k)
        if isinstance(v, dict) and v:
            return v
    return None


def _has_live_data(j):
    return _payload(j) is not None


# ───────────────────────── button orchestration ──────────────────────────
def do_wake(ctx, publish, is_awake=None, send_sms=True):
    """Run the wake flow and report the state (already readable strings) via `publish`.

      ctx            -> the vehicle's CoreCtx (VIN, host, token, cooldown)
      publish(text)  -> callback reporting the state (called several times)
      is_awake()     -> optional callback: True if MQTT events are already arriving (car awake)
      send_sms       -> if False it does NOT really send smsAwaken (tests/diagnostics only)

    Returns a summary dict {ok, online, code, ...}. Never raises: every error → status.
    """
    # PER-VEHICLE lock: two cars can be woken in parallel, the same one cannot.
    if not ctx.state.wake_lock.acquire(blocking=False):
        publish("⏳ Despertar ya en curso, espera…")
        return {"ok": False, "reason": "busy"}
    try:
        return _do_wake_inner(ctx, publish, is_awake, send_sms)
    except Exception as e:
        publish(f"⚠️ Error al despertar: {type(e).__name__}: {e}")
        return {"ok": False, "reason": "exception", "error": str(e)}
    finally:
        ctx.state.wake_lock.release()


def _do_wake_inner(ctx, publish, is_awake, send_sms):
    now = time.time()

    # 0) rate-limit cooldown (only if we are really going to send the SMS)
    if send_sms:
        last = ctx.state.last_sms_ts
        wait = ctx.wake_cooldown_s - (now - last)
        if last and wait > 0:
            mm, ss = divmod(int(wait), 60)
            publish(f"⏳ Anti límite de frecuencia: espera aún {mm}m{ss:02d}s antes de volver a despertar")
            return {"ok": False, "reason": "cooldown", "wait_s": int(wait)}

    # if the car is already publishing on MQTT, it is already awake: no SMS
    if is_awake and is_awake():
        publish("🟢 El coche ya está despierto (está enviando datos) — no hace falta despertarlo")
        return {"ok": True, "online": True, "reason": "already_awake"}

    # 1) login BFF → userToken
    publish("🔑 Accediendo…")
    ut, _tu = _bff_login(ctx)
    if not ut:
        publish("🔑 Sesión caducada (token viejo o app oficial abierta): vuelve a autenticarte")
        return {"ok": False, "reason": "no_usertoken"}

    # 2) smsAwaken (just once)
    code = None
    if send_sms:
        _sc, j = _signed_post(ctx, ut, "/asc/vehicleControl/smsAwaken", {"vin": ctx.vin})
        code = _code_of(j)
        ctx.state.last_sms_ts = time.time()     # record NOW for the cooldown, even on error
        if str(code) in ("000000", "A00079"):
            publish("✅ Despertar enviado — espero a que el coche se conecte…")
        elif str(code) == "A07312":
            publish("🚫 Límite de despertar (A07312): el coche rechaza más despertares ahora. Reinténtalo más tarde")
            return {"ok": False, "online": False, "code": code, "reason": "rate_limit"}
        else:
            publish(f"⚠️ Despertar no aceptado ({code}: {codes.meaning(code)}). Aun así intento escuchar…")
    else:
        publish("🧪 (test) smsAwaken NO enviado; paso solo al sondeo")

    # 3) realtime/location polling + MQTT listening (attempts × every_s seconds in total)
    for i in range(ctx.wake_poll_attempts):
        if is_awake and is_awake():
            publish("🟢 Coche ONLINE — está enviando datos en tiempo real")
            return {"ok": True, "online": True, "code": code, "via": "mqtt"}
        _sc1, j1 = _signed_post(ctx, ut, "/asr/manager/realtime", {"vin": ctx.vin})
        _sc2, j2 = _signed_post(ctx, ut, "/asc/vehicleControl/queryVehicleLocation",
                               {"vin": ctx.vin})
        if _has_live_data(j1) or _has_live_data(j2):
            publish("🟢 Coche ONLINE — datos en tiempo real recibidos")
            return {"ok": True, "online": True, "code": code, "via": "rest",
                    "data": _payload(j1) or _payload(j2)}
        secs_left = (ctx.wake_poll_attempts - i - 1) * ctx.wake_poll_every_s
        publish(f"… esperando el despertar ({_code_of(j1)}) — aún ~{secs_left}s")
        time.sleep(ctx.wake_poll_every_s)

    publish("⌛ El coche sigue en reposo (A07900). Reinténtalo cuando se haya usado hace poco o tenga buena cobertura")
    return {"ok": True, "online": False, "code": code, "reason": "still_asleep"}
