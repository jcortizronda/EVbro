"""What a person is told when an order to the car does not go through, in plain words and in their language.

`core/` (a mirror of the Home Assistant integration) raises `CommandError` with a technical text meant for the log, plus the cloud's
`code`, the remedy (`reason`) and, where there is no code, a stable `kind`. The page used to show that text as it was: «Bloquear
puertas: HTTP 200 A00084 — comando no permitido en este coche 🚫», «PIN de comandos rechazado por el servidor [checkPassword code=1
'A07908']…», in Spanish whatever the user's language (texts audit, 27-11-2026). This is the one place with the words for them; the
technical text still goes to the log and the audit.
"""
from __future__ import annotations

from typing import Optional

from app.core.errors import CommandError
from app.i18n import _

# Cloud codes of a command's answer (core/codes.py, core/routing.py)
_BY_CODE = {
    "A00082": lambda name: _("El coche está ocupado con otra orden. Espera unos segundos y vuelve a intentarlo."),
    "A00084": lambda name: _("Ebro no permite «{p0}» en este coche.", p0=name),
    "A00089": lambda name: _("Ebro no ha aceptado la orden. Vuelve a intentarlo; si se repite, vuelve a conectar la cuenta de Ebro en Ajustes → Cuenta."),
    "A00546": lambda name: _("Ebro no ha aceptado la orden. Vuelve a intentarlo; si se repite, vuelve a conectar la cuenta de Ebro en Ajustes → Cuenta."),
    "A00567": lambda name: _("Ebro no ha aceptado la orden. Vuelve a intentarlo; si se repite, vuelve a conectar la cuenta de Ebro en Ajustes → Cuenta."),
    "A00000": lambda name: _("Ebro ha cerrado la sesión de tu cuenta. Vuelve a conectarla en Ajustes → Cuenta."),
    "A07312": lambda name: _("Ebro limita las veces que se puede despertar al coche y ya se ha llegado al tope. Vuelve a intentarlo más tarde."),
    "A07900": lambda name: _("El coche no responde: puede estar sin cobertura. Vuelve a intentarlo en unos minutos."),
}

# Failures without a cloud code, by `CommandError.kind`
_BY_KIND = {
    "network": lambda name: _("No se pudo enviar la orden: EVbro no consigue conectar con Ebro. Vuelve a intentarlo en unos minutos."),
    "unconfirmed": lambda name: _("Ebro no ha confirmado la orden: puede que se haya hecho o que no. Mira cómo está el coche antes de repetirla."),
    "session": lambda name: _("Ebro ha cerrado la sesión de tu cuenta. Vuelve a conectarla en Ajustes → Cuenta."),
    "disabled": lambda name: _("Este servidor tiene desactivado el envío de órdenes. Avisa a quien lo administra."),
    "pin_missing": lambda name: _("Falta el PIN de Ebro. Escríbelo para enviar la orden."),
    "pin_rejected": lambda name: _("Ebro ya rechazó este PIN. Para no bloquear tu cuenta, EVbro no lo vuelve a probar: escribe el PIN correcto."),
    "pin_locked": lambda name: _("Demasiados PIN incorrectos seguidos. Para proteger tu cuenta de Ebro, EVbro no enviará órdenes hasta que guardes el PIN correcto en Ajustes → Cuenta."),
    "pin_wrong": lambda name: _("Ebro no acepta ese PIN. Es el PIN de 6 dígitos de tu cuenta de Ebro, no la contraseña."),
    "not_pin": lambda name: _("Ebro no ha dado permiso para la orden, y no es por el PIN. Puede que la cuenta conectada no sea la del titular del coche. Vuelve a intentarlo más tarde."),
    "no_answer": lambda name: _("Ebro no ha contestado bien al comprobar el PIN. No es culpa del PIN: vuelve a intentarlo en unos minutos."),
    "pin_unreachable": lambda name: _("No se pudo comprobar el PIN: Ebro no responde. Vuelve a intentarlo en unos minutos."),
    "pin_unverifiable": lambda name: _("No se pudo comprobar el PIN. Vuelve a intentarlo; si se repite, guarda de nuevo el PIN en Ajustes → Cuenta."),
}

def unexpected() -> str:
    """An exception that is not a refusal (a bug of EVbro): its text stays in the log."""
    return _("No se pudo enviar la orden por un fallo de EVbro. Vuelve a intentarlo; si se repite, avisa al administrador.")


def user_message(err: CommandError, name: Optional[str] = None) -> str:
    """The text for the person who sent the order `name` (the button's name). The kind decides first (the PIN checks answer
    `code=1` whatever happened), then the cloud's code; anything else is a generic refusal that names the order."""
    name = name or _("la orden")
    kind = getattr(err, "kind", None)
    if kind in _BY_KIND:
        return _BY_KIND[kind](name)
    if err.code in _BY_CODE:
        return _BY_CODE[err.code](name)
    if err.reason == "reauth":
        return _BY_KIND["session"](name)
    return _("Ebro no ha aceptado «{p0}». Vuelve a intentarlo en unos minutos.", p0=name)
