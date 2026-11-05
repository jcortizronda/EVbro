"""Smart polling engine and state machine."""
from __future__ import annotations

import asyncio
import dataclasses
import datetime
import logging
import os
from typing import Callable, Dict, List, Optional, Set

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.core import commands, ebro_login
from app.core.context import CoreCtx
from app.core.errors import CommandError
from app.core.probe import probe_once
from app.core.telemetry import parse_telemetry_data
from app.db.models import AppUser, CommandAudit, EbroAccount, Geofence, GeofenceEvent, TelemetrySnapshot, Vehicle
from app.db.session import async_session_factory
from app.security.access import first_manager_id, foreign_vins, within_vehicle_limit
from app.services import command_policy
from app.services import telemetry as usage_reports
from app.services.accounts import fetch_account_vehicles, vehicle_data_dir
from app.services.car_events import CarEventHub
from app.services.charge_detector import ChargeDetector, grid_charging
from app.services.charge_manager import ChargeManager
from app.services.geofence import find_geofence
from app.services.mqtt_hub import MqttHub
from app.services.mqtt_publisher import MqttPublisher
from app.services.notification_service import NotificationService
from app.services.refuel_detector import RefuelDetector
from app.services.trip_detector import TripDetector

_LOGGER = logging.getLogger(__name__)

# Longest poll interval while a trip is open (it only asks the cloud: it does not wake the car)
OPEN_TRIP_POLL_SECONDS = 150
# How long «car left with doors/windows open» or unlocked must last before alerting
SECURITY_CONFIRM_SECONDS = 5 * 60
# Tyre pressure: it must stay out of range this long (when starting off or with a warm tyre it rises 10 % without being a problem)
# and the alert is not repeated before TYRE_ALERT_COOLDOWN
TYRE_CONFIRM_SECONDS = 10 * 60
TYRE_ALERT_COOLDOWN = datetime.timedelta(hours=12)
UNEXPECTED_COMMAND_ERROR = "No se pudo enviar la orden al coche por un error inesperado del servidor (mira el log)."
# Cars polled at the same time in one pass of the loop: a manager whose cloud answers slowly must not delay everybody else's reads,
# but the Chery cloud is shared, so the fan-out stays small.
POLL_CONCURRENCY = 4
# Two reads of the same car at once, or one right after the other, bring nothing newer from the cloud and ran the detectors
# twice over the same snapshot (04-11-2026: a push from the car 2 s after the scheduled read duplicated trips and charges).
# A read requested while one is running joins it; one requested this soon after an answer gets that answer.
POLL_COALESCE_SECONDS = 5


