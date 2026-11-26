"""EVbro's maintenance recommendations: checks worth doing that are NOT in the brand's plan, timed by how the car is used.

The official plan covers oil, filters, brake fluid, spark plugs, coolant and transmission oil. What it leaves out is what a
plug-in hybrid wears differently: brakes that hardly work (regenerative braking) and rust, petrol that sits for months, tyres
that carry more weight, a 12 V battery that fails without warning, wiper blades. Each recommendation has an interval; when it
comes due the owner gets one notice and answers it in Mantenimiento: «Revisado, está bien» (it counts again from that day and
those km), «Registrar intervención» (the same, from the intervention) or «Recuérdamelo más adelante» (30 days, or 1,000 km for
the ones that count km, whichever comes first). Each one can be switched off.

The intervals are general workshop practice, NOT EBRO's data (docs/maintenance.md, «Recomendaciones»): they are advice.

| key           | due                                                                 | counts from                                             |
| brakes        | 15,000 km or 12 months                                              | last check, new pads/discs on the sheet, or delivery    |
| fuel_age      | 4 months without a refuel (again at 6)                              | last refuel or last check                               |
| tyre_rotation | 10,000 km (8,000 with more than half the km on fast roads)          | last check, new tyres on the sheet, or delivery         |
| tyre_age      | 5 years after they were made/fitted, then every year                | the tyres' DOT or fitting date, else delivery           |
| battery_12v   | 3 years after it was fitted, then every year                        | the battery's fitting date, else delivery               |
| wipers        | 12 months                                                           | last check, new blades on the sheet, or delivery        |

A car on a CUSTOM plan that already has a task for one of them (tyre rotation, wiper blades, 12 V battery, brake pads) does not
get that recommendation: the task rules.

`evaluate()` is pure (facts in, recommendations out); `collect()` reads the facts from the database.
"""
from __future__ import annotations

import datetime
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Drive, MaintenanceAdvice, MaintenanceItem, MaintenancePlan, Refuel, Vehicle, VehiclePart
from app.i18n import M, Message
from app.services import maintenance_official as official
from app.timeutil import utcnow

BRAKES_KM, BRAKES_MONTHS = 15000, 12
FUEL_MONTHS, FUEL_SECOND_MONTHS = 4, 6
ROTATION_KM, ROTATION_FAST_KM = 10000, 8000
FAST_ROAD_KMH = 90.0               # a drive averaging this or more is a fast-road drive
FAST_ROAD_SHARE = 0.5              # more than this share of the km on fast roads brings the rotation forward
FAST_ROAD_MIN_KM = 1000.0          # with fewer km of drives in the last year the share says nothing
TYRE_AGE_MONTHS = 60
BATTERY_MONTHS = 36
YEARLY_MONTHS = 12                 # tyres past 5 years and the 12 V battery past 3: looked at every year
WIPERS_MONTHS = 12
EV_SHARE_RUST = 0.70               # above this share of electric km the brake note talks about rust on the discs
SNOOZE_DAYS, SNOOZE_KM = 30, 1000
SEEN_MARK = "_seen"            # alert_states key (group «advice») left by the poller's first look at a car, which is silent

