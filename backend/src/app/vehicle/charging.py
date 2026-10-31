"""Programación de carga del coche: construir planes, horas locales↔UTC y leer lo que tiene el coche.

Procede de `vehicle/charging.py` de la integración de Home Assistant, sin el `ChargeLimiter` (la decisión de cuándo
parar la toma `services/charge_manager.py`) y con `zoneinfo` en lugar de las utilidades de Home Assistant.
Todo es puro: la zona horaria y el instante se pasan como argumentos, así que se prueba sin reloj ni red.

**Lo que hay que saber del coche**
  * `chargeStartStopControl` responde `A00084`: no hay arranque/parada directos. Solo existe la programación.
  * `startTime` se interpreta en **UTC** (verificado en campo: con UTC+2 una carga puesta a las 03:00
    arrancaba a las 05:00). `timeConsuming` es una duración y no se convierte.
  * El coche exige una duración mínima de 1 h; menos da code 89.
  * Parar = imponer una programación cuya ventana ya terminó. Esa ventana vuelve a «abrirse» al día siguiente
    a la misma hora, de ahí `hold_needs_refresh`: hay que moverla antes de que llegue.
"""
from __future__ import annotations

import datetime
from dataclasses import dataclass
from zoneinfo import ZoneInfo

from .constants import (
    CHARGE_MIN_DURATION_MIN,
    CHARGE_STOP_DURATION_MIN,
    CHARGE_STOP_START_BACK_MIN,
    MINUTES_PER_DAY,
)

# Todos los días de la semana: el plan se aplica siempre y el interruptor general decide si está activo.
ALL_WEEKDAYS = [1, 2, 3, 4, 5, 6, 7]

# Cuántos minutos antes de que la ventana de pausa vuelva a abrirse se desplaza. Con la ventana en
# [ahora−90, ahora−30] la próxima apertura está a 22 h 30; se renueva cuando faltan menos de 4 h.
HOLD_REFRESH_MARGIN_MIN = 240


def _utcnow() -> datetime.datetime:
    return datetime.datetime.now(datetime.timezone.utc)


def local_minutes_to_utc(local_mins: int, tz: ZoneInfo, now: datetime.datetime | None = None) -> int:
    """Minutos desde medianoche en hora LOCAL → minutos desde medianoche en UTC.

    Se usa la fecha de HOY para que el desfase siga el horario de verano/invierno vigente."""
    local_mins = int(local_mins) % MINUTES_PER_DAY
    hh, mm = divmod(local_mins, 60)
    now = (now or _utcnow()).astimezone(tz)
    local_dt = now.replace(hour=hh, minute=mm, second=0, microsecond=0)
    utc_dt = local_dt.astimezone(datetime.timezone.utc)
    return utc_dt.hour * 60 + utc_dt.minute


def utc_minutes_to_local(utc_mins: int, tz: ZoneInfo, now: datetime.datetime | None = None) -> int:
    """La inversa: lo que guarda el coche → reloj de pared (para ENSEÑAR su programación)."""
    utc_mins = int(utc_mins) % MINUTES_PER_DAY
    hh, mm = divmod(utc_mins, 60)
    utc_dt = (now or _utcnow()).astimezone(datetime.timezone.utc).replace(hour=hh, minute=mm, second=0, microsecond=0)
    local_dt = utc_dt.astimezone(tz)
    return local_dt.hour * 60 + local_dt.minute


@dataclass(frozen=True)
class ChargeSchedule:
    """La programación de carga TAL COMO LA TIENE EL COCHE (puede haberla cambiado la app oficial)."""

    enabled: bool
    start_minutes: int | None      # reloj de pared local
    duration_minutes: int | None
    days: tuple[int, ...] = ()

    @property
    def end_minutes(self) -> int | None:
        if self.start_minutes is None or self.duration_minutes is None:
            return None
        return (self.start_minutes + self.duration_minutes) % MINUTES_PER_DAY


def _as_int(value) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def parse_schedule(payload: dict | None, tz: ZoneInfo, now: datetime.datetime | None = None) -> ChargeSchedule | None:
    """Respuesta de `chargeAppointQuery` → `ChargeSchedule`, o `None` si no es interpretable.

    `mainSwitch` (general) y `switchStatus` (del plan) son cosas distintas y AMBOS deben estar activos para que
    el coche cargue por horario. El coche devuelve una lista de planes; se toma el primero."""
    if not isinstance(payload, dict):
        return None
    planes = payload.get("chargeAppointPlans")
    plan = planes[0] if isinstance(planes, list) and planes and isinstance(planes[0], dict) else {}
    inicio = _as_int(plan.get("startTime"))
    dias = plan.get("cycleData")
    return ChargeSchedule(
        enabled=_as_int(payload.get("mainSwitch")) == 1 and _as_int(plan.get("switchStatus")) == 1,
        start_minutes=None if inicio is None else utc_minutes_to_local(inicio, tz, now),
        duration_minutes=_as_int(plan.get("timeConsuming")),
        days=tuple(int(d) for d in dias) if isinstance(dias, list) else (),
    )


def build_plan(start_minutes: int, duration_minutes: int, switch_status: int, tz: ZoneInfo,
               now: datetime.datetime | None = None) -> dict:
    """Un plan de `chargeAppointPlans`. `startTime` en UTC; la duración se fuerza al mínimo del coche."""
    return {
        "cycleData": ALL_WEEKDAYS,
        "startTime": local_minutes_to_utc(int(start_minutes), tz, now),
        "switchStatus": switch_status,
        "timeConsuming": max(CHARGE_MIN_DURATION_MIN, int(duration_minutes)),
    }


def build_stop_plan(now: datetime.datetime | None = None) -> dict:
    """Un plan cuya ventana YA terminó: ahora − 90 min con 60 min de duración → [ahora−90, ahora−30].

    «Ahora» queda fuera de la ventana y el coche deja de cargar. No se acorta la duración (el coche
    rechaza menos de 1 h): se desplaza el INICIO. `startTime` se calcula ya en UTC."""
    now_utc = (now or _utcnow()).astimezone(datetime.timezone.utc)
    start = (now_utc.hour * 60 + now_utc.minute - CHARGE_STOP_START_BACK_MIN) % MINUTES_PER_DAY
    return {"cycleData": ALL_WEEKDAYS, "startTime": int(start), "switchStatus": 1,
            "timeConsuming": CHARGE_STOP_DURATION_MIN}


def minutes_until_start(start_utc: int, now: datetime.datetime | None = None) -> int:
    """Minutos hasta la próxima apertura de una ventana que empieza a `start_utc` (minutos UTC del día)."""
    now_utc = (now or _utcnow()).astimezone(datetime.timezone.utc)
    return (int(start_utc) - (now_utc.hour * 60 + now_utc.minute)) % MINUTES_PER_DAY


def hold_needs_refresh(hold_start_utc: int | None, now: datetime.datetime | None = None,
                       margin: int = HOLD_REFRESH_MARGIN_MIN) -> bool:
    """¿La ventana de pausa está a punto de volver a abrirse (y el coche empezaría a cargar solo)?

    Sin dato del inicio, se renueva por prudencia."""
    if hold_start_utc is None:
        return True
    return minutes_until_start(hold_start_utc, now) < margin


def duration_between(start_minutes: int, end_minutes: int) -> int:
    """Duración entre una hora de inicio y una de fin (locales). Pasa de medianoche si hace falta; 0 = inválido."""
    return (int(end_minutes) - int(start_minutes)) % MINUTES_PER_DAY
