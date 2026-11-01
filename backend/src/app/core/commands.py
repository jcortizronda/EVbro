#!/usr/bin/env python3
"""
commands.py — Catalogue + sending of the Ebro Auto car's commands (tspconsole EU REST).

Reuses the verified chain:
  - userToken via  wake._bff_login()      (token in token.json, automatic refresh)
  - signature      tsp_sign.sign_body()   (base64(sha256(base)).upper())
  - taskId         get_taskid()           (piggyback file -> auto-minted via checkPassword)

POST  https://tspconsole-eu.ebroauto.com/asc/vehicleControl/<endpoint>
Header: Authorization=<userToken>, timestamp=<ms>, Content-Type=application/json; charset=utf-8,
        User-Agent=okhttp/4.9.2

⚠️  Every send() with a valid taskId ACTS on the car. It is meant to be invoked ONLY
    when the user taps a button in EVBro's interface (= their explicit consent).
    The catalogue bodies are rebuilt 1:1 from real captured envelopes.
"""
import contextlib
import json
import logging
import time
import urllib.error
import urllib.request

from . import codes, routing, tsp_sign, wake
from .catalog import CMD_MAP
from .errors import CommandError
from .taskid import get_taskid, invalidate_taskid

_LOGGER = logging.getLogger(__name__)


# The import-time `importlib.reload(tsp_sign)` was removed (a pointless side effect; tsp_sign is
# not mutated anywhere else and reloading it on import could undo monkeypatches).

# VIN, PIN, host, taskId file and automatic minting are NOT module globals
# rewritten before each call: they come from the vehicle's `CoreCtx`, the first argument of
# every public function. With two cars configured, the old approach let the second
# entry overwrite the first one's configuration and a command could leave for the
# wrong car.

# tspconsole response codes → readable text: now from the SINGLE map core/codes.py.
CODE_MEANING = codes.CODE_MEANING

# Command result: the backend ALWAYS answers HTTP 200, the real result is in the body's
# `code`. `SUCCESS_CODES` = command accepted by the backend (the car then confirms
# via MQTT 110x); `FAILURE_CODES` = command NOT executed (car busy/asleep, permission
# denied, invalid taskId or token). Telling the two apart is what lets the interface
# NOT show a false "success" when the car has refused.
#
# These sets are now DERIVED from the single routing table, no longer hand-written
# lists next to it. They were parallel lists that could silently drift from how codes
# were actually routed.
SUCCESS_CODES = routing.SUCCESS_CODES
FAILURE_CODES = routing.FAILURE_CODES
RETRYABLE_CODES = routing.RETRYABLE_CODES


