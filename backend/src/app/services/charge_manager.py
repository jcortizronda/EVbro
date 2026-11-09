"""Charge control using ONLY the car's charging schedule.

**Why.** This car does not accept starting or stopping a charge (`chargeStartStopControl` → A00084). The only thing
the Ebro cloud lets us touch is the schedule (`chargeAppointControl`). Everything the interface offers is
built on top of that:

  * **Stop charging** → a schedule whose window has already ended (now−90 min, 60 min long). Being
    outside the window, the car does not charge. That window opens again the next day at the same time:
    the manager moves it on its own before it arrives (`hold_needs_refresh`), transparently.
  * **Start charging** → removes the pause and gives the car back what it had: its schedule (the one stored in
    EVBro or, if «Stop» replaced it, the one the car had set) or, if there was none, turns the main switch
    off and the car charges right away.
  * **Schedule** → a start/end (or start/duration) window written to the car.
  * **Battery target** (< 100 %) → when it is reached while charging, it switches to «paused at target». It releases
    itself when the car is plugged in and the battery drops `HYSTERESIS` points below the target, and when the
    target is raised to 100 %. At 100 % the manager does nothing.

**Safe by design.** With the defaults (target 100 %, no pause and no schedule) the manager is
inert: it only acts when the user asks or sets a target < 100 %. AUTOMATIC changes are spaced
`MIN_AUTO_GAP_MIN` minutes apart, also after a failure, so as not to hammer the Chery cloud with retries
(user commands do not count: after scheduling, a reached target is handled on the next reading).

**Verification.** The cloud accepts the command (`A00079`) before the car runs it. After `VERIFY_DELAY_S` the
real schedule is read and, if it does not match what was expected, it is resent ONCE (no further verification is chained).

**Known limits.** If the schedule is changed from the official app, the manager does not notice until it
reads it (`GET .../charge/plan?live=1`). A new plan may take a few seconds to show up in the car.
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
from app.db.models import ChargePlan, Vehicle
from app.db.session import async_session_factory
from app.services.charge_detector import grid_charging
from app.services.notification_service import NotificationService
from app.vehicle import charging
from app.vehicle.constants import DEFAULT_CHARGE_DURATION_MIN, DEFAULT_CHARGE_START_MIN, MINUTES_PER_DAY

_LOGGER = logging.getLogger("app.services.charge_manager")

HYSTERESIS = 5            # points below the target before charging is allowed again
MIN_AUTO_GAP_MIN = 10     # minimum gap between the manager's automatic actions
TICK_EVERY_S = 300        # how often to check that the pause window is still «closed»
TARGET_CHOICES = tuple(range(50, 101, 5))     # in steps of 5; the target is NOT sent to the car: EVBro watches it
VERIFY_DELAY_S = 20        # wait before checking that the car applied the command
PENDING_WINDOW_S = 90      # during this time a reading that does not match counts as «being applied»

MODE_FREE, MODE_SCHEDULED, MODE_HOLD = "free", "scheduled", "hold"
HOLD_USER, HOLD_TARGET = "user", "target"


class ChargeError(Exception):
    """The change could not be applied. `reason` is stable (the web acts on it); `status` is the HTTP status."""

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
    """What to do now: `release`, `refresh_hold`, `hold_target` or `None`. Pure function.

    `soc` ≤ 0 or `None` = the car has not reported (with high voltage off it sends 0): it never counts as
    «target reached» nor as «battery low»."""
    soc = soc if soc is not None and soc > 0 else None

    if state.mode == MODE_HOLD and state.hold_reason == HOLD_TARGET:
        if state.target_pct >= 100:
            return "release"                                   # the limit was removed
        if plugged and soc is not None and soc <= state.target_pct - HYSTERESIS:
            return "release"                                   # there is room: charging is allowed again
    if state.mode == MODE_HOLD and charging.hold_needs_refresh(state.hold_start_utc, now):
        return "refresh_hold"                                  # so the pause window does not reopen by itself
    if (state.mode in (MODE_FREE, MODE_SCHEDULED) and state.target_pct < 100
            and charging_now and soc is not None and soc >= state.target_pct):
        return "hold_target"
    return None


def _hhmm(minutes: Optional[int]) -> Optional[str]:
    return None if minutes is None else f"{minutes // 60:02d}:{minutes % 60:02d}"


def status_code(state: PlanState, charging_now: bool) -> str:
    """Stable label for the interface."""
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
        self._verify_delay = verify_delay_s          # None = do not verify (tests)
        self._tasks: set[asyncio.Task] = set()
        self._clock = clock
        self._locks: dict[str, asyncio.Lock] = {}
        self._holds: dict[str, Optional[str]] = {}   # why EVBro is holding each car's charge (None = it is not)
        self._last_auto: dict[str, datetime.datetime] = {}   # last AUTOMATIC action attempted (successful or not)
        self._hold_failed_told: set[str] = set()     # cars whose manager already heard that the pause at the target failed
        self._last_tick = 0.0

    # ───────────────────────── infrastructure ─────────────────────────
    def _lock(self, vin: str) -> asyncio.Lock:
        return self._locks.setdefault(vin, asyncio.Lock())

    @staticmethod
    async def _send_via_poller(vin: str, key: str, params: dict, origin: str) -> dict:
        from app.services.poller import VehiclePoller
        return await VehiclePoller.get_instance().execute_internal(vin, key, params, origin)

    async def warm_up(self) -> None:
        """At start-up: which vehicles are paused at the target (to poll them more closely)."""
        async with async_session_factory() as s:
            for row in (await s.execute(select(ChargePlan))).scalars():
                self._remember_hold(row)

    def _remember_hold(self, row: ChargePlan) -> None:
        self._holds[row.vin] = (row.hold_reason or HOLD_USER) if row.mode == MODE_HOLD else None

    def wants_fast_poll(self, vin: str) -> bool:
        """Paused at the target we must learn early that the car is plugged in (the parked poll runs every hour)."""
        return self._holds.get(vin) == HOLD_TARGET

    def holds_charge(self, vin: str) -> bool:
        """EVBro itself stopped this car's charge (target reached or «Parar carga»): the stop is no interruption."""
        return self._holds.get(vin) is not None

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

    # ───────────────────────── applying a plan to the car ─────────────────────────
    async def _apply(self, session, row: ChargePlan, kind: str, origin: str, reason: Optional[str] = None,
                     verify: bool = True) -> None:
        """Sends the `kind` plan (hold | schedule | clear) to the car and updates `row`. Raises ChargeError on failure."""
        tz, now = get_timezone(), self._clock()
        if kind == "hold":
            plan = charging.build_stop_plan(now)
            key, params = "carga_prog_on", {"mainSwitch": 1, "chargeAppointPlans": [plan]}
        elif kind == "schedule":
            plan = charging.build_plan(row.sched_start_min, row.sched_duration_min, 1, tz, now)
            key, params = "carga_prog_on", {"mainSwitch": 1, "chargeAppointPlans": [plan]}
        else:  # clear: main switch off → the car charges right away
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
        self._remember_hold(row)
        await session.commit()
        if verify and self._verify_delay is not None:
            task = asyncio.create_task(self._verify_later(row.vin))
            self._tasks.add(task)
            task.add_done_callback(self._tasks.discard)

    @staticmethod
    def _expected_enabled(row: ChargePlan) -> bool:
        """Should the car have an active schedule? (the pause is an active window too)."""
        return row.mode in (MODE_HOLD, MODE_SCHEDULED)

    async def _verify_later(self, vin: str) -> None:
        try:
            await asyncio.sleep(self._verify_delay or 0)
            await self.verify_applied(vin)
        except Exception:                                    # noqa: BLE001 - must never break anything
            _LOGGER.exception("Charge manager (%s): error while verifying", vin)

    async def verify_applied(self, vin: str) -> bool:
        """Checks that the car has what it was asked for; otherwise resends ONCE. Returns True when it matches."""
        car = await self._reader(vin)
        if car is None:
            return True                                      # could not read it: nothing is touched
        async with self._lock(vin), async_session_factory() as s:
            row = await s.get(ChargePlan, vin)
            if row is None or bool(car.get("enabled")) == self._expected_enabled(row):
                return True
            kind = "hold" if row.mode == MODE_HOLD else ("schedule" if row.mode == MODE_SCHEDULED else "clear")
            _LOGGER.warning("Charge manager (%s): the car does not show «%s»; resending once", vin, kind)
            await self._apply(s, row, kind, "automático (verificación)", row.hold_reason, verify=False)
        return False

    async def _release(self, session, row: ChargePlan, origin: str) -> None:
        """Leaves the pause: back to the user's schedule if there is one; otherwise free charging."""
        await self._apply(session, row, "schedule" if row.sched_enabled else "clear", origin)

    # ───────────────────────── user actions ─────────────────────────
    async def stop(self, vin: str, origin: str = "usuario") -> dict:
        """Stop charging: manual pause (kept until the user presses Start or sets a schedule)."""
        async with self._lock(vin), async_session_factory() as s:
            row = await self._row(s, vin)
            if row.mode != MODE_HOLD and not row.sched_enabled:
                await self._remember_car_schedule(row)       # whatever the car has set, to give it back on start
            await self._apply(s, row, "hold", origin, HOLD_USER)
        return await self.state(vin)

    async def _remember_car_schedule(self, row: ChargePlan) -> None:
        """If the car has an active schedule EVBro does not know about, it is stored before the pause overwrites it."""
        try:
            car = await self._reader(row.vin)
        except Exception:                                    # noqa: BLE001 - an extra: stopping must not fail because of this
            return
        if car and car.get("enabled") and car.get("start") and car.get("duration_min"):
            h, m = car["start"].split(":")
            row.sched_enabled, row.sched_start_min, row.sched_duration_min = True, int(h) * 60 + int(m), int(car["duration_min"])

    async def start(self, vin: str, origin: str = "usuario") -> dict:
        """Start charging: removes the pause and goes back to what was there: its schedule if any; otherwise it charges now."""
        async with self._lock(vin), async_session_factory() as s:
            row = await self._row(s, vin)
            await self._release(s, row, origin)
        return await self.state(vin)

    async def set_schedule(self, vin: str, start_min: int, duration_min: int, origin: str = "usuario") -> dict:
        """Schedules the charge: a start/duration window in the car (days: all)."""
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
        """Removes the schedule (also one created outside EVBro). If paused, the pause stays (no charging)."""
        async with self._lock(vin), async_session_factory() as s:
            row = await self._row(s, vin)
            row.sched_enabled = False
            if row.mode == MODE_HOLD:
                await s.commit()
            else:
                await self._apply(s, row, "clear", origin)
        return await self.state(vin)

    async def set_target(self, vin: str, pct: int, origin: str = "usuario") -> dict:
        """Sets the battery target (stored in EVBro only; the car has no such feature). 100 = no limit.

        The car is only contacted to lift a pause at the target (when raised to 100 %) or, when evaluating
        the latest reading, if the new target is already reached and it is time to pause."""
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

    # ───────────────────────── automatic ─────────────────────────
    async def _evaluate_latest(self, vin: str) -> None:
        from app.services.poller import VehiclePoller
        latest = VehiclePoller.get_instance().get_latest(vin)
        if latest:
            await self.on_snapshot(vin, latest)

    async def on_snapshot(self, vin: str, telemetry: dict) -> None:
        """Called after each reading of the car. Never raises: a failure here must not break the polling."""
        try:
            await self._auto(vin, telemetry)
        except ChargeError as err:
            _LOGGER.warning("Charge manager (%s): %s", vin, err.message)
        except Exception:                                    # noqa: BLE001
            _LOGGER.exception("Charge manager (%s): unexpected error", vin)

    async def _auto(self, vin: str, telemetry: dict) -> None:
        async with self._lock(vin), async_session_factory() as s:
            row = await s.get(ChargePlan, vin)
            if row is None:                                   # never touched: the manager does not exist for this car
                return
            state = self._to_state(row)
            action = decide(state, soc=telemetry.get("battery_level"), plugged=bool(telemetry.get("cable_connected")),
                            charging_now=grid_charging(telemetry), now=self._clock())
            if action is None:
                return
            now = self._clock()
            last = self._last_auto.get(vin)
            if last and (now - last) < datetime.timedelta(minutes=MIN_AUTO_GAP_MIN):
                return                       # only between automatic actions (also after a failure): do not hammer the cloud
            self._last_auto[vin] = now
            _LOGGER.info("Charge manager (%s): %s (target %s%%, battery %s%%)", vin, action, state.target_pct,
                         telemetry.get("battery_level"))
            if action == "release":
                await self._release(s, row, "automático")
            elif action == "refresh_hold":
                await self._apply(s, row, "hold", "automático", row.hold_reason)
            elif action == "hold_target":
                try:
                    await self._apply(s, row, "hold", "automático", HOLD_TARGET)
                except ChargeError as err:
                    await self._tell_hold_failed(s, vin, state.target_pct, err.message)
                    raise
                self._hold_failed_told.discard(vin)

    async def _tell_hold_failed(self, session, vin: str, target_pct: int, detail: str) -> None:
        """The target was reached but the pause did not get to the car (no stored PIN, a dead session, the car said no): it keeps
        charging and whoever set the target must know. Once per failure streak: a retry comes every few minutes (09-11-2026: a
        car charged past its target for lack of a stored PIN and nobody was told)."""
        if vin in self._hold_failed_told:
            return
        self._hold_failed_told.add(vin)
        name = (await session.execute(select(Vehicle.name).where(Vehicle.vin == vin))).scalar_one_or_none() or vin[-6:]
        try:
            await NotificationService.notify_charge_hold_failed(name, target_pct, detail, vin=vin)
        except Exception:                                    # noqa: BLE001 — telling is secondary to the charge control itself
            _LOGGER.exception("Charge manager (%s): the hold failure could not be notified", vin)

    async def tick(self) -> None:
        """Periodically checks that the pause window is not about to reopen. Cheap: it reads the DB."""
        if time.monotonic() - self._last_tick < TICK_EVERY_S:
            return
        self._last_tick = time.monotonic()
        async with async_session_factory() as s:
            vins = [r.vin for r in (await s.execute(select(ChargePlan).where(ChargePlan.mode == MODE_HOLD))).scalars()]
        for vin in vins:
            try:
                await self._auto(vin, {})                    # no telemetry: only `refresh_hold` can come out
            except ChargeError as err:
                _LOGGER.warning("Charge manager (%s): %s", vin, err.message)
            except Exception:                                # noqa: BLE001
                _LOGGER.exception("Charge manager (%s): unexpected error", vin)

    # ───────────────────────── query ─────────────────────────
    async def state(self, vin: str, live: bool = False) -> dict[str, Any]:
        """State for the interface. `live=True` also reads the car's REAL schedule (network)."""
        async with async_session_factory() as s:
            row = await s.get(ChargePlan, vin)
        st = self._to_state(row) if row else PlanState()
        from app.services.poller import VehiclePoller
        poller = VehiclePoller.get_instance()
        latest = poller.get_latest(vin) or {}
        out: dict[str, Any] = {
            "target_pct": st.target_pct, "target_choices": list(TARGET_CHOICES),
            "mode": st.mode, "hold_reason": st.hold_reason,
            "status": status_code(st, grid_charging(latest)),
            "schedule": {"enabled": st.sched_enabled, "start": _hhmm(st.sched_start_min),
                         "duration_min": st.sched_duration_min,
                         "end": _hhmm(None if st.sched_start_min is None or st.sched_duration_min is None
                                      else (st.sched_start_min + st.sched_duration_min) % MINUTES_PER_DAY)},
            "soc": latest.get("battery_level"), "plugged": latest.get("cable_connected"),
            "charging": grid_charging(latest) if latest else None,
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
