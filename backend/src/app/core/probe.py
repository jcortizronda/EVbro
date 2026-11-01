#!/usr/bin/env python3
"""
probe.py — Ebro Auto "position probe".

Question it answers: when the car is REALLY awake (it is publishing
5A02 on MQTT), does the tspconsole-eu channel return GPS position / realtime data, or
still `A07900`?

Finding (verified against the code of BOTH apps, EU + Russian):
  - There is NO BFF endpoint for commands. The "legend" BFF only has
    auth/cpm(PIN)/env/map/vac/vmc. Commands/position ALL go through Chery's
    SDK → tspconsole-eu `/asc/vehicleControl/*` and `/asr/manager/realtime`,
    i.e. EXACTLY the paths we already use. No hidden channel.
  - So the only variable left is the car's AWAKE STATE. The app
    gets it because it sends right after the car was used; we hit A07900
    (asleep) and cannot wake it (smsAwaken at A07312, per-account quota).

This probe is READ ONLY: it calls `/asr/manager/realtime {vin}` and
`/asc/vehicleControl/queryVehicleLocation {vin}` (the same as wake.py's
polling). It does NOT send smsAwaken, does NOT send commands, does NOT touch the PIN. It is benign:
it is what the app does when you open the "position" page.

It is invoked on the asleep→awake rising edge, with a cooldown so it does not
repeat on every 5A02.

Strictly personal use (the user's own car/account). Do NOT publish token/certificates.
"""
import json
import logging
import os
import time

# Reuses wake.py's already verified infrastructure: BFF login + signed tspconsole POST.
# package-relative imports.
from . import codes, wake as W

_LOGGER = logging.getLogger(__name__)

# "rich" fields of CVRealtimeResBean we care most about (if they arrive at all)
RICH_KEYS = ("lat", "lon", "altitude", "direction", "gpsSpeed", "vehicleSpeed",
             "odometer", "dumpEnergy", "electricRange", "pureElectricRange",
             "chargeState", "inCarTemperature", "onlineStatus")

# GEOGRAPHIC subset of RICH_KEYS: the POSITION. Excluded from the readable message —
# not from telemetry. The `probe_status` message ends up in a sensor's state (and
# therefore in the database), in the diagnostic files and in
# EVBro's log; writing «lat=…, lon=…» there leaked where the car is in plain text in all
# three places (found in the field on 2026-07-20). The position still reaches
# telemetry by its own path (`on_data` receives the RAW data, not this summary), so
# nothing is lost: it only disappears from the TEXT. Odometer/energy/range/temperature
# stay, so the message is still useful for diagnostics.
_GEO_KEYS = ("lat", "lon", "altitude", "direction")
_MSG_KEYS = tuple(k for k in RICH_KEYS if k not in _GEO_KEYS)

# Timestamps the car puts in the realtime payload, from most to least reliable.
_CLOCK_KEYS = ("time", "resultTime")


def freshness(data: dict, now: float | None = None) -> str:
    """Describe HOW OLD the data the probe just returned is.

    The realtime endpoint answers equally well with the car awake or asleep, but they do not
    mean the same: awake, the car answers; asleep, it returns the last snapshot
    the cloud stored, which may be half an hour old. Announcing both as «realtime
    data with the car awake» —which is what it did— sends the user looking for the
    fault where it is not: they press «Update», the sensor says all went well, and the
    boot they just opened still does not show up.

    `onlineStatus` comes from the response itself, not from our own deduction.
    """
    online = str(data.get("onlineStatus", "")).strip() in ("1", "1.0")
    if online:
        return "🟢 En vivo · coche despierto"
    edad = _age_min(data, time.time() if now is None else now)
    if edad is None:
        return "🟡 Estado desde la nube · coche dormido"
    return f"🟡 Estado desde la nube · hace {edad} min · coche dormido"


