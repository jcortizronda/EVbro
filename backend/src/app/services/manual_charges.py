"""Charges the user writes down: the ones from before EVbro, or one it missed. Typed one by one (Carga → «Añadir carga») or
brought from a spreadsheet (services/charge_import.py).

Two sides of a charge, as with the detected ones:
  * CHARGER side: the kWh the charger or the invoice shows (`kwh_used`, the same field the meter reading of a detected charge goes
    to). They are given, or follow from the total paid and the price per kWh. The cost is worked out over them, and when the
    battery % gained is given too, what reached the battery is estimated (% × usable capacity) and the efficiency follows.
  * BATTERY side: no charger kWh, but the battery % gained: what reached the battery is estimated from it (`kwh_added`) and the
    price per kWh, if any, is applied to that. There is no efficiency: there is nothing to compare it with.
The % is never worked out from the kWh, nor the other way round. Any two of kWh, price per kWh and total paid give the third.

The day, the hour, the place and the cost are optional. Without a day the charge is filed the day before the car joined EVbro
(`date_unknown`), so that it still adds up in the totals without landing in a real month. A manual charge has no curve and no
start/end percentages: the battery-health figures never see it. The place is one of the manager's geofences (the charge is then
counted with the ones detected there) or free text.
"""
from __future__ import annotations

import dataclasses
import datetime
from typing import Any, Dict, List, Optional

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_timezone
from app.db.models import ChargingSession, Geofence, Vehicle

KWH_MAX = 500.0
PRICE_MAX = 5.0
COST_MAX = 10000.0
PLACE_MAX = 200
DEFAULT_HOUR = datetime.time(12, 0)            # a day without an hour: midday, far from both ends of the day
EFFICIENCY_RANGE = (0.5, 1.0)                   # battery / charger: outside it the % or the capacity is wrong, and nothing is estimated


class ChargeError(ValueError):
    """What was asked for cannot be a charge (the message is shown to the user; `code` names the field for the import review)."""

    def __init__(self, message: str, code: str = "invalid") -> None:
        super().__init__(message)
        self.code = code


@dataclasses.dataclass
class ChargeData:
    kwh: Optional[float] = None                 # the charger's
    day: Optional[datetime.date] = None
    hour: Optional[datetime.time] = None
    battery_gain_pct: Optional[float] = None
    price_per_kwh: Optional[float] = None
    total_cost: Optional[float] = None
    free: bool = False
    geofence_id: Optional[int] = None
    place: Optional[str] = None
    charge_type: str = "ac"


def clean_place(text: Optional[str]) -> Optional[str]:
    place = " ".join((text or "").split())
    return place[:PLACE_MAX] or None


def charger_kwh(data: ChargeData) -> Optional[float]:
    """The charger's kWh: given, or the total paid over the price per kWh."""
    if data.kwh:
        return round(float(data.kwh), 2)
    if data.total_cost and data.price_per_kwh:
        return round(data.total_cost / data.price_per_kwh, 2)
    return None


def check(data: ChargeData) -> None:
    if data.kwh is not None and not 0 < data.kwh <= KWH_MAX:
        raise ChargeError("Indica los kWh cargados (entre 0 y 500).", "kwh")
    if data.battery_gain_pct is not None and not 0 < data.battery_gain_pct <= 100:
        raise ChargeError("La batería cargada va de 1 a 100 %.", "battery")
    kwh = charger_kwh(data)
    if kwh is not None and not 0 < kwh <= KWH_MAX:
        raise ChargeError("Indica los kWh cargados (entre 0 y 500).", "kwh")
    if kwh is None and not data.battery_gain_pct:
        raise ChargeError("Indica los kWh del cargador, el total y el precio por kWh, o la batería cargada (%).", "energy")
    if data.price_per_kwh is not None and not 0 <= data.price_per_kwh <= PRICE_MAX:
        raise ChargeError("El precio por kWh no es válido.", "cost")
    if data.total_cost is not None and not 0 <= data.total_cost <= COST_MAX:
        raise ChargeError("El total pagado no es válido.", "cost")
    if data.charge_type not in ("ac", "dc"):
        raise ChargeError("El tipo de carga es AC o DC.", "type")
    if data.day is not None and data.day > datetime.datetime.now(get_timezone()).date() + datetime.timedelta(days=1):
        raise ChargeError("La fecha no puede ser futura.", "future")


