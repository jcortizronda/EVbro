"""Control de la carga usando SOLO la programación del coche.

**Por qué.** Este coche no acepta arrancar ni parar la carga (`chargeStartStopControl` → A00084). Lo único que
la nube de Ebro deja tocar es la programación (`chargeAppointControl`). Todo lo que ofrece la interfaz se
construye encima de eso:

  * **Parar carga** → programación cuya ventana ya terminó (ahora−90 min, 60 min de duración). Estando
    fuera de la ventana, el coche no carga. Esa ventana vuelve a abrirse al día siguiente a la misma hora:
    el gestor la desplaza solo antes de que llegue (`hold_needs_refresh`), de forma transparente.
  * **Iniciar carga** → quita la pausa y devuelve el coche a lo que tenía: su programación (la guardada en
    EVBro o, si «Parar» la sustituyó, la que el coche tenía puesta) o, si no había ninguna, apaga el interruptor
    general y el coche carga ya.
  * **Programar** → ventana de inicio/fin (o inicio/duración) escrita en el coche.
  * **Objetivo de batería** (< 100 %) → al alcanzarlo cargando, se pasa a «pausa por objetivo». Se libera sola
    cuando el coche está enchufado y la batería baja `HYSTERESIS` puntos por debajo del objetivo, y al subir
    el objetivo a 100 %. Con 100 % el gestor no hace nada.

**Seguridad de diseño.** Con los valores por defecto (objetivo 100 %, sin pausa ni programación) el gestor está
inerte: solo actúa cuando el usuario lo pide o fija un objetivo < 100 %. Los cambios AUTOMÁTICOS se espacian
`MIN_AUTO_GAP_MIN` minutos entre sí, también tras un fallo, para no martillear la nube de Chery con reintentos
(las órdenes del usuario no cuentan: tras programar, un objetivo alcanzado se atiende en la siguiente lectura).

**Verificación.** La nube acepta la orden (`A00079`) antes de que el coche la ejecute. Pasado `VERIFY_DELAY_S` se
lee la programación real y, si no coincide con lo esperado, se reenvía UNA vez (no se encadena otra verificación).

**Límites conocidos.** Si la programación se cambia desde la app oficial, el gestor no lo detecta hasta que la
lee (`GET .../charge/plan?live=1`). Un plan nuevo puede tardar unos segundos en verse en el coche.
"""
from __future__ import annotations

import asyncio
import dataclasses
import datetime
import logging
import time
from typing import Any, Awaitable, Callable, Optional

from sqlalchemy import select

from app.config import get_timezone
from app.core import commands
from app.db.models import ChargePlan
from app.db.session import async_session_factory
from app.vehicle import charging
from app.vehicle.constants import DEFAULT_CHARGE_DURATION_MIN, DEFAULT_CHARGE_START_MIN, MINUTES_PER_DAY

_LOGGER = logging.getLogger("app.services.charge_manager")

HYSTERESIS = 5            # puntos por debajo del objetivo para volver a permitir la carga
MIN_AUTO_GAP_MIN = 10     # separación mínima entre acciones automáticas del gestor
TICK_EVERY_S = 300        # cada cuánto se revisa que la ventana de pausa siga «cerrada»
TARGET_CHOICES = tuple(range(50, 101, 5))     # de 5 en 5; el objetivo NO se envía al coche: lo vigila EVBro
VERIFY_DELAY_S = 20        # espera antes de comprobar que el coche aplicó la orden
PENDING_WINDOW_S = 90      # durante este tiempo una lectura que no coincide se considera «aplicándose»

MODE_FREE, MODE_SCHEDULED, MODE_HOLD = "free", "scheduled", "hold"
HOLD_USER, HOLD_TARGET = "user", "target"


class ChargeError(Exception):
    """No se pudo aplicar el cambio. `reason` es estable (la web decide por él); `status` es el HTTP."""

    def __init__(self, reason: str, message: str, status: int = 422) -> None:
        super().__init__(message)
        self.reason, self.message, self.status = reason, message, status


@dataclasses.dataclass
class PlanState:
    target_pct: int = 100
    mode: str = MODE_FREE
    hold_reason: Optional[str] = None
    hold_start_utc: Optional[int] = None
    sched_enabled: bool = False
    sched_start_min: Optional[int] = None
    sched_duration_min: Optional[int] = None


