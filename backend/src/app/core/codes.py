#!/usr/bin/env python3
"""
codes.py — The SINGLE map of Chery tspconsole/BFF response codes → readable text.

Source of truth for the ONLY diagnostic texts shown to the user (EVBro's interface and log).
The same code (e.g. A07900) used to have 3 different meanings spread across
commands/wake/probe/provision; this map unifies them. It does NOT change the command logic:
the modules decide the flow from the codes, this only translates a code into
a sentence.

⚠️ Some codes (A07900 above all) are CONTEXTUAL in the Chery backend: the text
here is the most frequent/useful one; each caller can add context.
"""

# Code → readable sentence (Spanish, for non-technical users).
CODE_MEANING = {
    "000000": "ok ✅",
    "A00079": "comando aceptado ✅",
    # A00082: the car is BUSY (it handles one command at a time) → the command was NOT
    # executed. Transient: retry in a few seconds (verified live 2026-06-21).
    "A00082": "coche ocupado ⏳ (hay otro comando en curso) — reinténtalo en unos segundos",
    # A00084 (i18n: "No vehicle control command permission"): the account/vehicle has no
    # permission FOR THAT command. Seen live with remoteStart (2026-06-21): this Ebro Auto
    # does not allow remote engine start, while climate/locking/GPS do work.
    "A00084": "comando no permitido en este coche 🚫 (permiso denegado para esta función)",
    "A00089": "taskId no válido ❌ (hace falta un taskId validado por checkPassword)",
    "A00546": "taskId no válido ❌ (scene incorrecto en checkPassword)",
    "A00567": "parámetros de checkPassword incompletos ❌",
    "A00000": "token caducado/no válido ❌ (vuelve a iniciar sesión)",
    "A07312": "límite de despertar 🚫 (el coche rechaza más despertares ahora, reinténtalo más tarde)",
    # A07900 is contextual: on poll/probe = car asleep; on commands = invalid signature or
    # car_token. Neutral text covering the most frequent case.
    "A07900": "coche en reposo / no accesible (o firma/car_token no válidos) ⌛",
    # A07908/A07909: `message` of a checkPassword with `code=1` → wrong command PIN.
    # Confirmed in the field on 2026-10-01 by trying the correct PIN on the same account.
    "A07908": "PIN de comandos incorrecto ❌",
    "A07909": "PIN de comandos incorrecto ❌",
}


def meaning(code, default=None):
    """Return the readable sentence for `code`. If unknown, return `default`
    (or a generic string with the raw code). Also accepts code=None/non-str."""
    if code is None:
        return default if default is not None else "sin código"
    key = str(code)
    if key in CODE_MEANING:
        return CODE_MEANING[key]
    return default if default is not None else f"código {key}"
