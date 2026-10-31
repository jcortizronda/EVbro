"""The car's timeline and the global search.

Timeline: every dated thing EVBro knows about one car (drives, charges, refuels, workshop interventions, expenses, documents,
geofence entries and exits) merged into one list, newest first, paged with `before`. Each entry carries the tab of the app that shows
it in full, so the interface only has to jump there.

Search: free text over what people look up quickly (a part reference, a workshop, an expense, a document, a refuel station, a drive
address) across the cars the account can see. Matching is a case-insensitive `LIKE` on a few columns: good enough for a personal
history, no index needed.
"""
from __future__ import annotations

import datetime
from typing import Any, Dict, Iterable, List, Optional

from sqlalchemy import and_, desc, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import (
    ChargingSession,
    Drive,
    Expense,
    GeofenceEvent,
    MaintenanceItem,
    MaintenanceLog,
    Refuel,
    VehicleDocument,
    VehiclePart,
)
from app.services import periods

KINDS = ("drive", "charge", "refuel", "maintenance", "expense", "document", "geofence")
# Which tab of the app shows each kind in full
TABS = {"drive": "drives", "charge": "charges", "refuel": "refuels", "maintenance": "maintenance", "expense": "expenses",
        "document": "vehicle", "geofence": "geofences", "part": "vehicle"}
MAX_LIMIT = 200
MIN_QUERY = 2


def _n(value: Optional[float], digits: int = 1) -> Optional[str]:
    if value is None:
        return None
    text = f"{value:,.{digits}f}".replace(",", "X").replace(".", ",").replace("X", ".")
    return text


def _event(kind: str, obj_id: int, when: datetime.datetime, title: str, subtitle: Optional[str] = None,
           amount: Optional[float] = None, amount_unit: str = "€") -> Dict[str, Any]:
    return {"kind": kind, "id": obj_id, "timestamp": when.isoformat(), "title": title, "subtitle": subtitle or "",
            "amount": amount, "amount_unit": amount_unit, "tab": TABS[kind]}


def _drive(d: Drive) -> Dict[str, Any]:
    route = " → ".join(x for x in (d.start_address, d.end_address) if x) or "Viaje"
    bits = [f"{_n(d.distance_km)} km" if d.distance_km else None,
            f"{_n(d.energy_consumed_kwh)} kWh" if d.energy_consumed_kwh else None,
            f"{_n(d.fuel_used_l)} l" if d.fuel_used_l else None]
    return _event("drive", d.id, d.start_time, route, " · ".join(b for b in bits if b), d.distance_km, "km")


def _charge(c: ChargingSession) -> Dict[str, Any]:
    title = f"Carga en {c.address}" if c.address else "Carga"
    bits = ["DC rápida" if c.charge_type == "dc" else None, f"{_n(c.kwh_added)} kWh" if c.kwh_added else None,
            f"{int(c.start_battery_pct)} → {int(c.end_battery_pct)} %" if c.start_battery_pct is not None and c.end_battery_pct is not None else None]
    return _event("charge", c.id, c.start_time, title, " · ".join(b for b in bits if b), c.charge_cost)


def _refuel(r: Refuel) -> Dict[str, Any]:
    title = f"Repostaje en {r.station}" if r.station else "Repostaje"
    bits = [f"{_n(r.liters)} l" if r.liters else None, f"{_n(r.price_per_liter, 3)} €/l" if r.price_per_liter else None,
            "detectado" if r.detected else None]
    return _event("refuel", r.id, r.timestamp, title, " · ".join(b for b in bits if b), r.total_cost)


def _maintenance(log: MaintenanceLog, item_name: str) -> Dict[str, Any]:
    title = log.title or item_name or "Intervención"
    bits = [log.workshop, f"{int(log.odometer):,} km".replace(",", ".") if log.odometer else None]
    return _event("maintenance", log.id, log.service_date, title, " · ".join(b for b in bits if b), log.cost)


def _expense_label(category: Optional[str]) -> str:
    from app.api.routes_expenses import CATEGORIES  # the one list of expense categories, in Spanish
    return CATEGORIES.get(category or "", category or "")


