#!/usr/bin/env python3
"""El error de un comando rechazado.

Vive en su propio módulo porque lo comparten los dos lados del camino: `taskid`, que falla
cuando el PIN no vale, y `commands`, que falla cuando el coche rechaza el envío. Tenerlo en uno
de los dos creaba un import circular — y, más de fondo, habría sugerido que el error pertenece a
una de las dos etapas cuando describe el resultado de la operación entera.
"""
from . import routing

# Reintentar tiene sentido solo con los códigos que la tabla marca como transitorios (coche
# ocupado). Derivado de `routing`, no una lista paralela.
RETRYABLE_CODES = routing.RETRYABLE_CODES


class CommandError(Exception):
    """Comando rechazado por el backend/coche (NO ejecutado). `code` = código tspconsole,
    `retryable` = True si reintentar tiene sentido (ej. coche ocupado). Se deja propagar hasta
    la API de comandos, que devuelve a la interfaz el error real (y su `reason`) en vez de un
    falso éxito.

    `reason` enruta el REMEDIO en la interfaz (routing por causa, no solo por código):
      - "pin"    = PIN de comandos erróneo / anti-bloqueo / PIN ausente → reconfigurar el PIN
                   (ajustes de la cuenta Ebro). NO es un problema de sesión: el token es
                   válido y la telemetría sigue llegando.
      - "reauth" = sesión/token caducados (login fallido, code A00000) → volver a iniciar sesión
                   con la cuenta Ebro. Eso NO cambia el PIN: los dos canales son distintos.
      - "config" = rechazo NO imputable al PIN ni a la sesión (permisos del vehículo, petición
                   malformada, generación de taskId desactivada): ningún remedio automático,
                   solo aviso. No pide cambiar el PIN y no cuenta para el anti-bloqueo.
      - None     = otro rechazo del coche (ocupado, no permitido, en reposo): solo aviso."""

    def __init__(self, message: str, code: str | None = None, reason: str | None = None) -> None:
        super().__init__(message)
        self.code = code
        self.reason = reason
        self.retryable = code in RETRYABLE_CODES