def send(ctx, cmd_key, emit=lambda m: None, params=None):
    """Send a command. emit(str) receives the steps (to log the steps).
       `params` (optional) = overrides/additions to the catalogue body BEFORE the common fields
       → enables the parametric commands (climate: temperature/times; immediate charging:
       controlType; scheduled charging: mainSwitch + chargeAppointPlans). The system fields
       (clientType/seq/taskId/vin) are still always the ones generated here.
       Returns a readable result string."""
    c = CMD_MAP.get(cmd_key)
    if not c:
        emit(f"comando desconocido: {cmd_key}")
        raise CommandError(f"Comando desconocido: {cmd_key}")

    token, tuid = wake._bff_login(ctx)
    if not token:
        emit("acceso fallido (¿token caducado?)")
        raise CommandError(
            "Sesión caducada — vuelve a iniciar sesión con tu cuenta Ebro",
            reason="reauth")

    # explicit path (e.g. alarm at /act/theftAlarm/setSwitch) or the classic
    # /asc/vehicleControl/<endpoint> for the standard vehicle commands.
    url = ctx.tsp_host + (c.get("path") or ("/asc/vehicleControl/" + c["endpoint"]))

    # Attempt 1 with the reused taskId (fast). If the car rejects it as invalid/
    # expired, it is re-minted (checkPassword) and retried ONCE only.
    for attempt in (1, 2):
        # get_taskid propagates CommandError (PIN/lockout/session) with its `reason`.
        taskid, src = get_taskid(ctx, tuid, emit, force_mint=(attempt == 2))
        if not taskid:
            # no taskId BUT no exception = minting is DISABLED
            # (EBRO_MINT_TASKID=0) and there was no taskId in env or in the file. The PIN has
            # nothing to do with it: saying «wrong PIN» sent the user off to reconfigure a healthy PIN.
            emit("no hay taskId disponible (generación desactivada)")
            raise CommandError(
                "Generación de taskId desactivada (EBRO_MINT_TASKID=0) y no hay ningún "
                "taskId disponible: los comandos no pueden ejecutarse. Reactiva la generación "
                "automática para usar los botones.",
                reason="config")

        ts = int(time.time() * 1000)
        body = dict(c["body"])
        if params:
            body.update(params)    # parametric override (temperature/duration/controlType/plan)
        body.update({"clientType": "1", "seq": f"{ctx.vin}-{ts}",
                     "taskId": taskid, "vin": ctx.vin})
        m = tsp_sign.sign_body(body, ts, half=ctx.sign_key)
        payload = json.dumps(m, separators=(",", ":"), ensure_ascii=False).encode()
        headers = {"Authorization": token, "timestamp": str(ts),
                   "Content-Type": "application/json; charset=utf-8", "User-Agent": "okhttp/4.9.2"}
        emit(f"enviando {c['name']} (taskId:{src})…")
        req = urllib.request.Request(url, data=payload, headers=headers, method="POST")
        try:
            with urllib.request.urlopen(req, timeout=20) as resp:
                raw = resp.read().decode("utf-8", "replace")
                status = resp.status
        except urllib.error.HTTPError as e:
            raw = e.read().decode("utf-8", "replace")
            status = e.code
        except Exception as e:
            emit(f"error de red: {e}")
            raise CommandError(f"Error de red al enviar el comando: {e}") from e

        code = None
        with contextlib.suppress(Exception):   # non-JSON body: stays without a code
            code = json.loads(raw).get("code")

        # the table says whether the taskId must be re-minted. On the first pass it is re-minted
        # and retried, so the user does not see a false error for a merely expired taskId.
        outcome = routing.classify(code, routing.CONTEXT_COMMAND)
        if attempt == 1 and outcome.regenerate_taskid and ctx.mint_taskid:
            invalidate_taskid(ctx)
            emit("taskId ya no válido → lo renuevo y reintento…")
            continue
        break

    meaning = CODE_MEANING.get(code, raw[:120])
    out = f"{c['name']}: HTTP {status} {code or ''} — {meaning}"
    emit(out)
    # Real result from the `code` (the backend always answers HTTP 200). A known failure =
    # command NOT executed → CommandError, so the interface gets the real failure instead
    # of showing a false success. Unknown codes stay non-blocking out of caution: we do not
    # invent a failure the backend has not declared.
    if outcome.failed:
        raise CommandError(out, code=outcome.code, reason=outcome.reason)
    return out


def query_charge_schedule(ctx):
    """Read the charge schedule the car has (READ ONLY).

    `/asd/chargeAppointManage/chargeAppointQuery`, without a taskId: it does not actuate anything
    or wake the car. Returns the raw `body` (`mainSwitch` + `chargeAppointPlans`) or `None`.

    It exists because the schedule can be changed from the official app or from the car
    itself, and EVBro learns nothing of it that way: the interface's time and duration fields
    are the PREFERENCE of what will be sent, not what the vehicle currently has set."""
    token, _tuid = wake._bff_login(ctx)
    if not token:
        return None
    try:
        _status, j = wake._signed_post(ctx, token, "/asd/chargeAppointManage/chargeAppointQuery",
                                       {"vin": ctx.vin})
    except Exception:
        return None
    body = j.get("body") if isinstance(j, dict) else None
    return body if isinstance(body, dict) else None


def query_theft_switch(ctx):
    """Read the alarm state (READ ONLY, /act/theftAlarm/querySwitch).
       Returns 1/0 (int) or None if unavailable. Does NOT use a taskId or actuate anything:
       the response puts the value under `body.theftAlarmSwitch`."""
    token, _tuid = wake._bff_login(ctx)
    if not token:
        return None
    try:
        _status, j = wake._signed_post(ctx, token, "/act/theftAlarm/querySwitch",
                                       {"vin": ctx.vin})
    except Exception:
        return None
    if isinstance(j, dict):
        body = j.get("body") if isinstance(j.get("body"), dict) else {}
        v = body.get("theftAlarmSwitch")
        if v is not None:
            try:
                return int(v)
            except (TypeError, ValueError):
                return None
    return None
