"""Alerts of the poller: geofences, charging, fuel and battery, windows and locks, tyre pressure and faults, from each reading.

Part of `VehiclePoller` (services/poller.py), split out on 2026-11-09 without changing behaviour.
"""
from __future__ import annotations

import asyncio
import datetime
import logging
from typing import Any, Dict, Iterable, List, Optional, Set

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import AlertState, Geofence, GeofenceEvent, Vehicle
from app.db.session import async_session_factory
from app.i18n import M
from app.services.charge_detector import grid_charging
from app.services.charge_manager import ChargeManager
from app.services.geofence import find_geofence, haversine_distance_meters
from app.services.notification_service import NotificationService
from app.timeutil import utcnow

_LOGGER = logging.getLogger("app.services.poller")

# The position of a parked car, as the cloud gives it, wanders: one charging in a 50 m geofence was placed 6, 58, 28, 101, 6
# and 88 m from its centre within half an hour (23-11-2026), and every swing was an exit and an entry. Without a sign that the
# car moved, only a position this far beyond the edge counts as leaving.
GEOFENCE_EXIT_MARGIN_METERS = 300
# How long «car left with doors/windows open» or unlocked must last before alerting
SECURITY_CONFIRM_SECONDS = 5 * 60
# Tyre pressure: it must stay out of range this long (when starting off or with a warm tyre it rises 10 % without being a problem);
# the alert is sent once per tyre until every pressure has been back in range for as long
TYRE_CONFIRM_SECONDS = 10 * 60
# A LOW pressure only has to hold this long: the cold reading is the one that matters and it lasts a few minutes, because rolling
# warms the tyre and raises it (03 and 04-11-2026: 2.31 bar for 2-4 minutes, never 10). A lone reading of one sensor is not enough.
TYRE_LOW_CONFIRM_SECONDS = 2 * 60
# Low fuel alerts again only after the level has been this many points above the threshold (a refuel, not the gauge swaying)
FUEL_REARM_MARGIN = 5

# Window and sunroof positions the car reports (`frontLeftWindowState`…), measured on a car on 18-11-2026: 0 closed; 1 and 2 the
# gap «Ventilar ventanillas» leaves (1, 2, 2, 2; on 28-10-2026 it was 1, 1, 1, 1); fully open with «Abrir ventanillas», 3 on the
# driver's window and 10 on the other three (in the whole server the driver's never reads 2 or 10, and the others hardly ever 3).
# Any other value that is not 0 counts as open too: better one alert too many than a window left open unnoticed.
WINDOW_AJAR, WINDOW_OPEN = "ajar", "open"
WINDOW_AJAR_VALUES = (1, 2)
# What the notice says for each window (and the sunroof) left ajar or open: whole sentences, like the doors below
WINDOW_TEXT = {
    "fl": {WINDOW_AJAR: M("Ventanilla delantera izquierda entreabierta."), WINDOW_OPEN: M("Ventanilla delantera izquierda abierta.")},
    "fr": {WINDOW_AJAR: M("Ventanilla delantera derecha entreabierta."), WINDOW_OPEN: M("Ventanilla delantera derecha abierta.")},
    "rl": {WINDOW_AJAR: M("Ventanilla trasera izquierda entreabierta."), WINDOW_OPEN: M("Ventanilla trasera izquierda abierta.")},
    "rr": {WINDOW_AJAR: M("Ventanilla trasera derecha entreabierta."), WINDOW_OPEN: M("Ventanilla trasera derecha abierta.")},
    "sunroof": {WINDOW_AJAR: M("Techo solar entreabierto."), WINDOW_OPEN: M("Techo solar abierto.")},
}
# What the notice says for each thing left open (the keys of `doors` in core/telemetry.py). Until 2026-11-22 the notice
# printed the keys themselves: «Puertas abiertas: fl».
DOOR_OPEN = {"fl": M("Puerta delantera izquierda abierta."), "fr": M("Puerta delantera derecha abierta."),
             "rl": M("Puerta trasera izquierda abierta."), "rr": M("Puerta trasera derecha abierta."),
             "trunk": M("Maletero abierto."), "hood": M("Capó abierto.")}


def open_windows(telemetry: Dict) -> Dict[str, str]:
    """Windows (and sunroof) the reading shows ajar or open: {"fl": "ajar", "sunroof": "open"}. The sunshade is not an opening."""
    out: Dict[str, str] = {}
    for key, value in (telemetry.get("windows") or {}).items():
        try:
            position = int(value) if value is not None else 0
        except (TypeError, ValueError):
            position = 0                                   # no data is not an opening
        if key in WINDOW_TEXT and position:
            out[key] = WINDOW_AJAR if position in WINDOW_AJAR_VALUES else WINDOW_OPEN
    return out