# key → what it is called, why, and what «Registrar intervención» starts with (title, category of the intervention, part of the sheet)
ADVICE: Dict[str, Dict[str, Any]] = {
    "brakes": {"name": "Revisar pastillas y discos de freno", "category": "Frenos", "part": "brake_pads",
               "why": "Con la frenada regenerativa las pastillas trabajan poco y los discos pueden oxidarse o agarrotarse. Pide que midan las pastillas y miren los discos."},
    "fuel_age": {"name": "Gasolina envejecida en el depósito", "category": None, "part": None,
                 "why": "La gasolina pierde propiedades con los meses. Conviene gastarla circulando con el motor de gasolina y repostar gasolina nueva."},
    "tyre_rotation": {"name": "Rotar los neumáticos y mirar el dibujo", "category": "Neumáticos", "part": None,
                      "why": "Un híbrido enchufable pesa más y gasta antes las ruedas delanteras. Al rotarlas, comprueba que el dibujo pasa de 3 mm (el mínimo legal es 1,6 mm)."},
    "tyre_age": {"name": "Edad de los neumáticos", "category": "Neumáticos", "part": "tyres",
                 "why": "La goma envejece aunque quede dibujo. A partir de los 5 años conviene que los revisen cada año, y cambiarlos antes de los 10."},
    "battery_12v": {"name": "Batería de 12 V", "category": "General", "part": "battery_12v",
                    "why": "En un híbrido enchufable la batería de 12 V suele fallar sin avisar, porque el motor no arranca con ella. Pide que midan su estado."},
    "wipers": {"name": "Escobillas limpiaparabrisas", "category": "General", "part": "wiper_blades",
               "why": "La goma se endurece con el sol y deja de limpiar bien. Cámbialas si dejan rayas o hacen ruido."},
}
ORDER = ("brakes", "tyre_rotation", "fuel_age", "tyre_age", "battery_12v", "wipers")
# a custom task whose name has one of these words already covers the recommendation
TASK_WORDS = {"brakes": ("pastilla",), "tyre_rotation": ("rotaci",), "battery_12v": ("12 v", "12v"), "wipers": ("escobilla",)}


@dataclass
class State:
    """What the owner answered to one recommendation (a row of `maintenance_advice`)."""
    checked_at: Optional[datetime.datetime] = None
    checked_odometer: Optional[float] = None
    snooze_until: Optional[datetime.datetime] = None
    snooze_odometer: Optional[float] = None
    enabled: bool = True


@dataclass
class Part:
    category: str
    position: Optional[str] = None
    installed_at: Optional[datetime.datetime] = None
    installed_odometer: Optional[float] = None
    dot: Optional[str] = None            # tyres: week and year of manufacture, «2324»


@dataclass
class Facts:
    odometer: float
    start_odometer: float = 0.0                              # the km the car was delivered with
    start_date: Optional[datetime.datetime] = None           # delivery (or registration); None = months are not watched
    ev_share: Optional[float] = None                         # share of the km driven on electricity since new (0–1)
    fast_share: Optional[float] = None                       # share of last year's km in drives averaging FAST_ROAD_KMH or more
    last_refuel: Optional[datetime.datetime] = None
    parts: List[Part] = field(default_factory=list)
    custom_tasks: List[str] = field(default_factory=list)    # names of the car's own tasks, when its plan is the custom one


add_months = official.add_months            # calendar months, clamped to the end of the month


def dot_date(dot: Optional[str]) -> Optional[datetime.datetime]:
    """«2324» (week 23 of 2024) → the Monday of that week; None when it is not a DOT date."""
    text = "".join(ch for ch in str(dot or "") if ch.isdigit())
    if len(text) != 4:
        return None
    week, year = int(text[:2]), 2000 + int(text[2:])
    if not 1 <= week <= 53:
        return None
    try:
        return datetime.datetime.fromisocalendar(year, min(week, 52), 1)
    except ValueError:
        return None


def _latest(dates: List[Optional[datetime.datetime]]) -> Optional[datetime.datetime]:
    known = [d for d in dates if d is not None]
    return max(known) if known else None


def _base(facts: Facts, state: State, part_categories: tuple, positions: Optional[tuple] = None) -> tuple:
    """Where a recommendation counts from: (km, date, kind). The latest of: the car's delivery, a part of the sheet fitted
    later (new pads, new tyres…) and the owner's last «revisado». `kind` says which one (for the text shown)."""
    parts = [p for p in facts.parts if p.category in part_categories and (positions is None or p.position in positions or p.position is None)]
    candidates = [("start", facts.start_odometer, facts.start_date)]
    for p in parts:
        if p.installed_at is not None or p.installed_odometer is not None:
            candidates.append(("part", p.installed_odometer, p.installed_at))
    if state.checked_at is not None or state.checked_odometer is not None:
        candidates.append(("check", state.checked_odometer, state.checked_at))
    km = max((c[1] for c in candidates if c[1] is not None), default=0.0)
    date = _latest([c[2] for c in candidates])
    # which one decides: the latest by date (by km when there are no dates); on a tie, the check, then the part
    kind, best = "start", (datetime.datetime.min, -1.0)
    for name, c_km, c_date in candidates:
        rank = (c_date or datetime.datetime.min, c_km if c_km is not None else -1.0)
        if rank >= best:
            kind, best = name, rank
    return km, date, kind