def _age_min(data: dict, now: float) -> int | None:
    """Minutes elapsed since the payload's timestamp. `None` if it carries none."""
    for key in _CLOCK_KEYS:
        try:
            ms = float(data.get(key))
        except (TypeError, ValueError):
            continue
        if ms:
            return max(0, int((now - ms / 1000) // 60))
    return None


def _log(path: str, rec: dict):
    if not path:
        return
    rec = {"ts": round(time.time(), 3),
           "iso": time.strftime("%Y-%m-%dT%H:%M:%S%z", time.localtime()), **rec}
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "a", encoding="utf-8") as f:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")
    except Exception:
        pass


def _rich(data: dict) -> dict:
    """Extract the interesting fields for the readable summary — WITHOUT the position.

    See `_GEO_KEYS`: the coordinates do not go into the message (which is published as a
    sensor's state, hence persisted and potentially shared). All the other rich
    fields stay. The position still travels intact to telemetry via
    `on_data`, which receives the raw dictionary, not this one."""
    if not isinstance(data, dict):
        return {}
    return {k: data[k] for k in _MSG_KEYS if k in data}


def probe_once(ctx, publish, force=False, on_data=None):
    """Run ONE read-only probe and report the result via `publish(text)`.

    `ctx` = the vehicle's CoreCtx: VIN, host, token and probe cooldown. They used to be
    module globals, so with two cars configured one car's probe consumed the other's
    cooldown — and read the VIN of the last entry started anyway.

    Returns a dict {ok, online, got_data, codes, rich} plus the per-endpoint BREAKDOWN
    (`got_realtime` / `got_location` / `got_travel`, their three named codes,
    `has_position` and `duration_ms`). The breakdown exists for the diagnostic monitor: the
    aggregated summary cannot tell «nobody answered» from «telemetry answered but the
    location returned A07900», which is exactly the difference that took four days to see in the field.
    `has_position` is a boolean on purpose — coordinates do NOT go into diagnostics.
    Never raises.
    `force=True` ignores the cooldown (for the manual test).
    `on_data(data)` (optional): callback invoked with the raw `data` dict ONLY when
    live data is received (car awake) — used to store GPS/battery.
    """
    if not ctx.state.probe_lock.acquire(blocking=False):
        return {"ok": False, "reason": "busy"}
    try:
        now = time.time()
        last_probe = ctx.state.last_probe_ts
        if not force and last_probe and (now - last_probe) < ctx.probe_cooldown_s:
            # SAY SO. Returning silently left the user staring at a button that produced
            # neither a message nor a change: indistinguishable from a broken integration.
            wait_s = int(ctx.probe_cooldown_s - (now - last_probe))
            publish(f"⏳ Lectura reciente · espero {wait_s} s")
            return {"ok": False, "reason": "cooldown", "wait_s": wait_s}
        ctx.state.last_probe_ts = now

        # Do NOT claim here that the car is awake: nothing has been asked yet. The
        # message dates from when the probe only ran on the asleep→awake edge; now it is
        # also triggered by the button and the loop, with the car in any state. The one who knows is
        # `freshness()`, AFTER the response and looking at its `onlineStatus` — and it left
        # the absurdity of announcing «the car is awake» and concluding two lines later that it was asleep.
        publish("🛰️ Consultando…")
        ut, _tu = W._bff_login(ctx)
        if not ut:
            publish("🔑 Sesión caducada · vuelve a autenticarte")
            _log(ctx.probe_log_path, {"event": "probe", "ok": False, "reason": "no_usertoken"})
            return {"ok": False, "reason": "no_usertoken"}

        _sc1, j1 = W._signed_post(ctx, ut, "/asr/manager/realtime", {"vin": ctx.vin})
        _sc2, j2 = W._signed_post(ctx, ut, "/asc/vehicleControl/queryVehicleLocation",
                                 {"vin": ctx.vin})
        # travelQuery: we look for km/odometer (field not seen yet; will show up on the 1st real wake)
        _sc3, j3 = W._signed_post(ctx, ut, "/asd/travelManage/travelQuery", {"vin": ctx.vin})
        c1, c2, c3 = W._code_of(j1), W._code_of(j2), W._code_of(j3)
        got1, got2, got3 = W._has_live_data(j1), W._has_live_data(j2), W._has_live_data(j3)

        # merged data: realtime goes LAST and therefore wins where they overlap. The payload
        # is under "data" or "body" depending on the endpoint (realtime → "body"): W._payload handles
        # both, otherwise the 84 realtime fields were lost.
        #
        # **The POSITION comes from `queryVehicleLocation` and nowhere else.** The response of
        # `/asr/manager/realtime` does NOT carry `lat`/`lon` — verified on a real capture of the
        # official app: 80 fields and not one coordinate. So there is no competition between
        # sources to resolve here, and the merge order does not affect the position.
        #
        # And `queryVehicleLocation` is a QUERY, not an order: it returns the last position
        # known to the cloud, without asking the car for anything. What forces a new fix is the
        # `vehicleLocation` command («Localizar coche (GPS)»), which goes with a taskId and makes the
        # car report. The names say it: `query…` reads, the other one acts. It is deliberate — the
        # probe exists precisely so as not to touch the car.
        data = {}
        for src, got in ((j2, got2), (j3, got3), (j1, got1)):
            payload = W._payload(src)
            if got and isinstance(payload, dict):
                data.update(payload)

        rich = _rich(data)
        _log(ctx.probe_log_path, {"event": "probe", "ok": True, "realtime_code": c1, "location_code": c2,
              "travel_code": c3, "got_realtime": got1, "got_location": got2, "got_travel": got3,
              "rich": rich, "data": data or None,
              "travel_data": j3.get("data") if got3 else None})

        # The same breakdown, but RETURNED: `_log` only writes if `EBRO_PROBE_LOG` is in the
        # environment, which never happens in a normal install. The one who
        # needs it is the diagnostic monitor, and for that it has to go back to the caller.
        desglose = {"realtime_code": c1, "location_code": c2, "travel_code": c3,
                    "got_realtime": got1, "got_location": got2, "got_travel": got3,
                    "has_position": "lat" in data and "lon" in data,
                    "duration_ms": int((time.time() - now) * 1000)}

        got1 = got1 or got3   # if travelQuery brings data with the car awake, it counts as "live"
        if (got1 or got2) and on_data and data:
            try:
                on_data(data)
            except Exception as e:
                publish(f"⚠️ Error al publicar datos · {type(e).__name__}")

        if got1 or got2:
            # WITHOUT dumping the fields here (speed, odometer, battery…): the user already
            # has them in their own sensors, and repeating them made a very long state that cannot
            # be read on a card. They still go to the diagnostic report and the probe log, which
            # is where they are of any use.
            #
            # But the POSITION is reported separately, because this sensor is called «Resultado
            # sonda de UBICACIÓN» and until now it lied by omission: if `realtime` answered and
            # `queryVehicleLocation` did not, it calmly said «🟢 En vivo» and the map stayed
            # stuck. It took four days to see in the field, and only by looking at the history. The reason
            # is given by the location endpoint's code, which we already had and threw away.
            if desglose["has_position"]:
                publish(freshness(data, now))
            else:
                # Saying just «Sin posición» would suggest the whole query failed, and
                # that is not true: telemetry (battery, range, odometer…) DID arrive and
                # its sensors were updated. What is missing is the location.
                #
                # WITHOUT the reason: the only one that shows up in practice is A07900, «coche en reposo»,
                # and placed next to «coche dormido» —which is what the other states say—
                # they look like two different things when they are the same. The code still goes to the log
                # and to the probe result, which is where it is needed for diagnosis.
                publish("🟠 Con datos, sin posición")
            return {"ok": True, "online": True, "got_data": True,
                    "codes": [c1, c2, c3], "rich": rich, **desglose}

        publish(f"🔴 Sin datos · {codes.meaning(c1)}")
        return {"ok": True, "online": False, "got_data": False, "codes": [c1, c2, c3], **desglose}
    except Exception as e:
        publish(f"⚠️ Error de sonda · {type(e).__name__}: {e}")
        return {"ok": False, "reason": "exception", "error": str(e)}
    finally:
        ctx.state.probe_lock.release()
