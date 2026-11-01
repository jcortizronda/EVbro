"""Shared catalogue of consumables: proposals from the managers' own parts, the administrator's review, and the public (approved) list.

Rules: a part can be proposed once while its proposal is pending or approved (a rejected one can be sent again, corrected); the
proposal copies the part's fields and the car's model at that moment (editing the part later does not change the proposal), but
never its notes, which are the owner's own;
only `approved` entries are visible to everybody, matched by model (case-insensitive) and category. Approving an entry that
duplicates an approved one (same category, brand and reference; without a reference, the same maker's number for the same model)
adds a confirmation to the existing entry instead. The maker's own number («Ref. fabricante») belongs to each fitment: the same
filter has one number at Ebro and another at a different make.
"""
from __future__ import annotations

import datetime
from typing import Any, Dict, List, Optional

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import AppUser, CatalogEntry, CatalogFitment, Vehicle, VehiclePart
from app.services.parts import CATEGORY_BY_KEY, POSITIONS

STATUSES = ("pending", "approved", "rejected")
EDITABLE = ("model", "make", "years", "name", "brand", "reference", "spec", "quantity", "notes", "position")   # what the admin may correct
MAX_FITMENTS = 20


class CatalogError(Exception):
    def __init__(self, status_code: int, detail: str):
        super().__init__(detail)
        self.status_code, self.detail = status_code, detail


def _fit(f: CatalogFitment) -> Dict[str, Any]:
    return {"id": f.id, "make": f.make, "model": f.model, "years": f.years, "oem_reference": f.oem_reference}


def serialize(e: CatalogEntry, proposer: Optional[str] = None, fitments: Optional[List[CatalogFitment]] = None) -> Dict[str, Any]:
    cat = CATEGORY_BY_KEY.get(e.category, {})
    fits = [_fit(f) for f in (fitments if fitments is not None else [])] or [{"id": None, "make": e.make, "model": e.model, "years": e.years, "oem_reference": e.oem_reference}]
    return {"id": e.id, "model": e.model, "make": e.make, "fitments": fits, "years": e.years, "category": e.category, "category_label": cat.get("label", e.category),
            "spec_label": cat.get("spec_label") or "Especificación", "qty_label": cat.get("qty_label") or "Cantidad",
            "position": e.position, "position_label": POSITIONS.get(e.position or "", None), "positions": [{"key": k, "label": POSITIONS[k]} for k in cat.get("positions", [])], "name": e.name, "brand": e.brand, "reference": e.reference, "oem_reference": e.oem_reference, "spec": e.spec, "quantity": e.quantity,
            "notes": e.notes, "source": e.source, "status": e.status, "reject_reason": e.reject_reason, "confirmations": e.confirmations or 0,
            "proposed_by": proposer, "proposed_at": e.proposed_at.isoformat() if e.proposed_at else None,
            "reviewed_at": e.reviewed_at.isoformat() if e.reviewed_at else None,
            "origin": e.origin or "local", "remote_note": e.remote_note,
            "forwarded_at": e.forwarded_at.isoformat() if e.forwarded_at else None}


async def propose_from_part(session: AsyncSession, part: VehiclePart, user_id: int, years: Optional[str] = None,
                            source: str = "manual") -> CatalogEntry:
    vehicle = (await session.execute(select(Vehicle).where(Vehicle.vin == part.vin))).scalar_one()
    model = (vehicle.model or "").strip()
    if not model:
        raise CatalogError(422, "Indica el modelo del coche en su ficha antes de proponer una pieza al catálogo.")
    if not (part.brand or part.reference or part.oem_reference or part.spec):
        raise CatalogError(422, "Una propuesta necesita al menos la marca, una referencia o la especificación de la pieza.")
    if part.catalog_id:
        current = await session.get(CatalogEntry, part.catalog_id)
        if current is not None and current.status in ("pending", "approved"):
            raise CatalogError(409, "Esta pieza ya está propuesta al catálogo." if current.status == "pending" else "Esta pieza ya está en el catálogo.")
    entry = CatalogEntry(model=model, make=(vehicle.brand or "").strip() or None, years=(years or "").strip() or (str(vehicle.model_year) if vehicle.model_year else None), category=part.category,
                         position=part.position, name=part.name, brand=part.brand, reference=part.reference, oem_reference=part.oem_reference, spec=part.spec,
                         quantity=part.quantity, notes=None, source=source if source in ("manual", "invoice", "photo") else "manual",
                         status="pending", proposed_by=user_id, proposed_at=datetime.datetime.utcnow())
    session.add(entry)
    await session.flush()
    session.add(CatalogFitment(entry_id=entry.id, make=entry.make, model=entry.model, years=entry.years, oem_reference=entry.oem_reference))
    part.catalog_id = entry.id
    await session.commit()
    return entry