def _one(key: str, facts: Facts, state: State, now: datetime.datetime) -> Optional[Dict[str, Any]]:
    """One recommendation, or None when the car gives nothing to count from."""
    spec = ADVICE[key]
    out: Dict[str, Any] = {"key": key, "name": M(spec["name"]), "why": M(spec["why"]), "note": None, "category": spec["category"],
                           "part": spec["part"], "can_log": spec["category"] is not None, "interval_km": None, "interval_months": None,
                           "base_odometer": None, "base_date": None, "base_kind": None, "due_odometer": None, "due_date": None, "band": "due"}
    if key == "brakes":
        km, date, kind = _base(facts, state, ("brake_pads", "brake_discs"))
        out.update(interval_km=BRAKES_KM, interval_months=BRAKES_MONTHS, base_odometer=km, base_date=date, base_kind=kind,
                   due_odometer=km + BRAKES_KM, due_date=add_months(date, BRAKES_MONTHS) if date else None)
        if facts.ev_share is not None and facts.ev_share > EV_SHARE_RUST:
            out["note"] = M("Tu coche hace el {p0} % de los km en eléctrico: fíjate sobre todo en el óxido de los discos.", p0=round(facts.ev_share * 100))
    elif key == "tyre_rotation":
        fast = facts.fast_share is not None and facts.fast_share > FAST_ROAD_SHARE
        interval = ROTATION_FAST_KM if fast else ROTATION_KM
        km, date, kind = _base(facts, state, ("tyres",), ("front", "rear"))
        out.update(interval_km=interval, base_odometer=km, base_date=date, base_kind=kind, due_odometer=km + interval)
        if fast:
            out["note"] = M("Haces más de la mitad de los km por vías rápidas: se adelanta de {p0} a {p1} km.", p0=_thousands(ROTATION_KM), p1=_thousands(ROTATION_FAST_KM))
    elif key == "wipers":
        _km, date, kind = _base(facts, state, ("wiper_blades",), ("front",))
        if date is None:
            return None
        out.update(interval_months=WIPERS_MONTHS, base_date=date, base_kind=kind, due_date=add_months(date, WIPERS_MONTHS))
    elif key == "fuel_age":
        if facts.last_refuel is None:
            return None                                      # no refuel on record: nothing to count from
        checked = state.checked_at if state.checked_at and state.checked_at > facts.last_refuel else None
        base = checked or facts.last_refuel
        out.update(interval_months=FUEL_MONTHS, base_date=base, base_kind="check" if checked else "refuel", due_date=add_months(base, FUEL_MONTHS),
                   band="m6" if now >= add_months(base, FUEL_SECOND_MONTHS) else "m4")
    elif key in ("tyre_age", "battery_12v"):
        first = TYRE_AGE_MONTHS if key == "tyre_age" else BATTERY_MONTHS
        if key == "tyre_age":
            tyres = [p for p in facts.parts if p.category == "tyres" and p.position in ("front", "rear", None)]
            made = [d for d in (dot_date(p.dot) or p.installed_at for p in tyres) if d is not None]
            born, kind = (min(made), "part") if made else (facts.start_date, "start")       # the oldest tyre decides
        else:
            fitted = _latest([p.installed_at for p in facts.parts if p.category == "battery_12v"])
            born, kind = (fitted, "part") if fitted else (facts.start_date, "start")
        if born is None:
            return None
        due = add_months(born, first)
        if state.checked_at is not None and state.checked_at >= born:
            due, kind = max(due, add_months(state.checked_at, YEARLY_MONTHS)), "check"
        out.update(interval_months=first, base_date=state.checked_at if kind == "check" else born, base_kind=kind, due_date=due)

    odo = facts.odometer
    due = (out["due_odometer"] is not None and odo >= out["due_odometer"]) or (out["due_date"] is not None and now >= out["due_date"])
    # «Recuérdamelo más adelante»: until its date or its km, whichever comes first
    waits = [w for w in ((now < state.snooze_until) if state.snooze_until else None,
                         (odo < state.snooze_odometer) if state.snooze_odometer is not None and out["due_odometer"] is not None else None) if w is not None]
    snoozed = bool(waits) and all(waits)
    out["status"] = "off" if not state.enabled else "snoozed" if due and snoozed else "due" if due else "ok"
    out["enabled"] = state.enabled
    out["remaining_km"] = round(out["due_odometer"] - odo) if out["due_odometer"] is not None else None
    out["remaining_days"] = (out["due_date"].date() - now.date()).days if out["due_date"] is not None else None
    out["snooze_until"] = state.snooze_until if out["status"] == "snoozed" else None
    out["snooze_odometer"] = state.snooze_odometer if out["status"] == "snoozed" and out["due_odometer"] is not None else None
    return out


