"""Política de comandos: cuáles exigen el PIN, qué parámetros se aceptan y cómo se comprueba el PIN.

**Por qué existe.** Con una sesión iniciada, la API puede abrir el coche. Estar «dentro» de
EVBro no debe bastar para desbloquearlo: los comandos que abren o desarman exigen teclear el
PIN en CADA envío, y el PIN no se guarda en el navegador.

**Cómo se comprueba.**
  * Si hay un PIN guardado en la cuenta, el tecleado se compara en local (tiempo constante) ANTES
    de contactar con Chery: un despiste no gasta un intento del anti-bloqueo de la cuenta Ebro.
    Los fallos locales tienen su propio límite (`pin_throttle`).
  * Si no hay PIN guardado, el tecleado se usa para pedir el `taskId` y lo verifica Chery.
"""
from __future__ import annotations

import hmac

from app.core.catalog import CMD_MAP
from app.security.throttle import LoginThrottle

# Comandos que ABREN, DESBLOQUEAN o DESARMAN. Cerrar/bloquear/activar no lo necesitan.
PIN_REQUIRED = frozenset({
    "desbloquear",
    "maletero_abrir",
    "ventanillas_abrir",
    "ventilar_ventanillas",
    "techo_abrir",
    "alarma_off",
})

# Parámetros que la API acepta por comando. Todo lo demás se rechaza: `params` se mezcla con el
# cuerpo del catálogo, así que sin esta lista `bloquear` + `{"lockType": "1"}` DESBLOQUEARÍA sin PIN.
_TEMPERATURE_RANGE = (16.0, 32.0)
_MINUTES_RANGE = (1, 60)
ALLOWED_PARAMS = {
    "clima_on": {"temperature", "times"},
}

# Reintentos de PIN incorrecto (contra el PIN guardado): 5 en 10 minutos, contados POR COCHE: the PIN belongs to the car's
# Ebro account, so one manager's mistakes must never lock the commands of another manager's car.
pin_throttle = LoginThrottle(max_failures=5, window_s=600)


def pin_throttle_key(vin: str) -> str:
    return f"pin:{vin}"

PIN_REQUIRED_MSG = "Este comando requiere que introduzcas el PIN."
PIN_INVALID_MSG = "PIN incorrecto."


class CommandRejected(Exception):
    """El comando se rechaza ANTES de enviarlo al coche. `reason` es un valor estable para la UI."""

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
    """Devuelve los parámetros validados y normalizados, o lanza CommandRejected."""
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
    """Decide con qué PIN se envía el comando. Lanza CommandRejected si no procede.

    `supplied_pin`: el tecleado en esta petición. `stored_pin`: el guardado en la cuenta ('' si no hay). `vin`: the car, which
    scopes the retry limit. `policy`: the owner's choice (`POLICIES`). Devuelve el PIN a usar, SOLO para este envío (nunca se guarda
    por el camino)."""
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
    """Claves de PIN_REQUIRED/ALLOWED_PARAMS que no existen en el catálogo (para los tests)."""
    return sorted((PIN_REQUIRED | set(ALLOWED_PARAMS)) - set(CMD_MAP))