class AlertsMixin:
    """Methods of `VehiclePoller`; the state they use is declared there."""

    async def _check_and_dispatch_alerts(
        self, session: AsyncSession, vehicle: Vehicle, telemetry: Dict
    ) -> None:
        """Checks state transitions and sends alerts to Android and iOS."""
        vin = vehicle.vin
        lat = telemetry.get("latitude")
        lon = telemetry.get("longitude")
        battery = telemetry.get("battery_level")
        odo = telemetry.get("odometer_km")
        fuel = telemetry.get("fuel_level")
        fuel_range = telemetry.get("fuel_range_km")

        # What each account wants to receive is decided when sending (per account); here only the car's own settings matter.
        # Every alert about a situation that lasts (windows, locking, fuel, faults, tyres, services, renewals) is sent ONCE per
        # episode: `sent` holds the ones already notified and still going on (table `alert_states`).
        sent = await self._alert_keys(session, vin)

        # 1. Geofence alerts (enter / exit - configurable per user)
        if lat is not None and lon is not None:
            current_gf = await find_geofence(session, lat, lon, owner_id=vehicle.owner_id)
            current_gf_id = current_gf.id if current_gf else None

            if vin not in self._last_geofence_by_vin:
                # Initialise the state without a spurious alert on the first start
                self._last_geofence_by_vin[vin] = current_gf_id
            else:
                last_gf_id = self._last_geofence_by_vin.get(vin)
                if current_gf_id != last_gf_id:
                    if last_gf_id is not None and not await self._left_geofence(session, vin, last_gf_id, telemetry, lat, lon):
                        current_gf_id = last_gf_id                # the position wandered, the car did not: it is still inside
                    else:
                        await self._geofence_transition(session, vehicle, current_gf, last_gf_id, lat, lon, battery, odo)
                        self._last_geofence_by_vin[vin] = current_gf_id
            if current_gf_id is not None and odo is not None:
                self._geofence_odometer_by_vin[vin] = odo

        # 2. Charging alerts (start / end / interruption); from the grid only, the engine charging on the road is not news
        is_charging = grid_charging(telemetry)
        if vin in self._last_charging_by_vin:
            was_charging = self._last_charging_by_vin[vin]
            if not was_charging and is_charging:
                await NotificationService.notify_charging_started(
                    vehicle.name, battery or 0.0, telemetry.get("electric_range_km"), vin=vin
                )
            elif was_charging and not is_charging:
                # If it stopped before completing (< 95%) and EVbro did not stop it itself (target reached, «Parar carga»)
                if battery is not None and battery < 95 and not ChargeManager.get_instance().holds_charge(vin):
                    await NotificationService.notify_charge_interrupted(
                        vehicle.name, battery, "Suministro eléctrico cortado o manguera desconectada.", vin=vin
                    )
                else:
                    await NotificationService.notify_charging_completed(
                        vehicle.name, battery or 0.0, telemetry.get("electric_range_km"), vin=vin
                    )
        self._last_charging_by_vin[vin] = is_charging

        # 3. Low fuel / reserve alert
        if fuel is not None:
            threshold = vehicle.fuel_low_threshold or 15            # the car's own threshold (a car setting, not an account one)
            if fuel <= threshold:
                if await self._fresh_alerts(session, vin, sent, "fuel", ["low"]):
                    await NotificationService.notify_fuel_low(vehicle.name, fuel, fuel_range, vin=vin)
            elif fuel > threshold + FUEL_REARM_MARGIN:
                await self._fresh_alerts(session, vin, sent, "fuel", [])

        # 4. Maintenance alerts (EBRO's official plan or custom tasks, depending on the car's mode).
        #    Checked every 200 km or every 6 h (month-based deadlines expire without the car moving). Each alert is sent once
        #    when it comes close and once when it is due (the status is part of its key).
        if odo is not None:
            now = utcnow()
            last_km = self._last_maint_check_km.get(vin, 0.0)
            last_at = self._last_maint_check_at.get(vin)
            if abs(odo - last_km) >= 200 or not last_at or (now - last_at).total_seconds() >= 6 * 3600:
                self._last_maint_check_km[vin] = odo
                self._last_maint_check_at[vin] = now
                try:
                    from app.services.maintenance_alerts import collect_alerts
                    async with async_session_factory() as m_session:
                        pending = await collect_alerts(m_session, vin, odo, now)
                    fresh = await self._fresh_alerts(session, vin, sent, "maintenance", [a["key"] for a in pending])
                    for alert in pending:
                        if alert["key"] not in fresh:
                            continue
                        await NotificationService.notify_maintenance_alert(
                            vehicle.name, alert["label"], alert["remaining_km"], alert["is_overdue"],
                            alert["items"], alert["labor_hours"], remaining_days=alert["remaining_days"], vin=vin,
                        )
                except Exception as ex:
                    _LOGGER.debug(f"Warning while checking maintenance alerts: {ex}")
                # EVbro's own recommendations (brakes, old petrol, tyres, 12 V battery, wipers: services/maintenance_advice.py):
                # one notice when one comes due, and a single notice for all when several do at once. The first look at a car is
                # silent (SEEN_MARK): what was already due that day is listed in Mantenimiento, only what comes due later notifies
                try:
                    from app.services import maintenance_advice
                    async with async_session_factory() as a_session:
                        due = await maintenance_advice.due_alerts(a_session, vin, now, odo)
                    first_look = f"advice:{maintenance_advice.SEEN_MARK}" not in sent
                    names = [maintenance_advice.SEEN_MARK] + [a["alert"] for a in due]
                    fresh = set(await self._fresh_alerts(session, vin, sent, "advice", names))
                    new = [] if first_look else [a for a in due if a["alert"] in fresh]
                    if new:
                        await NotificationService.notify_maintenance_advice(vehicle.name, maintenance_advice.notice(new), len(new), vin=vin)
                except Exception as ex:
                    _LOGGER.debug(f"Warning while checking maintenance recommendations: {ex}")

        # 4b. Dated renewals (insurance, taxes…): checked every 6 h; one alert 30 days before, one in the last week and one once
        #     overdue (the band is part of its key)
        now = utcnow()
        last_r = self._last_renewal_check_at.get(vin)
        if not last_r or (now - last_r).total_seconds() >= 6 * 3600:
            self._last_renewal_check_at[vin] = now
            try:
                from app.services.renewal_alerts import collect_renewals
                async with async_session_factory() as r_session:
                    due = await collect_renewals(r_session, vin, now)
                fresh = await self._fresh_alerts(session, vin, sent, "renewal", [a["key"] for a in due])
                for alert in due:
                    if alert["key"] in fresh:
                        await NotificationService.notify_renewal(vehicle.name, alert["label"], alert["remaining_days"], vin=vin)
            except Exception as ex:
                _LOGGER.debug(f"Warning while checking renewals: {ex}")

        # «The car has been left»: stopped AND switched off (high voltage at 0; without that signal, just stopped). With the car on
        # (traffic light, you just arrived and are getting out) there is no alert about doors or locking.
        left_car = telemetry.get("state") == "parked" and telemetry.get("high_voltage_on") is not True

        # 5. Windows / sunroof left open when parking (the situation must last SECURITY_CONFIRM_SECONDS). One alert per episode:
        #    it ends when they are closed or the car drives off. Up to 18-11-2026 it repeated every 30 minutes for as long as the
        #    car reported it (15 alerts in one night for a front window the car read as ajar).
        driving = telemetry.get("state") == "driving"
        opened = open_windows(telemetry)
        # Off as well as locked: the car locks itself on the road, and a window lowered while driving is no forgotten window
        # (every alert up to 05-11-2026 came with the car moving). The car may also raise them when locked: hence the wait.
        is_parked = telemetry.get("high_voltage_on") is not True and (left_car or telemetry.get("is_locked") is True)
        windows_confirmed = self._confirmed(vin, "windows", is_parked and bool(opened), utcnow())
        if not opened or driving:
            await self._fresh_alerts(session, vin, sent, "windows", [])
        elif windows_confirmed and await self._fresh_alerts(session, vin, sent, "windows", list(opened)):
            desc = [WINDOW_TEXT[key][position] for key, position in opened.items()]
            await NotificationService.notify_windows_open_alert(vehicle.name, desc, vin=vin)

        # 6. Vehicle fault / DTC alerts
        faults = telemetry.get("dtcs") or telemetry.get("fault_messages") or []
        if await self._fresh_alerts(session, vin, sent, "fault", [str(f) for f in faults]):
            await NotificationService.notify_vehicle_fault(vehicle.name, ", ".join(str(f) for f in faults), vin=vin)

        # 7. Security alerts when parked (doors open or not locked). They used to fire on the first «parked» reading
        #    (you arrive, open the door and get out: false alarm); now the car must be off and the situation must last
        #    SECURITY_CONFIRM_SECONDS. One alert per episode: it ends when the car is locked and closed, or drives off.
        issues: Dict[str, List[Any]] = {}                 # kind of problem → the lines of the notice (one per door)
        if left_car:
            if telemetry.get("is_locked") is False:
                issues["unlocked"] = [M("Vehículo sin cerrar con llave.")]
            open_doors = [k for k, v in (telemetry.get("doors") or {}).items() if v]
            if open_doors:
                issues["doors"] = [DOOR_OPEN.get(k) or M("Puerta abierta: {p0}", p0=k) for k in open_doors]
        security_confirmed = self._confirmed(vin, "security", bool(issues), utcnow())
        if driving or (left_car and not issues):
            await self._fresh_alerts(session, vin, sent, "security", [])
        elif security_confirmed and await self._fresh_alerts(session, vin, sent, "security", list(issues)):
            await NotificationService.notify_security_alert(vehicle.name, [line for lines in issues.values() for line in lines], vin=vin)

        # 8. Tyre pressure outside the range set on the vehicle
        await self._check_tyre_pressure(session, vehicle, telemetry, sent)

    @staticmethod
    def tyre_pressure_deviations(vehicle: Vehicle, telemetry: Dict) -> Dict[str, str]:
        """Tyres whose pressure differs from the vehicle's reference (front/rear) by more than the configured %:
        {"fr:low": "Delantero derecho: 2.10 bar (…)"}."""
        ref = {"fl": vehicle.tyre_front_pressure_bar, "fr": vehicle.tyre_front_pressure_bar,
               "rl": vehicle.tyre_rear_pressure_bar, "rr": vehicle.tyre_rear_pressure_bar}
        names = {"fl": "Delantero izquierdo", "fr": "Delantero derecho", "rl": "Trasero izquierdo", "rr": "Trasero derecho"}
        tol = vehicle.tyre_pressure_tolerance_pct or 10.0
        out: Dict[str, str] = {}
        for key, pressure in (telemetry.get("tyre_pressures") or {}).items():
            target = ref.get(key)
            if not target or not pressure or pressure <= 0:
                continue
            dev = (pressure - target) / target * 100.0
            if abs(dev) > tol:
                out[f"{key}:{'low' if dev < 0 else 'high'}"] = (
                    f"{names.get(key, key)}: {pressure:.2f} bar ({dev:+.0f} % sobre los {target:.2f} bar de referencia)")
        return out

    @classmethod
    def tyre_pressure_issues(cls, vehicle: Vehicle, telemetry: Dict) -> list:
        """The texts of `tyre_pressure_deviations`."""
        return list(cls.tyre_pressure_deviations(vehicle, telemetry).values())

    async def _left_geofence(self, session, vin: str, last_gf_id: int, telemetry: Dict, lat: float, lon: float) -> bool:
        """Whether a reading outside the geofence the car was in is a real exit. Needs a sign that the car moved: it is on the
        road, its odometer went up since it was last seen inside, or the position is well beyond the edge. Never while plugged
        in (it cannot be driving), unless the odometer went up: the cloud can keep the cable «connected» on the road
        (18-11-2026)."""
        odo, odo_inside = telemetry.get("odometer_km"), self._geofence_odometer_by_vin.get(vin)
        if odo is not None and odo_inside is not None and odo > odo_inside:
            return True
        if telemetry.get("cable_connected"):
            return False
        if (telemetry.get("speed_kmh") or 0.0) > 2.0 or telemetry.get("state") == "driving":
            return True
        prev_gf = await session.get(Geofence, last_gf_id)
        if prev_gf is None:
            return True
        return haversine_distance_meters(lat, lon, prev_gf.latitude, prev_gf.longitude) > prev_gf.radius_meters + GEOFENCE_EXIT_MARGIN_METERS

    async def _geofence_transition(self, session, vehicle: Vehicle, current_gf, last_gf_id, lat, lon, battery, odo) -> None:
        """The car changed geofence: record the exit and/or entry (always) and notify (per geofence switch)."""
        now = utcnow()
        vin = vehicle.vin
        if last_gf_id is not None:
            # It just left the previous geofence (also when it went straight into another one)
            prev_gf = await session.get(Geofence, last_gf_id)
            if prev_gf is not None:
                session.add(GeofenceEvent(vin=vin, geofence_id=prev_gf.id, geofence_name=prev_gf.name, kind="exit", timestamp=now,
                                          latitude=lat, longitude=lon, battery_pct=battery, odometer_km=odo))
                if prev_gf.notify_exit is not False:
                    await NotificationService.notify_geofence_exit(vehicle.name, prev_gf.name, battery, odo, vin=vin)
        if current_gf is not None:
            session.add(GeofenceEvent(vin=vin, geofence_id=current_gf.id, geofence_name=current_gf.name, kind="enter", timestamp=now,
                                      latitude=lat, longitude=lon, battery_pct=battery, odometer_km=odo))
            # Each geofence says whether it wants the alert (the account's own switches filter again when sending)
            if current_gf.notify_enter is not False:
                await NotificationService.notify_geofence_enter(vehicle.name, current_gf.name, battery, odo, vin=vin)
        await session.commit()

    async def _check_tyre_pressure(self, session: AsyncSession, vehicle: Vehicle, telemetry: Dict, sent: Optional[Set[str]] = None) -> None:
        """Alerts when a tyre stays out of the configured range TYRE_CONFIRM_SECONDS, or below it TYRE_LOW_CONFIRM_SECONDS. One alert
        per tyre and episode: the episode ends when every pressure has been back in range for TYRE_CONFIRM_SECONDS (a pressure
        that sways around the limit does not alert again and again)."""
        vin = vehicle.vin
        if sent is None:
            sent = await self._alert_keys(session, vin)
        deviations = self.tyre_pressure_deviations(vehicle, telemetry)
        low = any(key.endswith(":low") for key in deviations)
        has_data = any(p and p > 0 for p in (telemetry.get("tyre_pressures") or {}).values())
        episode = any(k.startswith("tyres:") for k in sent)
        now = utcnow()
        low_confirmed = self._confirmed(vin, "tyres_low", low, now, TYRE_LOW_CONFIRM_SECONDS)
        out_confirmed = self._confirmed(vin, "tyres", bool(deviations), now, TYRE_CONFIRM_SECONDS)
        if self._confirmed(vin, "tyres_ok", episode and has_data and not deviations, now, TYRE_CONFIRM_SECONDS):
            await self._fresh_alerts(session, vin, sent, "tyres", [])
        elif (out_confirmed or low_confirmed) and await self._fresh_alerts(session, vin, sent, "tyres", list(deviations), forget_stale=False):
            await NotificationService.notify_tyre_pressure(vehicle.name, list(deviations.values()), vin=vin)

    @staticmethod
    async def _alert_keys(session: AsyncSession, vin: str) -> Set[str]:
        """The alerts already sent for this car whose situation is still going on."""
        return set((await session.execute(select(AlertState.key).where(AlertState.vin == vin))).scalars().all())

    @staticmethod
    async def _fresh_alerts(session: AsyncSession, vin: str, sent: Set[str], group: str, active: Iterable[str],
                            forget_stale: bool = True) -> List[str]:
        """Which of the `active` situations of `group` have not been notified yet. They are recorded as notified (in `sent` and in
        the table, with the caller's transaction) and, unless `forget_stale` is off, the recorded ones that are no longer active
        are forgotten, so they alert again the next time they happen. An empty `active` closes the whole group."""
        prefix = f"{group}:"
        names = list(dict.fromkeys(str(name)[:120] for name in active))
        wanted = {prefix + name for name in names}
        stale = {key for key in sent if key.startswith(prefix)} - wanted if forget_stale else set()
        if stale:
            await session.execute(delete(AlertState).where(AlertState.vin == vin, AlertState.key.in_(stale)))
            sent -= stale
        fresh = [name for name in names if prefix + name not in sent]
        for name in fresh:
            session.add(AlertState(vin=vin, key=prefix + name, sent_at=utcnow()))
            sent.add(prefix + name)
        return fresh

    def _confirmed(self, vin: str, key: str, active: bool, now: datetime.datetime, confirm_s: int = SECURITY_CONFIRM_SECONDS) -> bool:
        """True when `active` has held for SECURITY_CONFIRM_SECONDS in a row. On the first sighting a read is scheduled for when the
        delay expires (with the car parked the poll is every 60 min, so otherwise the alert would arrive late)."""
        k = (vin, key)
        if not active:
            self._pending_since.pop(k, None)
            return False
        since = self._pending_since.get(k)
        if since is None:
            self._pending_since[k] = now
            try:
                asyncio.get_running_loop().call_later(confirm_s + 5, lambda: self.request_poll(vin, force=True))
            except RuntimeError:
                pass                       # no event loop (synchronous tests)
            return False
        return (now - since).total_seconds() >= confirm_s