def _thousands(n: float) -> str:
    return f"{int(round(n)):,}".replace(",", ".")


def evaluate(facts: Facts, states: Dict[str, State], now: Optional[datetime.datetime] = None) -> List[Dict[str, Any]]:
    """Every recommendation that applies to this car, due ones first (then the order of `ORDER`)."""
    now = now or utcnow()
    tasks = [t.lower() for t in facts.custom_tasks]
    out = []
    for key in ORDER:
        if any(word in task for word in TASK_WORDS.get(key, ()) for task in tasks):
            continue                                         # the car's own plan already has a task for it
        item = _one(key, facts, states.get(key) or State(), now)
        if item is not None:
            out.append(item)
    rank = {"due": 0, "snoozed": 1, "ok": 2, "off": 3}
    return sorted(out, key=lambda a: (rank[a["status"]], ORDER.index(a["key"])))


# ───────────────────────── database ─────────────────────────
async def facts_of(session: AsyncSession, vehicle: Vehicle, now: datetime.datetime, odometer: Optional[float] = None) -> Facts:
    """`odometer`: the reading in hand, when the caller has one newer than the stored one (the poller, before it commits)."""
    vin = vehicle.vin
    plan = await session.get(MaintenancePlan, vin)
    mode = (plan.mode if plan else "official") if official.is_ebro(vehicle.brand) else "custom"     # as services/maintenance_alerts.py
    custom = []
    if mode == "custom":
        custom = [name for (name,) in (await session.execute(
            select(MaintenanceItem.name).where(MaintenanceItem.vin == vin, MaintenanceItem.origin == "custom"))).all()]
    latest = (await session.execute(
        select(Drive.end_ev_odometer, Drive.end_odometer).where(Drive.vin == vin, Drive.end_ev_odometer.is_not(None), Drive.end_odometer > 0)
        .order_by(Drive.start_time.desc()).limit(1))).first()
    since = now - datetime.timedelta(days=365)
    total_km, fast_km = 0.0, 0.0
    for km, speed in (await session.execute(
            select(Drive.distance_km, Drive.speed_avg_kmh).where(Drive.vin == vin, Drive.end_time.is_not(None), Drive.start_time >= since))).all():
        total_km += km or 0.0
        if (speed or 0.0) >= FAST_ROAD_KMH:
            fast_km += km or 0.0
    parts = [Part(category=p.category, position=p.position, installed_at=p.installed_at, installed_odometer=p.installed_odometer,
                  dot=(p.attrs or {}).get("dot") if isinstance(p.attrs, dict) else None)
             for p in (await session.execute(select(VehiclePart).where(VehiclePart.vin == vin))).scalars()]
    return Facts(
        odometer=odometer if odometer is not None else (vehicle.odometer or 0.0), start_odometer=(plan.baseline_odometer if plan else 0.0) or 0.0,
        start_date=vehicle.delivery_date or vehicle.registration_date or (plan.baseline_date if plan else None),
        ev_share=min(1.0, latest[0] / latest[1]) if latest else None,
        fast_share=fast_km / total_km if total_km >= FAST_ROAD_MIN_KM else None,
        last_refuel=(await session.execute(select(func.max(Refuel.timestamp)).where(Refuel.vin == vin))).scalar_one_or_none(),
        parts=parts, custom_tasks=custom)