class VehiclePoller:
    _instance: Optional["VehiclePoller"] = None
    _contexts: Dict[str, CoreCtx] = {}
    _running: bool = False
    _task: Optional[asyncio.Task] = None
    _ws_subscribers: Set[Callable[[Dict], None]] = set()

    # Latest in-memory snapshot per VIN for instant answers
    _latest_snapshots: Dict[str, Dict] = {}
    # Timestamp of the last poll made
    _last_poll_times: Dict[str, datetime.datetime] = {}

    # State memory for firing mobile alerts (avoids duplicates)
    _last_geofence_by_vin: Dict[str, Optional[int]] = {}
    _last_charging_by_vin: Dict[str, bool] = {}
    _last_maint_check_km: Dict[str, float] = {}
    _last_maint_check_at: Dict[str, datetime.datetime] = {}
    _last_renewal_check_at: Dict[str, datetime.datetime] = {}
    _last_retention_run: Optional[datetime.datetime] = None
    _last_catalog_sync: Optional[datetime.datetime] = None
    # Alert situations not yet confirmed: (vin, key) -> since when they are seen
    _pending_since: Dict[tuple, datetime.datetime] = {}
    _last_tyre_alert_time: Dict[str, datetime.datetime] = {}
    _maint_alert_sent: Dict[str, datetime.datetime] = {}
    _last_security_alert_time: Dict[str, datetime.datetime] = {}
    _last_fuel_alert_time: Dict[str, datetime.datetime] = {}
    _last_windows_alert_time: Dict[str, datetime.datetime] = {}
    _last_fault_alert_time: Dict[str, datetime.datetime] = {}
    # Polls requested by car events (one at a time per vehicle)
    _poll_requests: Dict[str, "asyncio.Task"] = {}
    _poll_semaphore: Optional[asyncio.Semaphore] = None
    # The read in progress per vehicle: whoever asks meanwhile waits for it instead of asking the cloud again
    _inflight: Dict[str, "asyncio.Task"] = {}

    @classmethod
    def get_instance(cls) -> "VehiclePoller":
        if cls._instance is None:
            cls._instance = VehiclePoller()
        return cls._instance

    def register_ws_subscriber(self, callback: Callable[[Dict], None]) -> None:
        self._ws_subscribers.add(callback)

    def unregister_ws_subscriber(self, callback: Callable[[Dict], None]) -> None:
        self._ws_subscribers.discard(callback)

    def broadcast_telemetry(self, vin: str, data: Dict) -> None:
        """Sends updates to the connected WebSocket clients."""
        self.broadcast_event({"type": "telemetry", "vin": vin, "data": data})

    def broadcast_event(self, payload: Dict) -> None:
        """Sends any event (`type`, …) to the connected WebSocket clients."""
        for sub in list(self._ws_subscribers):
            try:
                sub(payload)
            except Exception as e:
                _LOGGER.debug(f"Error notifying WS subscriber: {e}")

    def get_latest(self, vin: str) -> Optional[Dict]:
        return self._latest_snapshots.get(vin)

    def apply_car_fields(self, vin: str, fields: Dict, geo: Optional[Dict] = None) -> Dict:
        """Merges an MQTT push from the car (doors, lock, cable, engine…) over the latest reading.

        It only touches the in-memory copy and notifies over WebSocket: it does not write to the DB. The
        snapshot that feeds the trip and charge detectors is still saved by the poll, which the
        `car_events` triggers bring forward when needed. The push wins over the poll."""
        base_raw = (self._latest_snapshots.get(vin) or {}).get("raw_data") or {}
        telemetry = parse_telemetry_data(base_raw, geo, fields)
        self._latest_snapshots[vin] = telemetry
        self.broadcast_telemetry(vin, telemetry)
        return telemetry

    def request_poll(self, vin: str, force: bool = False) -> None:
        """Requests a poll now, without waiting for the loop. Does nothing if one is already running for that vehicle."""
        task = self._poll_requests.get(vin)
        if task is not None and not task.done():
            return
        self._poll_requests[vin] = asyncio.create_task(self.poll_vehicle(vin, force=force))

    def _get_or_create_ctx(self, vin: str) -> CoreCtx:
        """Creates or returns the CoreCtx for a given VIN."""
        if vin not in self._contexts:
            data_dir = vehicle_data_dir(vin)

            ctx = CoreCtx(
                vin=vin,
                pin="",                       # the PIN comes from the car owner's Ebro account (see `_stored_pin`), never a shared one
                tsp_host=settings.EBRO_TSP_HOST,
                bff=settings.EBRO_BFF_HOST,
                channel_id=settings.EBRO_CHANNEL_ID,
                token_path=os.path.join(data_dir, "token.json"),
                taskid_file=os.path.join(data_dir, "taskid.txt"),
            )
            self._contexts[vin] = ctx
        return self._contexts[vin]

    async def initialize(self) -> None:
        """Loads the vehicles from the database or by logging in to the Ebro cloud."""
        async with async_session_factory() as session:
            # 1. Load the vehicles already in the DB
            stmt = select(Vehicle).where(Vehicle.is_manual.is_(False))        # manual cars have nothing to poll
            result = await session.execute(stmt)
            vehicles = result.scalars().all()

            for v in vehicles:
                self._get_or_create_ctx(v.vin)

            # Each manager's PIN goes to that manager's cars only
            accounts = (await session.execute(select(EbroAccount).where(EbroAccount.is_active == True).order_by(EbroAccount.id))).scalars().all()  # noqa: E712
            by_owner = {a.user_id: a for a in accounts if a.user_id is not None}
            for v in vehicles:
                acc = by_owner.get(v.owner_id)
                ctx = self._contexts[v.vin]
                if acc and acc.pin and acc.pin.strip() and not (ctx.pin or "").strip():
                    ctx.pin = acc.pin.strip()

            # 2. Auto-discover the cars of every account whose manager has none yet (or the .env account if there is nothing else)
            owners_with_cars = {v.owner_id for v in vehicles}
            for acc in accounts:
                if acc.user_id not in owners_with_cars:
                    _LOGGER.info("Auto-discovering vehicles of the Ebro account of user %s...", acc.user_id)
                    await self.discover_vehicles(account=acc)
            if not accounts and not vehicles and settings.EBRO_PHONE and settings.EBRO_PASSWORD:
                _LOGGER.info("Auto-discovering vehicles from the environment's Ebro account...")
                await self.discover_vehicles(account=None)

    async def discover_vehicles(self, account: Optional[EbroAccount] = None) -> List[Vehicle]:
        """Logs in to the Ebro cloud and discovers the vehicles linked to the account."""
        phone = None
        password = None
        area_code = "34"

        acc_user_id = None
        if account:
            phone = account.phone
            password = account.password
            area_code = account.area_code or "34"
            acc_user_id = account.user_id
        else:
            async with async_session_factory() as session:
                acc_stmt = select(EbroAccount).where(EbroAccount.is_active == True)
                db_acc = (await session.execute(acc_stmt)).scalars().first()
                if db_acc:
                    phone = db_acc.phone
                    password = db_acc.password
                    area_code = db_acc.area_code or "34"
                    acc_user_id = db_acc.user_id

        if not phone or not password:
            phone = settings.EBRO_PHONE
            password = settings.EBRO_PASSWORD
            area_code = settings.EBRO_AREA_CODE

        if not phone or not password:
            _LOGGER.info("No Ebro account linked yet. Waiting for a login in the web interface.")
            return []

        owner_for_skip = acc_user_id
        if owner_for_skip is None:
            async with async_session_factory() as session:
                owner_for_skip = await first_manager_id(session)

        def _do_login():
            token_resp = ebro_login.login_with_password(
                phone,
                password,
                area_code,
            )
            return token_resp

        loop = asyncio.get_running_loop()
        try:
            token_data = await loop.run_in_executor(None, _do_login)
        except Exception as e:
            _LOGGER.error(f"Error logging in to Ebro: {e}")
            return []

        if not token_data or "access_token" not in token_data:
            # Only the shape of the answer: it may carry partial tokens or account details that do not belong in the log.
            _LOGGER.error("Invalid login answer (keys: %s)", sorted(token_data) if isinstance(token_data, dict) else type(token_data).__name__)
            return []

        try:
            skip = await foreign_vins(owner_for_skip)
            v_list = await loop.run_in_executor(None, fetch_account_vehicles, token_data, skip)
        except Exception as e:
            _LOGGER.error(f"Error fetching the vehicle list: {e}")
            v_list = []

        discovered = []
        async with async_session_factory() as session:
            owner_id = acc_user_id or await first_manager_id(session)          # the manager that connected the account owns its cars
            for item in v_list:
                vin = item.get("vin")
                if not vin:
                    continue

                nickname = item.get("nickname") or item.get("fullName") or item.get("modelName") or "Ebro"
                model = item.get("fullName") or item.get("modelName") or "Ebro Auto"
                brand = item.get("brandName") or "Ebro"
                car_tok = item.get("carToken") or item.get("tboxSn")

                stmt = select(Vehicle).where(Vehicle.vin == vin)
                existing = (await session.execute(stmt)).scalar_one_or_none()

                if existing is not None and existing.owner_id not in (None, owner_id):
                    _LOGGER.warning("Vehicle %s already belongs to another account: left alone.", vin[-6:])
                    continue
                if existing is None and (owner_id is None or not await within_vehicle_limit(session, owner_id)):
                    _LOGGER.warning("Vehicle %s not registered: the account's vehicle limit was reached.", vin[-6:])
                    continue

                if not existing:
                    new_v = Vehicle(
                        owner_id=owner_id,
                        vin=vin,
                        name=nickname,
                        model=model,
                        brand=brand,
                        car_token=car_tok,
                    )
                    session.add(new_v)
                    await session.commit()
                    discovered.append(new_v)
                    _LOGGER.info(f"Vehicle registered: {nickname} (VIN: {vin})")
                else:
                    existing.owner_id = existing.owner_id or owner_id
                    existing.name = nickname
                    existing.model = model
                    existing.car_token = car_tok
                    await session.commit()
                    discovered.append(existing)

                self._get_or_create_ctx(vin)

            # If real vehicles were found, remove the dummy demo vehicle if it exists
            if discovered:
                dummy_stmt = select(Vehicle).where(Vehicle.vin == "LSJA8888888888888")
                dummy_v = (await session.execute(dummy_stmt)).scalar_one_or_none()
                if dummy_v and dummy_v.owner_id in (None, owner_id):
                    await session.delete(dummy_v)
                    await session.commit()

        return discovered

    async def poll_vehicle(self, vin: str, force: bool = False) -> Optional[Dict]:
        """One telemetry read of a vehicle: it joins a read already running, reuses one answered less than
        POLL_COALESCE_SECONDS ago, and otherwise asks the cloud (`_poll_vehicle`). Whatever the outcome, the time of the
        attempt is kept: a car whose reads fail is retried at its adaptive interval, not at every turn of the loop."""
        running = self._inflight.get(vin)
        if running is not None:
            return await asyncio.shield(running)
        last = self._last_poll_times.get(vin)
        if last is not None and (datetime.datetime.utcnow() - last).total_seconds() < POLL_COALESCE_SECONDS:
            return self._latest_snapshots.get(vin)
        task = asyncio.create_task(self._poll_vehicle(vin, force))
        self._inflight[vin] = task
        try:
            return await task
        finally:
            self._inflight.pop(vin, None)
            self._last_poll_times[vin] = datetime.datetime.utcnow()

    async def _poll_vehicle(self, vin: str, force: bool = False) -> Optional[Dict]:
        """Asks the cloud, saves the snapshot and runs the detectors. Always through `poll_vehicle`."""
        ctx = self._get_or_create_ctx(vin)
        loop = asyncio.get_running_loop()

        def _probe():
            captured_data = {}
            def _on_data(d):
                captured_data.update(d)

            res = probe_once(ctx, lambda m: None, force=force, on_data=_on_data)
            return res, captured_data

        try:
            res, raw_data = await loop.run_in_executor(None, _probe)
        except Exception as e:
            usage_reports.count_poll("error")
            _LOGGER.error(f"Exception polling {vin}: {e}", exc_info=True)
            return None

        if isinstance(res, dict) and res.get("ok") is False and res.get("reason") != "cooldown":
            _LOGGER.warning("Read of %s brought no data: %s", vin[-6:], res.get("reason"))

        # With no real-time data (e.g. the car is asleep), use the probe's "rich" summary.
        # Coordinates do NOT travel in the result of `probe_once` (see core/probe.py):
        # raw data only arrives through the `on_data` callback above.
        if not raw_data and isinstance(res, dict):
            raw_data = res.get("rich", {})

        if not raw_data:
            usage_reports.count_poll("empty")
            return None
        usage_reports.count_poll("ok")

        # Normalise the telemetry
        # The MQTT push wins over the poll when both carry the same key: the poll sometimes returns
        # a cloud snapshot older than the car's last event.
        telemetry = parse_telemetry_data(raw_data, None, CarEventHub.get_instance().fields_for(vin))
        self._latest_snapshots[vin] = telemetry

        # Save to the database and run the detectors
        async with async_session_factory() as session:
            v = await session.execute(select(Vehicle).where(Vehicle.vin == vin))
            vehicle = v.scalar_one_or_none()
            if not vehicle:
                return telemetry

            # Update the quick fields on the vehicle
            vehicle.state = telemetry["state"]
            vehicle.last_seen = datetime.datetime.utcnow()
            if telemetry.get("battery_level") is not None:
                vehicle.battery_level = telemetry["battery_level"]
            if telemetry.get("electric_range_km") is not None:
                vehicle.electric_range_km = telemetry["electric_range_km"]
            if telemetry.get("total_range_km") is not None:
                vehicle.total_range_km = telemetry["total_range_km"]
            if telemetry.get("odometer_km") is not None:
                vehicle.odometer = telemetry["odometer_km"]
            if telemetry.get("latitude") is not None:
                vehicle.latitude = telemetry["latitude"]
                vehicle.longitude = telemetry["longitude"]

            # Record the snapshot
            snap = TelemetrySnapshot(
                vin=vin,
                online_status=telemetry["online_status"],
                state=telemetry["state"],
                battery_level=telemetry["battery_level"],
                electric_range_km=telemetry["electric_range_km"],
                total_range_km=telemetry["total_range_km"],
                fuel_level=telemetry["fuel_level"],
                odometer_km=telemetry["odometer_km"],
                speed_kmh=telemetry["speed_kmh"],
                latitude=telemetry["latitude"],
                longitude=telemetry["longitude"],
                altitude=telemetry["altitude"],
                heading=telemetry["heading"],
                outside_temp=telemetry["outside_temp"],
                inside_temp=telemetry["inside_temp"],
                climate_on=telemetry["climate_on"],
                target_temp=telemetry["target_temp"],
                battery_voltage=telemetry["battery_voltage"],
                battery_current=telemetry["battery_current"],
                charge_power_kw=telemetry["charge_power_kw"],
                is_charging=telemetry["is_charging"],
                cable_connected=telemetry["cable_connected"],
                fast_charging=telemetry["fast_charging"],
                remaining_charge_time_min=telemetry["remaining_charge_time_min"],
                is_locked=telemetry["is_locked"],
                doors=telemetry["doors"],
                windows=telemetry["windows"],
                tyre_pressures=telemetry["tyre_pressures"],
                tyre_temps=telemetry["tyre_temps"],
                raw_data=telemetry["raw_data"],
            )
            session.add(snap)

            # Process trips and charges
            await TripDetector.process_snapshot(session, vehicle, telemetry)
            await ChargeDetector.process_snapshot(session, vehicle, telemetry)
            await RefuelDetector.process_snapshot(session, vehicle, telemetry)

            # Check and fire mobile push alerts (geofences, maintenance, charging, security)
            await self._check_and_dispatch_alerts(session, vehicle, telemetry)

            # The server's MQTT broker belongs to whoever runs the server: by default only their own cars are published there
            # (positions and state of other managers' cars never leave their accounts). See MQTT_PUBLISH_SCOPE.
            publish_mqtt = settings.MQTT_ENABLED and (settings.MQTT_PUBLISH_SCOPE == "all" or vehicle.owner_id == await first_manager_id(session))

            await session.commit()

        # Charge control: battery target, pauses and their renewal
        await ChargeManager.get_instance().on_snapshot(vin, telemetry)

        # Publish to MQTT if enabled
        if publish_mqtt:
            MqttPublisher.publish_telemetry(vin, telemetry, vehicle)
        MqttHub.get_instance().publish(vehicle.owner_id, vin, telemetry, vehicle)      # the owner's own broker, if they linked one
        # Notify the web UI's WebSockets
        self.broadcast_telemetry(vin, telemetry)

        return telemetry

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
                    await self._geofence_transition(session, vehicle, current_gf, last_gf_id, lat, lon, battery, odo)
                    self._last_geofence_by_vin[vin] = current_gf_id

        # 2. Charging alerts (start / end / interruption); from the grid only, the engine charging on the road is not news
        is_charging = grid_charging(telemetry)
        if vin in self._last_charging_by_vin:
            was_charging = self._last_charging_by_vin[vin]
            if not was_charging and is_charging:
                await NotificationService.notify_charging_started(
                    vehicle.name, battery or 0.0, telemetry.get("electric_range_km"), vin=vin
                )
            elif was_charging and not is_charging:
                # If it stopped before completing (< 95%) and it was not a scheduled stop
                if battery is not None and battery < 95:
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
            if fuel <= (vehicle.fuel_low_threshold or 15):         # the car's own threshold (a car setting, not an account one)
                now = datetime.datetime.utcnow()
                last_fuel_alert = self._last_fuel_alert_time.get(vin)
                if not last_fuel_alert or (now - last_fuel_alert).total_seconds() > 7200:
                    self._last_fuel_alert_time[vin] = now
                    await NotificationService.notify_fuel_low(vehicle.name, fuel, fuel_range, vin=vin)

        # 4. Maintenance alerts (EBRO's official plan or custom tasks, depending on the car's mode).
        #    Checked every 200 km or every 6 h (month-based deadlines expire without the car moving) and each alert
        #    is sent at most once a day.
        if odo is not None:
            now = datetime.datetime.utcnow()
            last_km = self._last_maint_check_km.get(vin, 0.0)
            last_at = self._last_maint_check_at.get(vin)
            if abs(odo - last_km) >= 200 or not last_at or (now - last_at).total_seconds() >= 6 * 3600:
                self._last_maint_check_km[vin] = odo
                self._last_maint_check_at[vin] = now
                try:
                    from app.services.maintenance_alerts import collect_alerts
                    async with async_session_factory() as m_session:
                        pending = await collect_alerts(m_session, vin, odo, now)
                    for alert in pending:
                        sent = self._maint_alert_sent.get(f"{vin}:{alert['key']}")
                        if sent and (now - sent).total_seconds() < 24 * 3600:
                            continue
                        self._maint_alert_sent[f"{vin}:{alert['key']}"] = now
                        await NotificationService.notify_maintenance_alert(
                            vehicle.name, alert["label"], alert["remaining_km"], alert["is_overdue"],
                            alert["items"], alert["labor_hours"], remaining_days=alert["remaining_days"], vin=vin,
                        )
                except Exception as ex:
                    _LOGGER.debug(f"Warning while checking maintenance alerts: {ex}")

        # 4b. Dated renewals (insurance, taxes…): checked every 6 h; each alert repeats according to its urgency
        now = datetime.datetime.utcnow()
        last_r = self._last_renewal_check_at.get(vin)
        if not last_r or (now - last_r).total_seconds() >= 6 * 3600:
            self._last_renewal_check_at[vin] = now
            try:
                from app.services.renewal_alerts import collect_renewals
                async with async_session_factory() as r_session:
                    due = await collect_renewals(r_session, vin, now)
                for alert in due:
                    k = f"{vin}:{alert['key']}"
                    sent = self._maint_alert_sent.get(k)
                    if sent and (now - sent).total_seconds() < alert["repeat_hours"] * 3600:
                        continue
                    self._maint_alert_sent[k] = now
                    await NotificationService.notify_renewal(vehicle.name, alert["label"], alert["remaining_days"], vin=vin)
            except Exception as ex:
                _LOGGER.debug(f"Warning while checking renewals: {ex}")

        # «The car has been left»: stopped AND switched off (high voltage at 0; without that signal, just stopped). With the car on
        # (traffic light, you just arrived and are getting out) there is no alert about doors or locking.
        left_car = telemetry.get("state") == "parked" and telemetry.get("high_voltage_on") is not True

        # 5. Windows / sunroof left open when parking (the situation must last SECURITY_CONFIRM_SECONDS)
        windows = telemetry.get("windows") or {}
        open_windows = [k for k, v in windows.items() if v]
        is_parked = left_car or telemetry.get("is_locked") is True
        if self._confirmed(vin, "windows", is_parked and bool(open_windows), datetime.datetime.utcnow()):
            now = datetime.datetime.utcnow()
            last_win_alert = self._last_windows_alert_time.get(vin)
            if not last_win_alert or (now - last_win_alert).total_seconds() > 1800:
                self._last_windows_alert_time[vin] = now
                win_names = {
                    "fl": "Delantera Izquierda", "fr": "Delantera Derecha",
                    "rl": "Trasera Izquierda", "rr": "Trasera Derecha",
                    "sunroof": "Techo Solar"
                }
                desc = [win_names.get(k, k) for k in open_windows]
                await NotificationService.notify_windows_open_alert(vehicle.name, desc, vin=vin)

        # 6. Vehicle fault / DTC alerts
        faults = telemetry.get("dtcs") or telemetry.get("fault_messages") or []
        if faults:
            now = datetime.datetime.utcnow()
            last_fault_alert = self._last_fault_alert_time.get(vin)
            if not last_fault_alert or (now - last_fault_alert).total_seconds() > 3600:
                self._last_fault_alert_time[vin] = now
                await NotificationService.notify_vehicle_fault(vehicle.name, ", ".join(faults), vin=vin)

        # 7. Security alerts when parked (doors open or not locked). They used to fire on the first «parked» reading
        #    (you arrive, open the door and get out: false alarm); now the car must be off and the situation must last
        #    SECURITY_CONFIRM_SECONDS.
        issues = []
        if left_car:
            if telemetry.get("is_locked") is False:
                issues.append("Vehículo sin cerrar con llave.")
            open_doors = [k for k, v in (telemetry.get("doors") or {}).items() if v]
            if open_doors:
                issues.append(f"Puertas abiertas: {', '.join(open_doors)}")
        if self._confirmed(vin, "security", bool(issues), datetime.datetime.utcnow()):
            now = datetime.datetime.utcnow()
            last_alert = self._last_security_alert_time.get(vin)
            if not last_alert or (now - last_alert).total_seconds() > 1800:
                self._last_security_alert_time[vin] = now
                await NotificationService.notify_security_alert(vehicle.name, issues, vin=vin)

        # 8. Tyre pressure outside the range set on the vehicle
        await self._check_tyre_pressure(vehicle, telemetry)

    @staticmethod
    def tyre_pressure_issues(vehicle: Vehicle, telemetry: Dict) -> list:
        """Tyres whose pressure differs from the vehicle's reference (front/rear) by more than the configured %."""
        ref = {"fl": vehicle.tyre_front_pressure_bar, "fr": vehicle.tyre_front_pressure_bar,
               "rl": vehicle.tyre_rear_pressure_bar, "rr": vehicle.tyre_rear_pressure_bar}
        names = {"fl": "Delantero izquierdo", "fr": "Delantero derecho", "rl": "Trasero izquierdo", "rr": "Trasero derecho"}
        tol = vehicle.tyre_pressure_tolerance_pct or 10.0
        out = []
        for key, pressure in (telemetry.get("tyre_pressures") or {}).items():
            target = ref.get(key)
            if not target or not pressure or pressure <= 0:
                continue
            dev = (pressure - target) / target * 100.0
            if abs(dev) > tol:
                out.append(f"{names.get(key, key)}: {pressure:.2f} bar ({dev:+.0f} % sobre los {target:.2f} bar de referencia)")
        return out

    async def _geofence_transition(self, session, vehicle: Vehicle, current_gf, last_gf_id, lat, lon, battery, odo) -> None:
        """The car changed geofence: record the exit and/or entry (always) and notify (per geofence switch)."""
        now = datetime.datetime.utcnow()
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

    async def _check_tyre_pressure(self, vehicle: Vehicle, telemetry: Dict) -> None:
        """Alerts when a tyre leaves the configured range and stays there TYRE_CONFIRM_SECONDS (once every 12 h)."""
        issues = self.tyre_pressure_issues(vehicle, telemetry)
        now = datetime.datetime.utcnow()
        if self._confirmed(vehicle.vin, "tyres", bool(issues), now, TYRE_CONFIRM_SECONDS):
            last = self._last_tyre_alert_time.get(vehicle.vin)
            if not last or now - last >= TYRE_ALERT_COOLDOWN:
                self._last_tyre_alert_time[vehicle.vin] = now
                await NotificationService.notify_tyre_pressure(vehicle.name, issues, vin=vehicle.vin)

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

    def _calculate_sleep_seconds(self, state: str, telemetry: Optional[Dict] = None) -> int:
        """Picks the dynamic poll interval from the vehicle's state."""
        if state in ("driving", "ready"):
            speed = (telemetry or {}).get("speed_kmh", 0.0)
            if speed and speed > 1.0:
                return max(15, settings.POLL_INTERVAL_DRIVING_MIN * 60)
            else:
                return max(30, settings.POLL_INTERVAL_DRIVING_IDLE_MIN * 60)
        elif state == "charging":
            return max(30, settings.POLL_INTERVAL_CHARGING_MIN * 60)
        elif state == "plugged":
            return max(60, settings.POLL_INTERVAL_PLUGGED_MIN * 60)
        elif state == "parked":
            if settings.POLL_INTERVAL_PARKED_MIN <= 0:
                return 3600
            return settings.POLL_INTERVAL_PARKED_MIN * 60
        else:  # asleep / offline
            return 3600

    def get_polling_status(self) -> Dict:
        """Returns the current state of the poll timers for each vehicle."""
        now = datetime.datetime.utcnow()
        vehicles_status = []
        for vin in self._contexts:
            latest = self.get_latest(vin) or {}
            state = latest.get("state", "parked")
            last_poll = self._last_poll_times.get(vin)
            interval_s = self._calculate_sleep_seconds(state, latest)

            elapsed_s = int((now - last_poll).total_seconds()) if last_poll else None
            next_in_s = max(0, interval_s - elapsed_s) if elapsed_s is not None else 0

            vehicles_status.append({
                "vin": vin,
                "state": state,
                "interval_seconds": interval_s,
                "interval_minutes": round(interval_s / 60.0, 1),
                "last_poll_time": last_poll.isoformat() if last_poll else None,
                "next_poll_in_seconds": next_in_s,
                "freshness_text": latest.get("freshness_text", "Desconocido"),
            })

        return {
            "poll_interval_parked_min": settings.POLL_INTERVAL_PARKED_MIN,
            "poll_interval_plugged_min": settings.POLL_INTERVAL_PLUGGED_MIN,
            "poll_interval_charging_min": settings.POLL_INTERVAL_CHARGING_MIN,
            "poll_interval_driving_min": settings.POLL_INTERVAL_DRIVING_MIN,
            "poll_interval_driving_idle_min": settings.POLL_INTERVAL_DRIVING_IDLE_MIN,
            "vehicles": vehicles_status,
        }

    def update_polling_intervals(
        self,
        parked_min: Optional[int] = None,
        plugged_min: Optional[int] = None,
        charging_min: Optional[int] = None,
        driving_min: Optional[int] = None,
        driving_idle_min: Optional[int] = None,
    ) -> Dict:
        """Updates the poll intervals in memory and in the settings."""
        if parked_min is not None:
            settings.POLL_INTERVAL_PARKED_MIN = max(0, parked_min)
        if plugged_min is not None:
            settings.POLL_INTERVAL_PLUGGED_MIN = max(1, plugged_min)
        if charging_min is not None:
            settings.POLL_INTERVAL_CHARGING_MIN = max(1, charging_min)
        if driving_min is not None:
            settings.POLL_INTERVAL_DRIVING_MIN = max(1, driving_min)
        if driving_idle_min is not None:
            settings.POLL_INTERVAL_DRIVING_IDLE_MIN = max(1, driving_idle_min)

        _LOGGER.info(
            f"Adaptive poll intervals updated: Parked={settings.POLL_INTERVAL_PARKED_MIN}m, "
            f"Plugged={settings.POLL_INTERVAL_PLUGGED_MIN}m, Charging={settings.POLL_INTERVAL_CHARGING_MIN}m, "
            f"Driving={settings.POLL_INTERVAL_DRIVING_MIN}m, Idle={settings.POLL_INTERVAL_DRIVING_IDLE_MIN}m"
        )
        return self.get_polling_status()

    async def _poller_loop(self) -> None:
        """Main adaptive, continuous polling loop."""
        _LOGGER.info("Starting the EVBro smart polling loop...")
        while self._running:
            try:
                async with async_session_factory() as session:
                    vehicles = (await session.execute(select(Vehicle).where(Vehicle.is_manual.is_(False)))).scalars().all()
                    vins = [v.vin for v in vehicles]

                due = []
                for vin in vins:
                    latest = self.get_latest(vin) or {}
                    current_state = latest.get("state", "parked")
                    last_poll = self._last_poll_times.get(vin)
                    now = datetime.datetime.utcnow()

                    interval = self._calculate_sleep_seconds(current_state, latest)
                    if ChargeManager.get_instance().wants_fast_poll(vin):
                        interval = min(interval, 300)        # paused at the target: learn early that it gets plugged in
                    if TripDetector.has_open_trip(vin):
                        interval = min(interval, OPEN_TRIP_POLL_SECONDS)     # open trip: close it soon after parking

                    if last_poll is None or (now - last_poll).total_seconds() >= interval:
                        _LOGGER.info(
                            f"Adaptive poll for {vin}: state '{current_state}', interval {interval}s"
                        )
                        due.append(vin)

                # Due cars are polled together (at most POLL_CONCURRENCY): a slow one does not delay the rest.
                # force=True on scheduled polls to get past the opportunistic-read cooldown.
                if due:
                    await asyncio.gather(*(self._poll_limited(vin) for vin in due))

            except asyncio.CancelledError:
                break
            except Exception as e:
                _LOGGER.error(f"Error in a polling iteration: {e}")

            await ChargeManager.get_instance().tick()
            await self._run_climate_schedules()
            await self._maybe_apply_retention()
            await self._maybe_sync_catalog()
            await asyncio.sleep(10)

    async def _poll_limited(self, vin: str) -> None:
        """One scheduled read, holding a slot of the shared fan-out limit; an error in one car never stops the pass."""
        if self._poll_semaphore is None:
            self._poll_semaphore = asyncio.Semaphore(POLL_CONCURRENCY)
        async with self._poll_semaphore:
            try:
                await self.poll_vehicle(vin, force=True)
            except Exception as e:                            # noqa: BLE001 — logged; the other cars of the pass go on
                _LOGGER.error(f"Error polling {vin}: {e}")

    async def _run_climate_schedules(self) -> None:
        """Timetabled pre-conditioning: sends `clima_on` with the stored PIN when a schedule is due (services/climate_schedule.py)."""
        try:
            from app.services import climate_schedule

            async def send(vin: str, temperature: float) -> Dict:
                return await self.execute_internal(vin, "clima_on", {"temperature": temperature}, origin="schedule")

            async with async_session_factory() as session:
                await climate_schedule.run_due(session, send)
        except Exception as ex:                               # noqa: BLE001 — the poll loop must survive
            _LOGGER.warning(f"Could not run the climate schedules: {ex}")

    async def _maybe_apply_retention(self) -> None:
        """Applies the history retention periods once every 24 h (and at start-up)."""
        now = datetime.datetime.utcnow()
        if self._last_retention_run and (now - self._last_retention_run).total_seconds() < 24 * 3600:
            return
        self._last_retention_run = now
        try:
            from app.services import retention
            async with async_session_factory() as session:
                deleted = await retention.apply(session, now)
            if any(deleted.values()):
                _LOGGER.info(f"History retention: deleted {deleted}")
        except Exception as ex:
            _LOGGER.warning(f"Could not apply the history retention: {ex}")

    async def _maybe_sync_catalog(self) -> None:
        """Mirror of the online catalogue once a day (and at start-up) when the server is set to use one."""
        now = datetime.datetime.utcnow()
        if self._last_catalog_sync and (now - self._last_catalog_sync).total_seconds() < 24 * 3600:
            return
        self._last_catalog_sync = now
        try:
            from app.services import catalog_remote
            async with async_session_factory() as session:
                await catalog_remote.sync_if_enabled(session)
        except Exception as ex:                               # noqa: BLE001 — the poll loop must survive
            _LOGGER.warning(f"Could not sync the online catalogue: {ex}")

    async def start(self) -> None:
        if self._running:
            return
        self._running = True
        await self.initialize()
        MqttPublisher.configure(self.execute_internal, lambda vin: self.poll_vehicle(vin, force=True))   # orders and refresh from Home Assistant
        await MqttPublisher.start()
        MqttHub.get_instance().configure(self.execute_internal, lambda vin: self.poll_vehicle(vin, force=True))
        await MqttHub.get_instance().start()
        await ChargeManager.get_instance().warm_up()
        await CarEventHub.get_instance().start(self)
        self._task = asyncio.create_task(self._poller_loop())

    async def stop(self) -> None:
        self._running = False
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        await CarEventHub.get_instance().stop()
        await MqttPublisher.stop()
        await MqttHub.get_instance().stop()

    async def execute_command(
        self,
        vin: str,
        command_key: str,
        pin: str,
        params: Optional[Dict] = None,
        client_ip: Optional[str] = None,
    ) -> Dict:
        """Runs a remote command on the vehicle.

        Returns `{"success", "message", "reason", "retry_after"}`. `reason` is stable (the UI
        acts on it): `pin_required`, `pin_invalid`, `pin_locked`, `params_invalid` (rejected
        before sending), or the core's `reason` (`pin`, `reauth`, `config`) if sending failed.
        The PIN and parameter rules live in `command_policy`."""
        ctx = self._get_or_create_ctx(vin)
        stored_pin = await self._stored_pin(ctx)
        policy = await self._pin_policy(vin)

        try:
            clean_params = command_policy.sanitize_params(command_key, params)
            effective_pin = command_policy.authorize(command_key, pin, stored_pin, vin=vin, policy=policy)
        except command_policy.CommandRejected as rej:
            await self._audit_command(vin, command_key, params, False, rej.reason, rej.message, client_ip)
            return {"success": False, "message": rej.message, "reason": rej.reason, "retry_after": rej.retry_after}

        # The typed PIN is good for THIS send only: a copy of the context (it shares the vehicle's
        # state: lock-out guard and taskId cache). It used to be assigned to `ctx.pin` for good,
        # so a mistyped PIN stayed and used up attempts on the following commands.
        cmd_ctx = dataclasses.replace(ctx, pin=effective_pin)
        if command_policy.requires_pin(command_key, policy) and not stored_pin:
            # Without a stored PIN, the cached taskId no longer proves the sender knows the PIN:
            # Chery is forced to check the typed one.
            ctx.invalidate_taskid()

        loop = asyncio.get_running_loop()
        success, code, reason = False, None, None

        def _send():
            return commands.send(cmd_ctx, command_key, emit=lambda m: None, params=clean_params)

        try:
            message = await loop.run_in_executor(None, _send)
            success = True
        except CommandError as e:
            message, code, reason = str(e), e.code, e.reason
        except Exception:
            # The text of an unexpected exception (library internals, hosts) is for the log, not for the client.
            _LOGGER.exception("Unexpected failure sending '%s' to %s", command_key, vin[-6:])
            message = UNEXPECTED_COMMAND_ERROR

        await self._audit_command(vin, command_key, clean_params, success, code or reason, message, client_ip)

        # If the command succeeded, trigger a quick poll to refresh the state
        if success:
            asyncio.create_task(self.poll_vehicle(vin, force=True))

        return {"success": success, "message": message, "reason": reason, "retry_after": 0}

    async def execute_internal(self, vin: str, command_key: str, params: Optional[Dict], origin: str) -> Dict:
        """A command EVBro sends itself (e.g. the charge manager): it uses the stored PIN and does NOT go through the
        API's parameter whitelist, which exists to restrain a client, not EVBro. It is audited all the same."""
        ctx = self._get_or_create_ctx(vin)
        stored_pin = await self._stored_pin(ctx)
        if not stored_pin:
            msg = "Falta el PIN de la cuenta Ebro: guárdalo en Ajustes → Cuenta Ebro para que EVBro pueda gestionar la carga."
            await self._audit_command(vin, command_key, params, False, "pin", msg, origin)
            return {"success": False, "message": msg, "reason": "pin"}
        cmd_ctx = dataclasses.replace(ctx, pin=stored_pin)
        loop = asyncio.get_running_loop()
        success, code, reason = False, None, None
        try:
            message = await loop.run_in_executor(None, lambda: commands.send(cmd_ctx, command_key, emit=lambda m: None, params=params))
            success = True
        except CommandError as e:
            message, code, reason = str(e), e.code, e.reason
        except Exception:
            _LOGGER.exception("Unexpected failure sending '%s' (%s) to %s", command_key, origin, vin[-6:])
            message = UNEXPECTED_COMMAND_ERROR
        await self._audit_command(vin, command_key, params, success, code or reason, message, origin)
        if success:
            asyncio.create_task(self.poll_vehicle(vin, force=True))
        return {"success": success, "message": message, "reason": reason}

    @staticmethod
    async def _pin_policy(vin: str) -> str:
        """When the car's owner wants the PIN asked for (AppUser.pin_policy); the default when the owner is unknown."""
        async with async_session_factory() as session:
            policy = (await session.execute(select(AppUser.pin_policy).join(Vehicle, Vehicle.owner_id == AppUser.id).where(Vehicle.vin == vin))).scalar_one_or_none()
        return policy if policy in command_policy.POLICIES else command_policy.DEFAULT_POLICY

    async def _stored_pin(self, ctx: CoreCtx) -> str:
        """The stored PIN of THIS car: the context's, or its owner's Ebro account, or (admin's cars only) the environment's ('' if none)."""
        if (ctx.pin or "").strip():
            return ctx.pin.strip()
        async with async_session_factory() as session:
            owner_id = (await session.execute(select(Vehicle.owner_id).where(Vehicle.vin == ctx.vin))).scalar_one_or_none()
            acc = None
            if owner_id is not None:
                acc = (await session.execute(select(EbroAccount).where(EbroAccount.is_active == True, EbroAccount.user_id == owner_id)  # noqa: E712
                                             .order_by(EbroAccount.id.desc()))).scalars().first()
            is_server_owner_car = owner_id is not None and owner_id == await first_manager_id(session)
        if acc and (acc.pin or "").strip():
            ctx.pin = acc.pin.strip()
        elif is_server_owner_car and (settings.EBRO_PIN or "").strip():
            ctx.pin = settings.EBRO_PIN.strip()
        return (ctx.pin or "").strip()

    @staticmethod
    async def _audit_command(vin, command_key, params, success, code, message, client_ip) -> None:
        """Records the attempt in the audit log, rejected ones too (wrong PIN, etc.)."""
        async with async_session_factory() as session:
            session.add(CommandAudit(
                vin=vin, command=command_key, params=params, success=success,
                code=(str(code)[:20] if code else None), message=message, client_ip=client_ip,
            ))
            await session.commit()