def decide(state: PlanState, *, soc: Optional[float], plugged: bool, charging_now: bool,
           now: Optional[datetime.datetime] = None) -> Optional[str]:
    """Qué hacer ahora: `release`, `refresh_hold`, `hold_target` o `None`. Función pura.

    `soc` ≤ 0 o `None` = el coche no ha informado (con la alta tensión apagada manda 0): nunca cuenta como
    «objetivo alcanzado» ni como «batería baja»."""
    soc = soc if soc is not None and soc > 0 else None

    if state.mode == MODE_HOLD and state.hold_reason == HOLD_TARGET:
        if state.target_pct >= 100:
            return "release"                                   # se quitó el límite
        if plugged and soc is not None and soc <= state.target_pct - HYSTERESIS:
            return "release"                                   # hay hueco: se vuelve a permitir cargar
    if state.mode == MODE_HOLD and charging.hold_needs_refresh(state.hold_start_utc, now):
        return "refresh_hold"                                  # que la ventana de pausa no se reabra sola
    if (state.mode in (MODE_FREE, MODE_SCHEDULED) and state.target_pct < 100
            and charging_now and soc is not None and soc >= state.target_pct):
        return "hold_target"
    return None


def _hhmm(minutes: Optional[int]) -> Optional[str]:
    return None if minutes is None else f"{minutes // 60:02d}:{minutes % 60:02d}"


def status_code(state: PlanState, charging_now: bool) -> str:
    """Etiqueta estable para la interfaz."""
    if state.mode == MODE_HOLD:
        return "hold_target" if state.hold_reason == HOLD_TARGET else "hold_user"
    if state.mode == MODE_SCHEDULED:
        return "charging_scheduled" if charging_now else "scheduled"
    return "charging" if charging_now else "free"