def battery_kwh(charger_kwh: float, gain_pct: Optional[float], capacity_kwh: Optional[float]) -> Optional[float]:
    """What reached the battery, from the % it gained and the usable capacity; None when it cannot be told believably."""
    if not gain_pct or not capacity_kwh or charger_kwh <= 0:
        return None
    estimate = round(gain_pct / 100.0 * capacity_kwh, 2)
    return estimate if EFFICIENCY_RANGE[0] <= estimate / charger_kwh <= EFFICIENCY_RANGE[1] else None


def to_utc(day: datetime.date, hour: Optional[datetime.time]) -> datetime.datetime:
    """The local day and hour as the naive UTC the database keeps."""
    local = datetime.datetime.combine(day, hour or DEFAULT_HOUR, tzinfo=get_timezone())
    return local.astimezone(datetime.timezone.utc).replace(tzinfo=None)


def local_day(moment: datetime.datetime) -> datetime.date:
    return moment.replace(tzinfo=datetime.timezone.utc).astimezone(get_timezone()).date()


async def undated_moment(db: AsyncSession, vehicle: Vehicle) -> datetime.datetime:
    """Where a charge without a day is filed: the day before the car joined EVbro."""
    joined = vehicle.created_at or datetime.datetime.utcnow()
    return to_utc(local_day(joined) - datetime.timedelta(days=1), DEFAULT_HOUR)


async def _geofence(db: AsyncSession, vehicle: Vehicle, geofence_id: int) -> Geofence:
    geofence = await db.get(Geofence, geofence_id)
    if geofence is None or geofence.owner_id != vehicle.owner_id:
        raise ChargeError("Esa geocerca no existe.", "place")
    return geofence


async def fill(db: AsyncSession, charge: ChargingSession, vehicle: Vehicle, data: ChargeData) -> ChargingSession:
    """Writes `data` into `charge` (a new one or a manual one being corrected). Does not commit."""
    check(data)
    kwh = charger_kwh(data)                              # None: battery side
    gain = round(float(data.battery_gain_pct), 1) if data.battery_gain_pct else None
    if kwh is None and not vehicle.battery_capacity_kwh:
        raise ChargeError("Sin los kWh del cargador hace falta la capacidad de la batería en la ficha del coche.", "energy")
    if data.day is not None:
        charge.start_time, charge.date_unknown = to_utc(data.day, data.hour), False
    else:
        charge.start_time, charge.date_unknown = await undated_moment(db, vehicle), True
    charge.end_time = charge.start_time                      # closed: a charge without an end would look «in progress»
    charge.duration_seconds = 0
    charge.manual = True
    charge.battery_gain_pct = gain
    if kwh is not None:                                  # charger side: the battery is estimated only when it is believable
        charge.kwh_used = kwh
        charge.kwh_added = battery_kwh(kwh, gain, vehicle.battery_capacity_kwh) or kwh
    else:                                                # battery side: what the % says reached it
        charge.kwh_used = None
        charge.kwh_added = round(gain / 100.0 * vehicle.battery_capacity_kwh, 2)
    energy = kwh if kwh is not None else charge.kwh_added      # what the price applies to
    charge.start_battery_pct = charge.end_battery_pct = None
    charge.max_charge_power_kw = 0.0
    charge.charge_type = data.charge_type

    if data.geofence_id is not None:
        geofence = await _geofence(db, vehicle, data.geofence_id)
        charge.geofence_id, charge.address = geofence.id, geofence.name
        charge.latitude, charge.longitude = geofence.latitude, geofence.longitude
    else:
        charge.geofence_id, charge.address = None, clean_place(data.place)
        charge.latitude = charge.longitude = None

    if data.free or data.total_cost == 0 or data.price_per_kwh == 0:
        charge.charge_cost, charge.effective_cost_per_kwh, charge.cost_mode = 0.0, 0.0, "free"
    elif data.total_cost is not None:
        charge.charge_cost = round(float(data.total_cost), 2)
        charge.effective_cost_per_kwh = round(charge.charge_cost / energy, 4)
        charge.cost_mode = "manual"
    elif data.price_per_kwh is not None:
        charge.effective_cost_per_kwh = round(float(data.price_per_kwh), 4)
        charge.charge_cost = round(charge.effective_cost_per_kwh * energy, 2)
        charge.cost_mode = "manual"
    else:
        charge.charge_cost, charge.effective_cost_per_kwh, charge.cost_mode = 0.0, None, None
    return charge