def _expense(e: Expense) -> Dict[str, Any]:
    return _event("expense", e.id, e.date, e.title or "Gasto", _expense_label(e.category), e.amount)


def _document(doc: VehicleDocument) -> Dict[str, Any]:
    return _event("document", doc.id, doc.uploaded_at, doc.title or doc.original_name or "Documento", doc.original_name or "")


def _geofence(ev: GeofenceEvent) -> Dict[str, Any]:
    verb = "Entra en" if ev.kind == "enter" else "Sale de"
    bits = [f"{int(ev.battery_pct)} %" if ev.battery_pct is not None else None,
            f"{int(ev.odometer_km):,} km".replace(",", ".") if ev.odometer_km else None]
    return _event("geofence", ev.id, ev.timestamp, f"{verb} {ev.geofence_name}", " · ".join(b for b in bits if b))


async def events(session: AsyncSession, vin: str, kinds: Iterable[str], before: Optional[datetime.datetime], limit: int,
                 bounds: periods.Bounds = (None, None)) -> Dict[str, Any]:
    """Newest `limit` entries of the chosen kinds older than `before` and inside `bounds` (every kind when `kinds` is empty)."""
    wanted = [k for k in kinds if k in KINDS] or list(KINDS)
    limit = max(1, min(limit, MAX_LIMIT))
    out: List[Dict[str, Any]] = []

    def older(column):
        conds = [periods.condition(column, bounds)]
        if before:
            conds.append(column < before)
        return and_(*conds)

    if "drive" in wanted:
        rows = (await session.execute(select(Drive).where(Drive.vin == vin, older(Drive.start_time)).order_by(desc(Drive.start_time)).limit(limit))).scalars()
        out += [_drive(d) for d in rows]
    if "charge" in wanted:
        rows = (await session.execute(select(ChargingSession).where(ChargingSession.vin == vin, older(ChargingSession.start_time))
                                      .order_by(desc(ChargingSession.start_time)).limit(limit))).scalars()
        out += [_charge(c) for c in rows]
    if "refuel" in wanted:
        rows = (await session.execute(select(Refuel).where(Refuel.vin == vin, older(Refuel.timestamp)).order_by(desc(Refuel.timestamp)).limit(limit))).scalars()
        out += [_refuel(r) for r in rows]
    if "maintenance" in wanted:
        rows = (await session.execute(select(MaintenanceLog, MaintenanceItem.name).join(MaintenanceItem, MaintenanceLog.item_id == MaintenanceItem.id)
                                      .where(MaintenanceItem.vin == vin, older(MaintenanceLog.service_date))
                                      .order_by(desc(MaintenanceLog.service_date)).limit(limit))).all()
        out += [_maintenance(log, name) for log, name in rows]
    if "expense" in wanted:
        rows = (await session.execute(select(Expense).where(Expense.vin == vin, older(Expense.date)).order_by(desc(Expense.date)).limit(limit))).scalars()
        out += [_expense(e) for e in rows]
    if "document" in wanted:
        rows = (await session.execute(select(VehicleDocument).where(VehicleDocument.vin == vin, older(VehicleDocument.uploaded_at))
                                      .order_by(desc(VehicleDocument.uploaded_at)).limit(limit))).scalars()
        out += [_document(d) for d in rows]
    if "geofence" in wanted:
        rows = (await session.execute(select(GeofenceEvent).where(GeofenceEvent.vin == vin, older(GeofenceEvent.timestamp))
                                      .order_by(desc(GeofenceEvent.timestamp)).limit(limit))).scalars()
        out += [_geofence(g) for g in rows]

    out.sort(key=lambda e: e["timestamp"], reverse=True)
    page = out[:limit]
    return {"events": page, "has_more": len(out) > limit, "kinds": wanted}


# ───────────────────────── search ─────────────────────────
def _hit(kind: str, vin: str, car: str, obj_id: int, title: str, subtitle: str, when: Optional[datetime.datetime]) -> Dict[str, Any]:
    return {"kind": kind, "vin": vin, "car": car, "id": obj_id, "title": title, "subtitle": subtitle, "tab": TABS[kind],
            "timestamp": when.isoformat() if when else None}


