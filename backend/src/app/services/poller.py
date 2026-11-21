"""Smart polling engine and state machine."""
from __future__ import annotations

import asyncio
import datetime
import logging
import os
from typing import Callable, Dict, List, Optional, Set

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.core import codes, ebro_login
from app.core.context import CoreCtx
from app.core.probe import probe_once
from app.core.telemetry import parse_telemetry_data
from app.db.models import EbroAccount, TelemetrySnapshot, Vehicle
from app.db.session import async_session_factory
from app.security.access import first_manager_id, foreign_vins, within_vehicle_limit
from app.services import energy_recalc, vehicle_profiles
from app.services import telemetry as usage_reports
from app.services.accounts import fetch_account_vehicles, vehicle_data_dir
from app.services.car_events import CarEventHub
from app.services.charge_detector import ChargeDetector, lacks_charge_data
from app.services.charge_manager import ChargeManager
from app.services.ebro_sessions import account_session_path, seed_account_session
from app.services.mqtt_hub import MqttHub
from app.services.mqtt_publisher import MqttPublisher
from app.services.poller_alerts import AlertsMixin
from app.services.poller_commands import CommandsMixin
from app.services.poller_position import PositionWatchMixin

# SESSION_RELOGIN_* are re-exported: the tests read them from here
from app.services.poller_session import (  # noqa: F401
    SESSION_RELOGIN_INTERVAL,
    SESSION_RELOGIN_NETWORK_RETRY,
    LoginFailed,
    SessionMixin,
    connected_owner_ids,
    pollable,
    without_secrets,
)
from app.services.refuel_detector import RefuelDetector
from app.services.trip_detector import TripDetector
from app.timeutil import utcnow

_LOGGER = logging.getLogger(__name__)

# Longest poll interval while a trip is open (it only asks the cloud: it does not wake the car)
OPEN_TRIP_POLL_SECONDS = 150
# Cars polled at the same time in one pass of the loop: a manager whose cloud answers slowly must not delay everybody else's reads,
# but the Chery cloud is shared, so the fan-out stays small.
POLL_CONCURRENCY = 4
# Two reads of the same car at once, or one right after the other, bring nothing newer from the cloud and ran the detectors
# twice over the same snapshot (04-11-2026: a push from the car 2 s after the scheduled read duplicated trips and charges).
# A read requested while one is running joins it; one requested this soon after an answer gets that answer.
POLL_COALESCE_SECONDS = 5
# Raw keys remembered per car (`vehicles.reported_keys`); the cloud sends about a hundred
MAX_REPORTED_KEYS = 300


_NETWORK_MARKERS = ("NameResolutionError", "Max retries exceeded", "ConnectionError", "ConnectTimeout", "ReadTimeout", "timed out")


def read_failure_group(res: Dict) -> str:
    """The cause of a read without data, to keep each one in its own group of the project's reports: `session` (the cloud no
    longer accepts the session), `network` (DNS, connection or timeout before reaching the cloud) or the probe's own reason."""
    reason = str(res.get("reason") or "unknown")
    if reason == "no_usertoken":
        return "session"
    if reason == "exception" and any(m in str(res.get("error") or "") for m in _NETWORK_MARKERS):
        return "network"
    return reason