class ChargeManager:
    _instance: Optional["ChargeManager"] = None

    @classmethod
    def get_instance(cls) -> "ChargeManager":
        if cls._instance is None:
            cls._instance = ChargeManager()
        return cls._instance

    def __init__(self, sender: Optional[Callable[..., Awaitable[dict]]] = None,
                 clock: Callable[[], datetime.datetime] = lambda: datetime.datetime.now(datetime.timezone.utc),
                 reader: Optional[Callable[[str], Awaitable[Optional[dict]]]] = None,
                 verify_delay_s: Optional[float] = VERIFY_DELAY_S) -> None:
        self._sender = sender or self._send_via_poller
        self._reader = reader or self._read_car_schedule
        self._verify_delay = verify_delay_s          # None = no verificar (tests)
        self._tasks: set[asyncio.Task] = set()
        self._clock = clock
        self._locks: dict[str, asyncio.Lock] = {}
        self._fast: dict[str, bool] = {}          # vehículos en pausa por objetivo: sondear más a menudo
        self._last_auto: dict[str, datetime.datetime] = {}   # última acción AUTOMÁTICA intentada (acierte o falle)
        self._last_tick = 0.0

    # ───────────────────────── infraestructura ─────────────────────────
    def _lock(self, vin: str) -> asyncio.Lock:
        return self._locks.setdefault(vin, asyncio.Lock())

    @staticmethod
    async def _send_via_poller(vin: str, key: str, params: dict, origin: str) -> dict:
        from app.services.poller import VehiclePoller
        return await VehiclePoller.get_instance().execute_internal(vin, key, params, origin)

    async def warm_up(self) -> None:
        """Al arrancar: qué vehículos están en pausa por objetivo (para sondear más fino)."""
        async with async_session_factory() as s:
            for row in (await s.execute(select(ChargePlan))).scalars():
                self._fast[row.vin] = row.mode == MODE_HOLD and row.hold_reason == HOLD_TARGET

    def wants_fast_poll(self, vin: str) -> bool:
        """En pausa por objetivo hay que enterarse pronto de que se enchufa el coche (el sondeo parado va cada hora)."""
        return self._fast.get(vin, False)

    @staticmethod
    async def _row(session, vin: str) -> ChargePlan:
        row = await session.get(ChargePlan, vin)
        if row is None:
            row = ChargePlan(vin=vin, target_pct=100, mode=MODE_FREE, sched_enabled=False)
            session.add(row)
        return row

    @staticmethod
    def _to_state(row: ChargePlan) -> PlanState:
        return PlanState(row.target_pct or 100, row.mode or MODE_FREE, row.hold_reason, row.hold_start_utc,
                         bool(row.sched_enabled), row.sched_start_min, row.sched_duration_min)

    # ───────────────────────── aplicar un plan al coche ─────────────────────────
    async def _apply(self, session, row: ChargePlan, kind: str, origin: str, reason: Optional[str] = None,
                     verify: bool = True) -> None:
        """Manda al coche el plan `kind` (hold | schedule | clear) y actualiza `row`. Lanza ChargeError si falla."""
        tz, now = get_timezone(), self._clock()
        if kind == "hold":
            plan = charging.build_stop_plan(now)
            key, params = "carga_prog_on", {"mainSwitch": 1, "chargeAppointPlans": [plan]}
        elif kind == "schedule":
            plan = charging.build_plan(row.sched_start_min, row.sched_duration_min, 1, tz, now)
            key, params = "carga_prog_on", {"mainSwitch": 1, "chargeAppointPlans": [plan]}
        else:  # clear: interruptor general apagado → el coche carga ya
            plan = charging.build_plan(row.sched_start_min if row.sched_start_min is not None else DEFAULT_CHARGE_START_MIN,
                                       row.sched_duration_min or DEFAULT_CHARGE_DURATION_MIN, 0, tz, now)
            key, params = "carga_prog_off", {"mainSwitch": 0, "chargeAppointPlans": [plan]}

        result = await self._sender(row.vin, key, params, origin)
        row.last_action, row.last_action_at = kind, now.replace(tzinfo=None)
        if not result.get("success"):
            row.last_error = result.get("message") or "El coche no aceptó la orden."
            await session.commit()
            raise ChargeError(result.get("reason") or "error", row.last_error,
                              424 if result.get("reason") == "reauth" else 422)
        row.last_error = None
        if kind == "hold":
            row.mode, row.hold_reason, row.hold_start_utc = MODE_HOLD, reason or HOLD_USER, plan["startTime"]
        else:
            row.mode = MODE_SCHEDULED if kind == "schedule" else MODE_FREE
            row.hold_reason = row.hold_start_utc = None
        self._fast[row.vin] = row.mode == MODE_HOLD and row.hold_reason == HOLD_TARGET
        await session.commit()
        if verify and self._verify_delay is not None:
            task = asyncio.create_task(self._verify_later(row.vin))
            self._tasks.add(task)
            task.add_done_callback(self._tasks.discard)

    @staticmethod
    def _expected_enabled(row: ChargePlan) -> bool:
        """¿Debe haber una programación activa en el coche? (la pausa también es una ventana activa)."""
        return row.mode in (MODE_HOLD, MODE_SCHEDULED)

    async def _verify_later(self, vin: str) -> None:
        try:
            await asyncio.sleep(self._verify_delay or 0)
            await self.verify_applied(vin)
        except Exception:                                    # noqa: BLE001 - nunca debe romper nada
            _LOGGER.exception("Gestor de carga (%s): error al verificar", vin)

    async def verify_applied(self, vin: str) -> bool:
        """Comprueba que el coche tiene lo que se le pidió; si no, reenvía UNA vez. Devuelve True si coincide."""
        car = await self._reader(vin)
        if car is None:
            return True                                      # no se pudo leer: no se toca nada
        async with self._lock(vin), async_session_factory() as s:
            row = await s.get(ChargePlan, vin)
            if row is None or bool(car.get("enabled")) == self._expected_enabled(row):
                return True
            kind = "hold" if row.mode == MODE_HOLD else ("schedule" if row.mode == MODE_SCHEDULED else "clear")
            _LOGGER.warning("Gestor de carga (%s): el coche no refleja «%s»; se reenvía una vez", vin, kind)
            await self._apply(s, row, kind, "automático (verificación)", row.hold_reason, verify=False)
        return False

    async def _release(self, session, row: ChargePlan, origin: str) -> None:
        """Sale de la pausa: vuelve a la programación del usuario si la tiene; si no, carga libre."""
        await self._apply(session, row, "schedule" if row.sched_enabled else "clear", origin)

    # ───────────────────────── acciones del usuario ─────────────────────────
    async def stop(self, vin: str, origin: str = "usuario") -> dict:
        """Parar carga: pausa manual (se mantiene hasta que el usuario pulse Iniciar o programe)."""
        async with self._lock(vin), async_session_factory() as s:
            row = await self._row(s, vin)
            if row.mode != MODE_HOLD and not row.sched_enabled:
                await self._remember_car_schedule(row)       # la que tenga puesta el coche, para devolvérsela al iniciar
            await self._apply(s, row, "hold", origin, HOLD_USER)
        return await self.state(vin)

    async def _remember_car_schedule(self, row: ChargePlan) -> None:
        """Si el coche tiene una programación activa que EVBro no conoce, se guarda antes de pisarla con la pausa."""
        try:
            car = await self._reader(row.vin)
        except Exception:                                    # noqa: BLE001 - es un extra: parar no debe fallar por esto
            return
        if car and car.get("enabled") and car.get("start") and car.get("duration_min"):
            h, m = car["start"].split(":")
            row.sched_enabled, row.sched_start_min, row.sched_duration_min = True, int(h) * 60 + int(m), int(car["duration_min"])

    async def start(self, vin: str, origin: str = "usuario") -> dict:
        """Iniciar carga: se quita la pausa y se vuelve a lo anterior: su programación si la había; si no, carga ya."""
        async with self._lock(vin), async_session_factory() as s:
            row = await self._row(s, vin)
            await self._release(s, row, origin)
        return await self.state(vin)

    async def set_schedule(self, vin: str, start_min: int, duration_min: int, origin: str = "usuario") -> dict:
        """Programa la carga: ventana de inicio/duración en el coche (días: todos)."""
        if duration_min < charging.CHARGE_MIN_DURATION_MIN:
            raise ChargeError("duration_too_short", "El coche exige al menos 1 hora de carga programada.")
        if duration_min >= MINUTES_PER_DAY:
            raise ChargeError("duration_too_long", "La duración debe ser menor de 24 horas.")
        async with self._lock(vin), async_session_factory() as s:
            row = await self._row(s, vin)
            row.sched_enabled, row.sched_start_min, row.sched_duration_min = True, int(start_min) % MINUTES_PER_DAY, int(duration_min)
            await self._apply(s, row, "schedule", origin)
        return await self.state(vin)

    async def clear_schedule(self, vin: str, origin: str = "usuario") -> dict:
        """Quita la programación (también la creada fuera de EVBro). Si hay pausa, se mantiene (no se carga)."""
        async with self._lock(vin), async_session_factory() as s:
            row = await self._row(s, vin)
            row.sched_enabled = False
            if row.mode == MODE_HOLD:
                await s.commit()
            else:
                await self._apply(s, row, "clear", origin)
        return await self.state(vin)

    async def set_target(self, vin: str, pct: int, origin: str = "usuario") -> dict:
        """Fija la batería objetivo (solo se guarda en EVBro; el coche no tiene esa función). 100 = sin límite.

        Solo se habla con el coche si hay que levantar una pausa por objetivo (al subirlo a 100 %) o, al evaluar
        la última lectura, si ya se ha alcanzado el nuevo objetivo y toca pausar."""
        if pct not in TARGET_CHOICES:
            raise ChargeError("target_invalid", f"El objetivo debe ser uno de {', '.join(map(str, TARGET_CHOICES))} %.")
        async with self._lock(vin), async_session_factory() as s:
            row = await self._row(s, vin)
            row.target_pct = pct
            if pct >= 100 and row.mode == MODE_HOLD and row.hold_reason == HOLD_TARGET:
                await self._release(s, row, origin)
            else:
                await s.commit()
        await self._evaluate_latest(vin)
        return await self.state(vin)

    # ───────────────────────── automático ─────────────────────────
    async def _evaluate_latest(self, vin: str) -> None:
        from app.services.poller import VehiclePoller
        latest = VehiclePoller.get_instance().get_latest(vin)
        if latest:
            await self.on_snapshot(vin, latest)

    async def on_snapshot(self, vin: str, telemetry: dict) -> None:
        """Se llama tras cada lectura del coche. Nunca lanza: un fallo aquí no debe romper el sondeo."""
        try:
            await self._auto(vin, telemetry)
        except ChargeError as err:
            _LOGGER.warning("Gestor de carga (%s): %s", vin, err.message)
        except Exception:                                    # noqa: BLE001
            _LOGGER.exception("Gestor de carga (%s): error inesperado", vin)

    async def _auto(self, vin: str, telemetry: dict) -> None:
        async with self._lock(vin), async_session_factory() as s:
            row = await s.get(ChargePlan, vin)
            if row is None:                                   # nunca se ha tocado: el gestor no existe para este coche
                return
            state = self._to_state(row)
            action = decide(state, soc=telemetry.get("battery_level"), plugged=bool(telemetry.get("cable_connected")),
                            charging_now=bool(telemetry.get("is_charging")), now=self._clock())
            if action is None:
                return
            now = self._clock()
            last = self._last_auto.get(vin)
            if last and (now - last) < datetime.timedelta(minutes=MIN_AUTO_GAP_MIN):
                return                       # solo entre acciones automáticas (también tras un fallo): no martillear la nube
            self._last_auto[vin] = now
            _LOGGER.info("Gestor de carga (%s): %s (objetivo %s%%, batería %s%%)", vin, action, state.target_pct,
                         telemetry.get("battery_level"))
            if action == "release":
                await self._release(s, row, "automático")
            elif action == "refresh_hold":
                await self._apply(s, row, "hold", "automático", row.hold_reason)
            elif action == "hold_target":
                await self._apply(s, row, "hold", "automático", HOLD_TARGET)

    async def tick(self) -> None:
        """Revisa periódicamente que la ventana de pausa no esté a punto de reabrirse. Barato: lee la BD."""
        if time.monotonic() - self._last_tick < TICK_EVERY_S:
            return
        self._last_tick = time.monotonic()
        async with async_session_factory() as s:
            vins = [r.vin for r in (await s.execute(select(ChargePlan).where(ChargePlan.mode == MODE_HOLD))).scalars()]
        for vin in vins:
            try:
                await self._auto(vin, {})                    # sin telemetría: solo puede salir `refresh_hold`
            except ChargeError as err:
                _LOGGER.warning("Gestor de carga (%s): %s", vin, err.message)
            except Exception:                                # noqa: BLE001
                _LOGGER.exception("Gestor de carga (%s): error inesperado", vin)

    # ───────────────────────── consulta ─────────────────────────
    async def state(self, vin: str, live: bool = False) -> dict[str, Any]:
        """Estado para la interfaz. `live=True` lee además la programación REAL del coche (red)."""
        async with async_session_factory() as s:
            row = await s.get(ChargePlan, vin)
        st = self._to_state(row) if row else PlanState()
        from app.services.poller import VehiclePoller
        poller = VehiclePoller.get_instance()
        latest = poller.get_latest(vin) or {}
        out: dict[str, Any] = {
            "target_pct": st.target_pct, "target_choices": list(TARGET_CHOICES),
            "mode": st.mode, "hold_reason": st.hold_reason,
            "status": status_code(st, bool(latest.get("is_charging"))),
            "schedule": {"enabled": st.sched_enabled, "start": _hhmm(st.sched_start_min),
                         "duration_min": st.sched_duration_min,
                         "end": _hhmm(None if st.sched_start_min is None or st.sched_duration_min is None
                                      else (st.sched_start_min + st.sched_duration_min) % MINUTES_PER_DAY)},
            "soc": latest.get("battery_level"), "plugged": latest.get("cable_connected"),
            "charging": latest.get("is_charging"),
            "last_action": row.last_action if row else None,
            "last_action_at": row.last_action_at.isoformat() if row and row.last_action_at else None,
            "last_error": row.last_error if row else None,
            "hold_refresh_in_min": (max(0, charging.minutes_until_start(st.hold_start_utc, self._clock())
                                        - charging.HOLD_REFRESH_MARGIN_MIN)
                                    if st.mode == MODE_HOLD and st.hold_start_utc is not None else None),
            "timezone": str(get_timezone()),
            "car_schedule": None,
        }
        out["car_pending"] = False
        if live:
            car = await self._reader(vin)
            out["car_schedule"] = car
            if car is not None and row is not None and row.last_action_at is not None:
                age = (self._clock().replace(tzinfo=None) - row.last_action_at).total_seconds()
                out["car_pending"] = age < PENDING_WINDOW_S and bool(car.get("enabled")) != self._expected_enabled(row)
        return out

    async def _read_car_schedule(self, vin: str) -> Optional[dict]:
        from app.services.poller import VehiclePoller
        ctx = VehiclePoller.get_instance()._get_or_create_ctx(vin)
        loop = asyncio.get_running_loop()
        payload = await loop.run_in_executor(None, commands.query_charge_schedule, ctx)
        sched = charging.parse_schedule(payload, get_timezone(), self._clock())
        if sched is None:
            return None
        return {"enabled": sched.enabled, "start": _hhmm(sched.start_minutes), "end": _hhmm(sched.end_minutes),
                "duration_min": sched.duration_minutes}
