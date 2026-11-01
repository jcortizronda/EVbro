"""Command policy: which commands need the PIN, which parameters are accepted and how the PIN is checked.

**Why it exists.** With a signed-in session, the API can open the car. Being «inside»
EVBro must not be enough to unlock it: the commands that open or disarm require typing the
PIN on EVERY send, and the PIN is not stored in the browser.

**How it is checked.**
  * If a PIN is stored on the account, the typed one is compared locally (constant time) BEFORE
    contacting Chery: a slip does not use up an attempt of the Ebro account's lock-out guard.
    Local failures have their own limit (`pin_throttle`).
  * If no PIN is stored, the typed one is used to request the `taskId` and Chery checks it.
"""
from __future__ import annotations

import hmac

from app.core.catalog import CMD_MAP
from app.security.throttle import LoginThrottle

# Commands that OPEN, UNLOCK or DISARM. Closing/locking/arming do not need it.
PIN_REQUIRED = frozenset({
    "desbloquear",
    "maletero_abrir",
    "ventanillas_abrir",
    "ventilar_ventanillas",
    "techo_abrir",
    "alarma_off",
})

# Parameters the API accepts per command. Everything else is rejected: `params` is merged into the
# catalogue body, so without this list `bloquear` + `{"lockType": "1"}` would UNLOCK without a PIN.
_TEMPERATURE_RANGE = (16.0, 32.0)
_MINUTES_RANGE = (1, 60)
ALLOWED_PARAMS = {
    "clima_on": {"temperature", "times"},
}

# Wrong-PIN retries (against the stored PIN): 5 in 10 minutes, counted PER CAR: the PIN belongs to the car's
# Ebro account, so one manager's mistakes must never lock the commands of another manager's car.
pin_throttle = LoginThrottle(max_failures=5, window_s=600)


def pin_throttle_key(vin: str) -> str:
    return f"pin:{vin}"

PIN_REQUIRED_MSG = "Este comando requiere que introduzcas el PIN."
PIN_INVALID_MSG = "PIN incorrecto."


class CommandRejected(Exception):
    """The command is rejected BEFORE it is sent to the car. `reason` is a stable value for the UI."""

    def __init__(self, reason: str, message: str, retry_after: int = 0) -> None:
        super().__init__(message)
        self.reason = reason
        self.message = message
        self.retry_after = retry_after


# When the PIN is asked for, chosen by each manager (AppUser.pin_policy): `open` = the commands above (default), `always` = every
# command, `never` = none (only meaningful with a stored PIN: Chery needs one to issue the taskId, so without it every command asks).
POLICIES = ("open", "always", "never")
DEFAULT_POLICY = "open"


def requires_pin(command_key: str, policy: str = DEFAULT_POLICY) -> bool:
    if policy == "always":
        return True
    if policy == "never":
        return False
    return command_key in PIN_REQUIRED


def sanitize_params(command_key: str, params: dict | None) -> dict | None:
    """Returns the validated, normalised parameters, or raises CommandRejected."""
    if not params:
        return None
    allowed = ALLOWED_PARAMS.get(command_key, set())
    extra = sorted(set(params) - allowed)
    if extra:
        raise CommandRejected("params_invalid", f"Parámetros no permitidos para este comando: {', '.join(extra)}.")

    clean: dict = {}
    if "temperature" in params:
        try:
            t = float(str(params["temperature"]).replace(",", "."))
        except ValueError:
            raise CommandRejected("params_invalid", "La temperatura no es un número.") from None
        if not _TEMPERATURE_RANGE[0] <= t <= _TEMPERATURE_RANGE[1]:
            raise CommandRejected(
                "params_invalid", f"La temperatura debe estar entre {_TEMPERATURE_RANGE[0]:g} y {_TEMPERATURE_RANGE[1]:g} °C.")
        clean["temperature"] = f"{t:.1f}"
    if "times" in params:
        try:
            m = int(str(params["times"]))
        except ValueError:
            raise CommandRejected("params_invalid", "La duración no es un número de minutos.") from None
        if not _MINUTES_RANGE[0] <= m <= _MINUTES_RANGE[1]:
            raise CommandRejected(
                "params_invalid", f"La duración debe estar entre {_MINUTES_RANGE[0]} y {_MINUTES_RANGE[1]} minutos.")
        clean["times"] = str(m)
    return clean


def authorize(command_key: str, supplied_pin: str, stored_pin: str, vin: str = "", policy: str = DEFAULT_POLICY) -> str:
    """Decides which PIN the command is sent with. Raises CommandRejected when it must not go.

    `supplied_pin`: the one typed in this request. `stored_pin`: the one stored on the account ('' if none). `vin`: the car, which
    scopes the retry limit. `policy`: the owner's choice (`POLICIES`). Returns the PIN to use, for THIS send only (it is never stored
    along the way)."""
    supplied = (supplied_pin or "").strip()
    stored = (stored_pin or "").strip()
    key = pin_throttle_key(vin)

    if not requires_pin(command_key, policy):
        return supplied or stored

    if not supplied:
        raise CommandRejected("pin_required", PIN_REQUIRED_MSG)

    wait = pin_throttle.retry_after(key)
    if wait:
        raise CommandRejected(
            "pin_locked", f"Demasiados PIN incorrectos. Espera {max(1, wait // 60)} min antes de reintentar.", wait)

    if stored and not hmac.compare_digest(supplied.encode(), stored.encode()):
        pin_throttle.record_failure(key)
        raise CommandRejected("pin_invalid", PIN_INVALID_MSG)

    pin_throttle.reset(key)
    return supplied


def check_catalog_consistency() -> list[str]:
    """Keys of PIN_REQUIRED/ALLOWED_PARAMS that do not exist in the catalogue (for the tests)."""
    return sorted((PIN_REQUIRED | set(ALLOWED_PARAMS)) - set(CMD_MAP))
