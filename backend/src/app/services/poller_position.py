"""Frozen position: the cloud repeating one point while the car drives, and the automatic locate experiment.

Part of `VehiclePoller` (services/poller.py), split out on 2026-11-09 without changing behaviour. docs/trips.md, «Posición congelada».
"""
from __future__ import annotations

import datetime
import logging
from typing import Dict, Optional

from app.config import settings
from app.timeutil import utcnow

_LOGGER = logging.getLogger("app.services.poller")

# Frozen position (docs/trips.md, «Posición congelada»): the same point for this long since the odometer started to advance, and
# this many km, is the cloud repeating an old position, not a parked car. `AUTO_LOCATE_ENABLED` then sends «Localizar coche (GPS)»,
# one per gap at most. The odometer counts whole km, so 1 km can be a few metres (1364.9 → 1365): 12/13/14-11-2026, four false
# alarms of exactly 1.0 km, one of them «1097 min frozen» for a car asleep overnight whose first reading of the morning had ticked.
# The km only count on readings with the car on: an odometer that advanced between two readings of a sleeping car is a drive
# EVbro did not see, not a position stuck while driving (16-11-2026: +4 km an hour after a 98 km trip, the car asleep at its real
# position after a short round trip; taken for a frozen position, it got three «Localizar» in three hours, each one waking it
# up). For the same reason the fixes asked per episode are limited, and a car that is off gets none for a frozen position. The one
# exception is the moment it stops: a car that drove 2 km or more on its last point asks once for where it really stopped
# (21-11-2026: the cloud only moved the point after each locate, and the map showed the car 2 km from home after parking there).
FROZEN_POSITION_MIN = 3
FROZEN_POSITION_KM = 2.0
AUTO_LOCATE_GAP_MIN = 5
AUTO_LOCATE_MAX = 2
CAR_ON_STATES = ("driving", "ready")


class PositionWatchMixin:
    """Methods of `VehiclePoller`; the state they use is declared there."""

    async def _watch_frozen_position(self, vin: str, telemetry: Dict, now: Optional[datetime.datetime] = None) -> None:
        """Notices when the cloud repeats the same position while the car drives (an S900, 31-10 and 03/04-11-2026: hours with one
        point and up to 27 km per trip without a trace; the realtime data was fresh, only the position store stalled) and, with
        `AUTO_LOCATE_ENABLED`, asks the car for a fix («Localizar coche (GPS)», which gave a fresh point every time it was sent
        by hand), AUTO_LOCATE_MAX times per episode at most. The result is logged either way so the experiment can be judged
        from the log."""
        lat, lon, odo = telemetry.get("latitude"), telemetry.get("longitude"), telemetry.get("odometer_km")
        if lat is None or lon is None or odo is None:
            return
        now = now or utcnow()
        point = (round(float(lat), 4), round(float(lon), 4))
        watch = self._position_watch.get(vin)
        if watch is None or watch["point"] != point:
            if watch is not None and watch.get("frozen"):
                since_locate = f", {int((now - watch['locate_at']).total_seconds())} s after the automatic locate" if watch.get("locate_at") else ""
                _LOGGER.info("Position of %s moves again after %.0f min and %.1f km frozen%s", vin[-6:],
                             (now - watch["moving_since"]).total_seconds() / 60, odo - watch["odo"], since_locate)
            self._position_watch[vin] = {"point": point, "since": now, "odo": odo, "moving_since": None, "frozen": False,
                                         "last_locate": (watch or {}).get("last_locate"), "locate_at": None, "no_pin": False,
                                         "locates": 0}
            return
        km = odo - watch["odo"]
        if km <= 0:
            return                                                   # parked: the point may stay for days
        if telemetry.get("state") not in CAR_ON_STATES:
            if watch.get("moving_since") is not None and km >= FROZEN_POSITION_KM and watch.get("locate_at") is None:
                # It drove on this point with the car on and has just stopped: the point is from mid-route and would stay on
                # the map until the next trip (21-11-2026: home on the screen 2 km away, the last fix one minute after a locate
                # in the middle of the trip). The car is still awake: one fix for where it really stopped.
                await self._auto_locate(vin, watch, now, f"stopped {km:.1f} km after its last point")
            # The odometer advanced with the car off: a drive nobody saw between two readings, or the first reading of the
            # morning. Nothing to compare the position with, so the count starts again from here; and a car that is off gets
            # no more fixes for a frozen position (each one would wake it up).
            watch["odo"], watch["moving_since"] = odo, None
            return
        if watch.get("moving_since") is None:
            watch["moving_since"] = now                              # the clock starts when the car moves, not when the point was first seen
        minutes = (now - watch["moving_since"]).total_seconds() / 60
        if minutes < FROZEN_POSITION_MIN or km < FROZEN_POSITION_KM:
            return                                                   # too early to tell
        if not watch["frozen"]:
            watch["frozen"] = True
            _LOGGER.warning("Frozen position on %s: the cloud has repeated the same point for %.0f min while the car drove %.1f km",
                            vin[-6:], minutes, km)
        if watch.get("locates", 0) >= AUTO_LOCATE_MAX:
            return
        last = watch.get("last_locate")
        if last is not None and now - last < datetime.timedelta(minutes=AUTO_LOCATE_GAP_MIN):
            return
        await self._auto_locate(vin, watch, now, f"position frozen for {minutes:.0f} min and {km:.1f} km")

    async def _auto_locate(self, vin: str, watch: Dict, now: datetime.datetime, why: str) -> None:
        """Sends «Localizar coche (GPS)» with the stored PIN (origin `auto_locate`) when the experiment is on, and logs the outcome."""
        if not settings.AUTO_LOCATE_ENABLED or watch.get("no_pin"):
            return
        watch["last_locate"] = now
        result = await self.execute_internal(vin, "localizar_coche_gps", None, origin="auto_locate")
        if result.get("success"):
            watch["locate_at"] = now
            watch["locates"] = watch.get("locates", 0) + 1
            _LOGGER.info("Automatic locate sent to %s (%s)", vin[-6:], why)
        elif result.get("reason") == "pin":
            watch["no_pin"] = True                                   # the account has no PIN saved: the manager's choice, not retried
            _LOGGER.info("Automatic locate for %s skipped: the account has no PIN saved", vin[-6:])
        else:
            _LOGGER.warning("Automatic locate for %s not accepted: %s", vin[-6:], result.get("message"))