async def create(db: AsyncSession, vehicle: Vehicle, data: ChargeData, batch: Optional[str] = None) -> ChargingSession:
    charge = await fill(db, ChargingSession(vin=vehicle.vin, import_batch=batch), vehicle, data)
    db.add(charge)
    return charge


def out(charge: ChargingSession) -> Dict[str, Any]:
    """A manual charge as the form needs it back (local day and hour)."""
    local = charge.start_time.replace(tzinfo=datetime.timezone.utc).astimezone(get_timezone())
    return {"id": charge.id, "vin": charge.vin, "manual": bool(charge.manual), "date_unknown": bool(charge.date_unknown),
            "day": None if charge.date_unknown else local.date().isoformat(),
            "hour": None if charge.date_unknown else local.strftime("%H:%M"),
            "kwh": charge.kwh_used, "battery_kwh": charge.kwh_added, "battery_gain_pct": charge.battery_gain_pct, "charge_type": charge.charge_type or "ac",
            "geofence_id": charge.geofence_id, "place": None if charge.geofence_id else charge.address,
            "free": charge.cost_mode == "free", "total_cost": charge.charge_cost if charge.cost_mode == "manual" else None,
            "price_per_kwh": charge.effective_cost_per_kwh if charge.cost_mode == "manual" else None,
            "import_batch": charge.import_batch}


def energy_kwh(charge: ChargingSession) -> float:
    """The energy a charge counts with in the totals and the costs: the charger's when it is known (a manual charge, or a
    detected one whose meter reading was entered), otherwise what the car measured. An estimate by nature: the two sides differ
    by the losses of the charge."""
    return charge.kwh_used or charge.kwh_added or 0.0


TARIFF_FIXED, TARIFF_FREE = "fixed", "free"


async def places(db: AsyncSession, vehicle: Vehicle) -> Dict[str, List[Dict[str, Any]]]:
    """What the «Dónde» list of the form offers: the manager's geofences (with the price the form can fill in by itself: a fixed
    tariff or a free one; hourly tariffs need the hours of the charge) and the places already typed in other manual charges."""
    geofences = (await db.execute(select(Geofence).where(Geofence.owner_id == vehicle.owner_id).order_by(Geofence.name))).scalars().all()
    typed = (await db.execute(
        select(ChargingSession.address, func.count()).where(
            ChargingSession.vin == vehicle.vin, ChargingSession.manual.is_(True), ChargingSession.geofence_id.is_(None),
            ChargingSession.address.is_not(None)).group_by(ChargingSession.address).order_by(func.count().desc(), ChargingSession.address))).all()
    return {
        "geofences": [{"id": g.id, "name": g.name, "tariff": g.cost_mode or TARIFF_FIXED,
                       "price_per_kwh": g.cost_per_kwh if (g.cost_mode or TARIFF_FIXED) == TARIFF_FIXED else None,
                       "free": g.cost_mode == TARIFF_FREE} for g in geofences if vehicle.owner_id is not None],
        "places": [{"name": name} for name, _n in typed][:30],
    }