async def states_of(session: AsyncSession, vin: str) -> Dict[str, State]:
    rows = (await session.execute(select(MaintenanceAdvice).where(MaintenanceAdvice.vin == vin))).scalars().all()
    return {r.key: State(checked_at=r.checked_at, checked_odometer=r.checked_odometer, snooze_until=r.snooze_until,
                         snooze_odometer=r.snooze_odometer, enabled=bool(r.enabled)) for r in rows}


async def collect(session: AsyncSession, vehicle: Vehicle, now: Optional[datetime.datetime] = None,
                  odometer: Optional[float] = None) -> List[Dict[str, Any]]:
    now = now or utcnow()
    return evaluate(await facts_of(session, vehicle, now, odometer), await states_of(session, vehicle.vin), now)


async def due_alerts(session: AsyncSession, vin: str, now: Optional[datetime.datetime] = None,
                     odometer: Optional[float] = None) -> List[Dict[str, Any]]:
    """What is due now and neither postponed nor switched off, each with the `alert` key the poller sends once."""
    vehicle = (await session.execute(select(Vehicle).where(Vehicle.vin == vin))).scalar_one_or_none()
    if vehicle is None:
        return []
    return [{**a, "alert": f"{a['key']}:{a['band']}"} for a in await collect(session, vehicle, now, odometer) if a["status"] == "due"]


async def _row(session: AsyncSession, vin: str, key: str) -> MaintenanceAdvice:
    row = await session.get(MaintenanceAdvice, (vin, key))
    if row is None:
        row = MaintenanceAdvice(vin=vin, key=key, enabled=True)
        session.add(row)
    return row


async def mark_checked(session: AsyncSession, vehicle: Vehicle, key: str, when: Optional[datetime.datetime] = None,
                       odometer: Optional[float] = None) -> None:
    """«Revisado, está bien» or an intervention recorded for it: it counts again from that day and those km."""
    row = await _row(session, vehicle.vin, key)
    row.checked_at = (when or utcnow()).replace(tzinfo=None)
    row.checked_odometer = odometer if odometer is not None else (vehicle.odometer or 0.0)
    row.snooze_until = row.snooze_odometer = None


async def snooze(session: AsyncSession, vehicle: Vehicle, key: str, now: Optional[datetime.datetime] = None) -> None:
    """«Recuérdamelo más adelante»: 30 days and 1,000 km from now (the km only count for the recommendations that watch km)."""
    row = await _row(session, vehicle.vin, key)
    row.snooze_until = (now or utcnow()) + datetime.timedelta(days=SNOOZE_DAYS)
    row.snooze_odometer = (vehicle.odometer or 0.0) + SNOOZE_KM


async def set_enabled(session: AsyncSession, vehicle: Vehicle, key: str, enabled: bool) -> None:
    row = await _row(session, vehicle.vin, key)
    row.enabled = enabled
    if not enabled:
        row.snooze_until = row.snooze_odometer = None


def notice(alerts: List[Dict[str, Any]]) -> Message:
    """The text of the notice: the one recommendation, or the list when several come due together."""
    if len(alerts) == 1:
        return alerts[0]["name"]
    return Message.join("\n", [M("• {p0}", p0=a["name"]) for a in alerts])