class VehiclePoller(SessionMixin, CommandsMixin, AlertsMixin, PositionWatchMixin):
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
    # Cars whose last read found the Ebro session dead: the loop leaves them alone until there is a new session (`reads_paused`)
    _session_paused: Dict[str, Dict] = {}
    # Alert situations not yet confirmed: (vin, key) -> since when they are seen
    _pending_since: Dict[tuple, datetime.datetime] = {}
    # Polls requested by car events (one at a time per vehicle)
    _poll_requests: Dict[str, "asyncio.Task"] = {}
    _poll_semaphore: Optional[asyncio.Semaphore] = None
    # The read in progress per vehicle: whoever asks meanwhile waits for it instead of asking the cloud again
    _inflight: Dict[str, "asyncio.Task"] = {}
    # Sessions reopened with the stored password: the last attempt per Ebro account, and the accounts whose password the cloud
    # rejected (the value is the account's `last_login` at that moment: reconnecting the account changes it and lifts the stop)
    _relogin_attempt_at: Dict[int, datetime.datetime] = {}
    _session_lost: Dict[int, Optional[datetime.datetime]] = {}
    # Cars left out of the loop because their manager has no connected Ebro account (said once in the log)
    _unpolled_logged: Set[str] = set()
    _position_watch: Dict[str, Dict] = {}            # per car: the point the cloud keeps returning and since when (frozen position)
    # What the location endpoint answered the last time a car was read without a position (logged when it changes)
    _last_location_code: Dict[str, Optional[str]] = {}

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
                await self._backfill_reported_keys(session, v)
                # A sheet nobody filled in gets the figures of its model (services/vehicle_profiles.py); a new capacity re-estimates
                # the energy of its drives and charges, as saving the sheet by hand does
                changed = vehicle_profiles.fill_defaults(v)
                if changed:
                    if "battery_capacity_kwh" in changed:
                        await energy_recalc.recalculate_for_capacity(session, v)
                    _LOGGER.info("Sheet of %s filled in from its model %s: %s", v.vin[-6:], v.model, ", ".join(changed))
            if vehicles:
                await session.commit()

            # Each manager's PIN goes to that manager's cars only
            accounts = (await session.execute(select(EbroAccount).where(EbroAccount.is_active == True).order_by(EbroAccount.id))).scalars().all()  # noqa: E712
            by_owner = {a.user_id: a for a in accounts if a.user_id is not None}
            for v in vehicles:
                acc = by_owner.get(v.owner_id)
                ctx = self._contexts[v.vin]
                if acc and acc.pin and acc.pin.strip() and not (ctx.pin or "").strip():
                    ctx.pin = acc.pin.strip()

        # One Ebro session per account, shared by its cars (services/ebro_sessions.py)
        await self.bind_sessions()

        async with async_session_factory() as session:
            accounts = (await session.execute(select(EbroAccount).where(EbroAccount.is_active == True).order_by(EbroAccount.id))).scalars().all()  # noqa: E712
            # 2. Auto-discover the cars of every account whose manager has none yet (or the .env account if there is nothing else)
            owners_with_cars = {v.owner_id for v in vehicles}
            for acc in accounts:
                if acc.user_id not in owners_with_cars:
                    _LOGGER.info("Auto-discovering vehicles of the Ebro account of user %s...", acc.user_id)
                    await self.discover_vehicles(account=acc)
            if not accounts and not vehicles and settings.EBRO_PHONE and settings.EBRO_PASSWORD:
                _LOGGER.info("Auto-discovering vehicles from the environment's Ebro account...")
                await self.discover_vehicles(account=None)

    @staticmethod
    async def _backfill_reported_keys(session: AsyncSession, vehicle: Vehicle, days: int = 30, limit: int = 3000) -> None:
        """Adds to `reported_keys` the raw keys found in the car's stored readings. The list is built read by read, and some keys
        only come in some states (`remainChargeTime` only while charging): a car that has not charged since the list began looked
        like one that never sends it (09-11-2026, an S900 charged every day). The history already knows better."""
        since = utcnow() - datetime.timedelta(days=days)
        rows = (await session.execute(select(TelemetrySnapshot.raw_data).where(TelemetrySnapshot.vin == vehicle.vin, TelemetrySnapshot.timestamp >= since)
                                      .order_by(TelemetrySnapshot.timestamp.desc()).limit(limit))).scalars().all()
        seen = set(vehicle.reported_keys or [])
        fresh = {k for raw in rows if isinstance(raw, dict) for k in raw if isinstance(k, str)} - seen
        if fresh and len(seen) < MAX_REPORTED_KEYS:
            vehicle.reported_keys = sorted(seen | fresh)[:MAX_REPORTED_KEYS]
            _LOGGER.info("Reported keys of %s completed from its readings: %d added", vehicle.vin[-6:], len(fresh))

    async def discover_vehicles(self, account: Optional[EbroAccount] = None, strict: bool = False) -> List[Vehicle]:
        """Discovers the vehicles linked to the account. With the session the account already holds when it still works (Chery
        may keep one session per account, unverified: a password login could throw the official app out, so it is the last resort); otherwise it
        logs in with the stored password. `strict` raises `LoginFailed` instead of returning an empty list, so a request made
        by the manager can tell «no new car» from «the cloud refuses the password» (09-11-2026: a manager whose password the
        cloud rejected pressed «Sincronizar» twice and was told there were none)."""
        phone = None
        password = None
        area_code = "34"

        acc_user_id = None
        token_data: Optional[dict] = None
        if account is None:
            async with async_session_factory() as session:
                acc_stmt = select(EbroAccount).where(EbroAccount.is_active == True)  # noqa: E712
                account = (await session.execute(acc_stmt)).scalars().first()
        session_path: Optional[str] = None                   # the account's session, shared by its cars (ebro_sessions.py)
        if account is not None:
            phone = account.phone
            password = account.password
            area_code = account.area_code or "34"
            acc_user_id = account.user_id
            session_path = account_session_path(account.id)

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
        skip = await foreign_vins(owner_for_skip)
        v_list: Optional[List[dict]] = None
        if account is not None:
            # The session the account's cars already share, when it still opens: no password login (it might close the official app's)
            async with async_session_factory() as session:
                cars = [vin for (vin,) in (await session.execute(select(Vehicle.vin).where(Vehicle.owner_id == account.user_id,
                                                                                          Vehicle.is_manual.is_(False)))).all()]
            copies = [os.path.join(vehicle_data_dir(vin), "token.json") for vin in cars]
            await loop.run_in_executor(None, seed_account_session, account.id, copies, account.token_data, account.last_login)
            if os.path.exists(session_path) and not self.session_lost(account.id, account.last_login):
                try:
                    v_list = await loop.run_in_executor(None, fetch_account_vehicles, None, skip, session_path)
                except Exception as e:                        # noqa: BLE001 — a dead session: the password decides below
                    _LOGGER.info("The stored Ebro session of user %s could not list the cars (%s): logging in again", acc_user_id, type(e).__name__)
                    v_list = None
        if v_list is None:
            try:
                token_data = await loop.run_in_executor(None, _do_login)
            except RuntimeError as e:
                # The cloud answered and said no: a manager's wrong or changed password, not a failure of EVBro
                who = f"of user {acc_user_id}" if acc_user_id is not None else "of the environment"
                _LOGGER.warning("The Ebro cloud rejected the password of the account %s: %s", who, without_secrets(str(e)),
                                extra={"telemetry": False})
                if strict and account is not None:
                    self._session_lost[account.id] = account.last_login
                if strict:
                    raise LoginFailed(without_secrets(str(e)), rejected=True) from e
                return []
            except Exception as e:                            # noqa: BLE001 — network or cryptography: the text may name hosts, it stays in the log
                _LOGGER.warning("Could not log in to Ebro: %s", type(e).__name__)
                if strict:
                    raise LoginFailed(type(e).__name__, rejected=False) from e
                return []

            if not token_data or "access_token" not in token_data:
                # Only the shape of the answer: it may carry partial tokens or account details that do not belong in the log.
                _LOGGER.error("Invalid login answer (keys: %s)", sorted(token_data) if isinstance(token_data, dict) else type(token_data).__name__)
                if strict:
                    raise LoginFailed("invalid answer", rejected=True)
                return []

            try:
                v_list = await loop.run_in_executor(None, fetch_account_vehicles, token_data, skip, session_path)
            except Exception as e:                            # noqa: BLE001
                _LOGGER.error(f"Error fetching the vehicle list: {e}")
                v_list = []
            if account is not None:
                # The copy kept in the account: it only seeds the session file if that file is ever lost
                async with async_session_factory() as session:
                    acc = await session.get(EbroAccount, account.id)
                    if acc is not None:
                        acc.token_data = token_data
                        acc.last_login = utcnow()
                        await session.commit()
                self._session_lost.pop(account.id, None)

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
                    vehicle_profiles.fill_defaults(new_v)
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

        await self.bind_sessions()                              # the new cars read the account's session from the start
        return discovered

    async def poll_vehicle(self, vin: str, force: bool = False) -> Optional[Dict]:
        """One telemetry read of a vehicle: it joins a read already running, reuses one answered less than
        POLL_COALESCE_SECONDS ago, and otherwise asks the cloud (`_poll_vehicle`). Whatever the outcome, the time of the
        attempt is kept: a car whose reads fail is retried at its adaptive interval, not at every turn of the loop."""
        running = self._inflight.get(vin)
        if running is not None:
            return await asyncio.shield(running)
        last = self._last_poll_times.get(vin)
        if last is not None and (utcnow() - last).total_seconds() < POLL_COALESCE_SECONDS:
            return self._latest_snapshots.get(vin)
        task = asyncio.create_task(self._poll_vehicle(vin, force))
        self._inflight[vin] = task
        try:
            return await task
        finally:
            self._inflight.pop(vin, None)
            self._last_poll_times[vin] = utcnow()

    async def _poll_vehicle(self, vin: str, force: bool = False, _retry: bool = True) -> Optional[Dict]:
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

        if isinstance(res, dict) and res.get("ok") is False and res.get("reason") == "no_usertoken" and _retry:
            # The session is dead and the refresh token did not bring it back: sign in again with the stored password and retry once
            if await self._recover_session(vin):
                return await self._poll_vehicle(vin, force=True, _retry=False)
        if isinstance(res, dict) and res.get("ok") is False and res.get("reason") != "cooldown":
            # With the probe's error text: «exception» alone left 194 reports in the project's console impossible to diagnose
            detail = f" ({without_secrets(str(res.get('error')))[:200]})" if res.get("error") else ""
            group = read_failure_group(res)
            # A session the cloud keeps closing is the account's (its owner signed in elsewhere, e.g. the official app): it stays
            # in the local log but is not reported to the project as a fault of the code
            extra = {"telemetry_group": group, "telemetry": group != "session"}
            # Without a session every read fails the same way: the loop stops reading the car until there is a new one, and it is
            # said once (16-11-2026: 276 identical lines, and as many requests to the cloud, in five hours for one car)
            first = self._pause_reads(vin) if group == "session" else True
            pause = "; reads paused until there is a new session" if group == "session" else ""
            _LOGGER.log(logging.WARNING if first else logging.DEBUG, "Read of %s brought no data: %s%s%s", vin[-6:],
                        res.get("reason"), detail, pause, extra=extra)
        if not (isinstance(res, dict) and res.get("reason") in ("no_usertoken", "cooldown")):
            if self._session_paused.pop(vin, None) is not None:
                _LOGGER.info("Reads of %s resumed: the Ebro session works again", vin[-6:])

        # With no real-time data (e.g. the car is asleep), use the probe's "rich" summary.
        # Coordinates do NOT travel in the result of `probe_once` (see core/probe.py):
        # raw data only arrives through the `on_data` callback above.
        if not raw_data and isinstance(res, dict):
            raw_data = res.get("rich", {})

        cloud_codes = res.get("codes") if isinstance(res, dict) else None
        if not raw_data:
            usage_reports.count_poll("empty")
            if cloud_codes:
                _LOGGER.info("Read of %s came back empty, cloud codes (realtime, location, trips): %s", vin[-6:], cloud_codes)
            return None
        usage_reports.count_poll("ok")

        # Normalise the telemetry
        # The MQTT push wins over the poll when both carry the same key: the poll sometimes returns
        # a cloud snapshot older than the car's last event.
        telemetry = parse_telemetry_data(raw_data, None, CarEventHub.get_instance().fields_for(vin))
        # Mid-charge, a read without the realtime answer would look like «plugged, not charging»: it would raise a false alert and
        # stretch the next read to the plugged interval, splitting the charge. It is dropped and the next read comes on time.
        if lacks_charge_data(telemetry):
            _LOGGER.info("Read of %s without charge data, cloud codes (realtime, location, trips): %s", vin[-6:], cloud_codes)
            if ChargeDetector.has_active_charge(vin):
                return self._latest_snapshots.get(vin)
        if telemetry.get("latitude") is None and isinstance(res, dict) and res.get("got_realtime"):
            # Telemetry without a position: say what the location endpoint answered, once per change (two S800 never got one,
            # 08-11-2026, and the reason was nowhere to be read)
            code = res.get("location_code")
            if self._last_location_code.get(vin, "") != code:
                self._last_location_code[vin] = code
                _LOGGER.info("Read of %s without a position: the location endpoint answered %s (%s)", vin[-6:], code, codes.meaning(code))
        self._latest_snapshots[vin] = telemetry

        # Save to the database and run the detectors
        async with async_session_factory() as session:
            v = await session.execute(select(Vehicle).where(Vehicle.vin == vin))
            vehicle = v.scalar_one_or_none()
            if not vehicle:
                return telemetry

            # The raw keys this car has ever sent: the interface and the Home Assistant discovery show it only what it can report
            # (the models differ; docs/ebro-protocol.md, «Qué manda cada modelo»)
            seen = set(vehicle.reported_keys or [])
            fresh = {k for k in (telemetry.get("raw_data") or {}) if isinstance(k, str)} - seen
            if fresh and len(seen) < MAX_REPORTED_KEYS:
                vehicle.reported_keys = sorted(seen | fresh)[:MAX_REPORTED_KEYS]

            # Update the quick fields on the vehicle
            vehicle.state = telemetry["state"]
            vehicle.last_seen = utcnow()
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
        await self._watch_frozen_position(vin, telemetry)

        # Publish to MQTT if enabled
        if publish_mqtt:
            MqttPublisher.publish_telemetry(vin, telemetry, vehicle)
        MqttHub.get_instance().publish(vehicle.owner_id, vin, telemetry, vehicle)      # the owner's own broker, if they linked one
        # Notify the web UI's WebSockets
        self.broadcast_telemetry(vin, telemetry)

        return telemetry

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
        now = utcnow()
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
                await self.bind_sessions()                    # a connected, changed or new account takes effect on the next pass
                async with async_session_factory() as session:
                    vehicles = (await session.execute(select(Vehicle).where(Vehicle.is_manual.is_(False)))).scalars().all()
                    connected = await connected_owner_ids(session)
                    lost = await self.lost_owner_ids(session)
                    stored_states = {v.vin: v.state for v in vehicles if pollable(v, connected, lost)}
                    owners = {v.vin: v.owner_id for v in vehicles}
                    account_of = {uid: acc_id for (acc_id, uid) in (await session.execute(     # the newest account of a manager wins
                        select(EbroAccount.id, EbroAccount.user_id).where(EbroAccount.is_active == True).order_by(EbroAccount.id))).all()}  # noqa: E712
                    for v in vehicles:
                        if v.vin not in stored_states and v.vin not in self._unpolled_logged:
                            # A disconnected account: its token is dead and the cloud would only say so every hour (seen on a real install, 2026-11-08)
                            if v.owner_id in lost:
                                _LOGGER.info("Vehicle %s is not polled: the Ebro cloud rejected its manager's account; it has to be reconnected", v.vin[-6:])
                            else:
                                _LOGGER.info("Vehicle %s is not polled: its manager has no connected Ebro account", v.vin[-6:])
                            self._unpolled_logged.add(v.vin)
                        elif v.vin in stored_states:
                            self._unpolled_logged.discard(v.vin)

                due = []
                for vin, stored_state in stored_states.items():
                    if self.reads_paused(vin, account_of.get(owners.get(vin))):
                        continue                              # its session is dead: asking again would only get another «no»
                    latest = self.get_latest(vin) or {}
                    # After a restart nothing is in memory: a car left plugged in stays on the plugged interval while the
                    # reads come back empty (05-11-2026: read hourly as «parked», a charge went unseen for 2.5 hours)
                    current_state = latest.get("state") or ("plugged" if stored_state in ("plugged", "charging") else "parked")
                    last_poll = self._last_poll_times.get(vin)
                    now = utcnow()

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
        now = utcnow()
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
        now = utcnow()
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