async def fitments_of(session: AsyncSession, entry_ids: List[int]) -> Dict[int, List[CatalogFitment]]:
    out: Dict[int, List[CatalogFitment]] = {i: [] for i in entry_ids}
    if entry_ids:
        for f in (await session.execute(select(CatalogFitment).where(CatalogFitment.entry_id.in_(entry_ids)).order_by(CatalogFitment.id))).scalars():
            out.setdefault(f.entry_id, []).append(f)
    return out


async def serialize_many(session: AsyncSession, rows, proposers: Optional[Dict[int, str]] = None) -> List[Dict[str, Any]]:
    fits = await fitments_of(session, [e.id for e in rows])
    return [serialize(e, (proposers or {}).get(e.id), fits.get(e.id, [])) for e in rows]


async def serialize_one(session: AsyncSession, e: CatalogEntry) -> Dict[str, Any]:
    return (await serialize_many(session, [e]))[0]


async def approved(session: AsyncSession, model: Optional[str] = None, category: Optional[str] = None, limit: int = 50,
                   include_remote: bool = True) -> List[CatalogEntry]:
    """Approved entries: this server's own and, unless `include_remote` is False, the mirror of the online catalogue."""
    stmt = select(CatalogEntry).where(CatalogEntry.status == "approved")
    if not include_remote:
        stmt = stmt.where(CatalogEntry.origin == "local")
    if model:
        fits = select(CatalogFitment.entry_id).where(func.lower(CatalogFitment.model) == model.strip().lower())
        stmt = stmt.where(CatalogEntry.id.in_(fits))
    if category:
        stmt = stmt.where(CatalogEntry.category == category)
    stmt = stmt.order_by(CatalogEntry.confirmations.desc(), CatalogEntry.reviewed_at.desc()).limit(limit)
    return list((await session.execute(stmt)).scalars().all())


async def mine(session: AsyncSession, user_id: int) -> List[CatalogEntry]:
    return list((await session.execute(select(CatalogEntry).where(CatalogEntry.proposed_by == user_id)
                                       .order_by(CatalogEntry.proposed_at.desc()))).scalars().all())


async def for_review(session: AsyncSession, status: str = "pending") -> List[Dict[str, Any]]:
    stmt = select(CatalogEntry, AppUser.username).join(AppUser, AppUser.id == CatalogEntry.proposed_by, isouter=True).where(CatalogEntry.origin == "local")
    if status != "all":
        if status not in STATUSES:
            raise CatalogError(422, "Estado no válido.")
        stmt = stmt.where(CatalogEntry.status == status)
    rows = (await session.execute(stmt.order_by(CatalogEntry.proposed_at.desc()).limit(500))).all()
    return await serialize_many(session, [e for e, _ in rows], {e.id: proposer for e, proposer in rows})


async def _duplicate_of(session: AsyncSession, e: CatalogEntry) -> Optional[CatalogEntry]:
    """The same part already approved: same brand and reference or, without a reference, the same maker's number for the same model."""
    base = select(CatalogEntry).where(CatalogEntry.status == "approved", CatalogEntry.id != e.id, CatalogEntry.category == e.category)
    if e.reference:
        stmt = base.where(func.lower(func.coalesce(CatalogEntry.brand, "")) == (e.brand or "").lower(),
                          func.lower(func.coalesce(CatalogEntry.reference, "")) == e.reference.lower())
    elif e.oem_reference:                                      # the maker's number only means something for that make and model
        fits = select(CatalogFitment.entry_id).where(func.lower(CatalogFitment.model) == e.model.lower(),
                                                     func.lower(func.coalesce(CatalogFitment.oem_reference, "")) == e.oem_reference.lower())
        stmt = base.where(func.coalesce(CatalogEntry.reference, "") == "", CatalogEntry.id.in_(fits))
    else:                                                      # nothing to match reliably
        return None
    return (await session.execute(stmt.limit(1))).scalar_one_or_none()


async def review(session: AsyncSession, entry_id: int, admin_id: int, approve: bool, reason: Optional[str] = None) -> CatalogEntry:
    e = await session.get(CatalogEntry, entry_id)
    if e is None:
        raise CatalogError(404, "Propuesta no encontrada.")
    if e.status != "pending":
        raise CatalogError(409, "Esta propuesta ya está revisada.")
    e.reviewed_by, e.reviewed_at = admin_id, datetime.datetime.utcnow()
    if approve:
        dup = await _duplicate_of(session, e)
        if dup is not None:
            dup.confirmations = (dup.confirmations or 0) + 1       # the same reference again: it confirms the existing entry
            known = {(f.model or "").lower(): f for f in (await fitments_of(session, [dup.id]))[dup.id]}
            same = known.get(e.model.lower())
            if same is None:                                       # ...and, from another model, extends what the reference fits
                session.add(CatalogFitment(entry_id=dup.id, make=e.make, model=e.model, years=e.years, oem_reference=e.oem_reference))
            elif e.oem_reference and not same.oem_reference:       # ...or fills in the maker's number it was missing
                same.oem_reference = e.oem_reference
                if (dup.model or "").lower() == e.model.lower() and not dup.oem_reference:
                    dup.oem_reference = e.oem_reference
            e.status, e.reject_reason = "rejected", f"Ya estaba en el catálogo (entrada {dup.id}); cuenta como confirmación."
            # the proposer's part now points at the entry that is public
            for part in (await session.execute(select(VehiclePart).where(VehiclePart.catalog_id == e.id))).scalars():
                part.catalog_id = dup.id
        else:
            e.status, e.reject_reason = "approved", None
    else:
        e.status, e.reject_reason = "rejected", (reason or "").strip()[:255] or None
    await session.commit()
    return e