async def search(session: AsyncSession, cars: Dict[str, str], query: str, limit: int = 40) -> List[Dict[str, Any]]:
    """Text hits across the given cars ({vin: name}), newest first within each kind, `limit` in all."""
    q = " ".join(query.split())
    if len(q) < MIN_QUERY or not cars:
        return []
    like = f"%{q}%"
    vins = list(cars)
    per_kind = max(5, limit // 4)
    hits: List[Dict[str, Any]] = []

    rows = (await session.execute(select(VehiclePart).where(VehiclePart.vin.in_(vins), or_(
        VehiclePart.name.ilike(like), VehiclePart.brand.ilike(like), VehiclePart.reference.ilike(like), VehiclePart.spec.ilike(like),
        VehiclePart.notes.ilike(like))).order_by(desc(VehiclePart.id)).limit(per_kind))).scalars()
    for p in rows:
        sub = " · ".join(x for x in (p.brand, p.reference, p.spec) if x)
        hits.append(_hit("part", p.vin, cars[p.vin], p.id, p.name or p.category, sub, p.installed_at))

    rows = (await session.execute(select(MaintenanceLog, MaintenanceItem.vin, MaintenanceItem.name)
                                  .join(MaintenanceItem, MaintenanceLog.item_id == MaintenanceItem.id)
                                  .where(MaintenanceItem.vin.in_(vins), or_(MaintenanceLog.title.ilike(like), MaintenanceLog.workshop.ilike(like),
                                                                             MaintenanceLog.notes.ilike(like), MaintenanceItem.name.ilike(like)))
                                  .order_by(desc(MaintenanceLog.service_date)).limit(per_kind))).all()
    for log, vin, name in rows:
        sub = " · ".join(x for x in (log.workshop, f"{_n(log.cost, 2)} €" if log.cost else None) if x)
        hits.append(_hit("maintenance", vin, cars[vin], log.id, log.title or name, sub, log.service_date))

    rows = (await session.execute(select(Expense).where(Expense.vin.in_(vins), or_(Expense.title.ilike(like), Expense.notes.ilike(like)))
                                  .order_by(desc(Expense.date)).limit(per_kind))).scalars()
    for e in rows:
        sub = " · ".join(x for x in (_expense_label(e.category), f"{_n(e.amount, 2)} €" if e.amount is not None else None) if x)
        hits.append(_hit("expense", e.vin, cars[e.vin], e.id, e.title, sub, e.date))

    rows = (await session.execute(select(VehicleDocument).where(VehicleDocument.vin.in_(vins), or_(
        VehicleDocument.title.ilike(like), VehicleDocument.original_name.ilike(like))).order_by(desc(VehicleDocument.uploaded_at)).limit(per_kind))).scalars()
    for d in rows:
        hits.append(_hit("document", d.vin, cars[d.vin], d.id, d.title or d.original_name or "Documento", d.original_name or "", d.uploaded_at))

    rows = (await session.execute(select(Refuel).where(Refuel.vin.in_(vins), or_(Refuel.station.ilike(like), Refuel.notes.ilike(like), Refuel.address.ilike(like)))
                                  .order_by(desc(Refuel.timestamp)).limit(per_kind))).scalars()
    for r in rows:
        hits.append(_hit("refuel", r.vin, cars[r.vin], r.id, r.station or "Repostaje", f"{_n(r.liters)} l" if r.liters else "", r.timestamp))

    rows = (await session.execute(select(Drive).where(Drive.vin.in_(vins), or_(Drive.start_address.ilike(like), Drive.end_address.ilike(like), Drive.notes.ilike(like)))
                                  .order_by(desc(Drive.start_time)).limit(per_kind))).scalars()
    for d in rows:
        route = " → ".join(x for x in (d.start_address, d.end_address) if x)
        hits.append(_hit("drive", d.vin, cars[d.vin], d.id, route or "Viaje", f"{_n(d.distance_km)} km" if d.distance_km else "", d.start_time))

    hits.sort(key=lambda h: h["timestamp"] or "", reverse=True)
    return hits[:limit]