def _clean_fitments(entry_id: Optional[int], fits: List[Dict[str, Any]]) -> List[CatalogFitment]:
    """The cars a reference fits, without empty rows; at least one model is required."""
    clean = []
    for f in fits[:MAX_FITMENTS]:
        model = (f.get("model") or "").strip()
        if model:
            clean.append(CatalogFitment(entry_id=entry_id, make=(f.get("make") or "").strip() or None, model=model, years=(f.get("years") or "").strip() or None,
                                        oem_reference=(f.get("oem_reference") or "").strip() or None))
    if not clean:
        raise CatalogError(422, "La pieza tiene que valer al menos para un modelo.")
    return clean


async def create(session: AsyncSession, admin_id: int, data: Dict[str, Any]) -> CatalogEntry:
    """The administrator adds a part straight to the catalogue: it is approved at once, unless the same part is already there."""
    cat = CATEGORY_BY_KEY.get(data.get("category") or "")
    if cat is None:
        raise CatalogError(422, "Categoría desconocida.")
    text = {k: ((data.get(k) or "").strip() or None) for k in ("name", "brand", "reference", "spec", "notes", "position")}
    if text["position"] is not None and text["position"] not in cat.get("positions", []):
        raise CatalogError(422, "Esa posición no vale para este tipo de pieza.")
    fits = _clean_fitments(None, data.get("fitments") or [])
    main = fits[0]
    if not (text["brand"] or text["reference"] or main.oem_reference or text["spec"]):
        raise CatalogError(422, "Indica al menos la marca, una referencia o la especificación de la pieza.")
    now = datetime.datetime.utcnow()
    entry = CatalogEntry(model=main.model, make=main.make, years=main.years, category=cat["key"], position=text["position"], name=text["name"] or cat["label"],
                         brand=text["brand"], reference=text["reference"], oem_reference=main.oem_reference, spec=text["spec"], quantity=data.get("quantity"),
                         notes=text["notes"], source="manual", status="approved", proposed_by=admin_id, proposed_at=now, reviewed_by=admin_id, reviewed_at=now)
    dup = await _duplicate_of(session, entry)
    if dup is not None:
        raise CatalogError(409, f"Esa pieza ya está en el catálogo (entrada {dup.id}): edítala para añadirle coches.")
    session.add(entry)
    await session.flush()
    for f in fits:
        f.entry_id = entry.id
    session.add_all(fits)
    await session.commit()
    return entry


async def edit(session: AsyncSession, entry_id: int, changes: Dict[str, Any]) -> CatalogEntry:
    """The administrator completes or corrects a proposal (pending or approved); the category never changes."""
    e = await session.get(CatalogEntry, entry_id)
    if e is None:
        raise CatalogError(404, "Propuesta no encontrada.")
    if e.status == "rejected":
        raise CatalogError(409, "Una propuesta rechazada no se edita: que el proponente la corrija y la vuelva a enviar.")
    fits = changes.pop("fitments", None)
    if fits is not None:
        clean = _clean_fitments(e.id, fits)
        for old in (await fitments_of(session, [e.id]))[e.id]:
            await session.delete(old)
        session.add_all(clean)
        e.make, e.model, e.years, e.oem_reference = clean[0].make, clean[0].model, clean[0].years, clean[0].oem_reference   # the first one is the "main" car
    for field, value in changes.items():
        if field not in EDITABLE:
            continue
        if isinstance(value, str):
            value = value.strip() or None
        if field in ("model", "name") and not value:
            raise CatalogError(422, "El modelo y el nombre no pueden quedar vacíos.")
        if field == "position" and value is not None and value not in CATEGORY_BY_KEY.get(e.category, {}).get("positions", []):
            raise CatalogError(422, "Esa posición no vale para este tipo de pieza.")
        setattr(e, field, value)
    await session.commit()
    return e


async def export_approved(session: AsyncSession) -> Dict[str, Any]:
    """This server's own approved entries (never the mirrored ones: they already live on the online server)."""
    rows = await approved(session, limit=100_000, include_remote=False)
    return {"format": "evbro-catalog/2", "exported_at": datetime.datetime.utcnow().isoformat() + "Z", "count": len(rows),
            "entries": [{k: v for k, v in d.items() if _export_key(k)}
                        for d in await serialize_many(session, rows)]}


def _export_key(k: str) -> bool:
    return k not in ("positions", "status", "reject_reason", "proposed_by", "proposed_at", "reviewed_at", "origin", "remote_note", "forwarded_at")
