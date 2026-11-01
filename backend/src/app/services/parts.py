"""Vehicle parts and consumables: the catalogue of categories, input validation and the "parts used" helper.

A `VehiclePart` is what *this* car carries (oil, filters, brake pads, tyres...). It is private to the vehicle's owner. It has two part
numbers: `reference` (the one on the box, aftermarket or original) and `oem_reference` («Ref. fabricante»: the car maker's own number,
e.g. Ebro's). `catalog_id` points at the shared catalogue: the owner's proposal, or the approved entry the part was copied from.
"""
from __future__ import annotations

import datetime
import re
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field, field_validator
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import MaintenanceLog, VehiclePart

# Display groups (user-facing text is Spanish).
GROUPS: Dict[str, str] = {
    "engine": "Motor y fluidos", "filters": "Filtros", "brakes": "Frenos", "tyres": "Neumáticos",
    "electrical": "Eléctrico y luces", "other": "Otros",
}
POSITIONS: Dict[str, str] = {"front": "Delantero", "rear": "Trasero", "spare": "Repuesto"}
TYRE_SEASONS: Dict[str, str] = {"summer": "Verano", "winter": "Invierno", "all_season": "Todo tiempo"}

# `multiple`: several parts of the same category/position can coexist (bulbs, "other"). `attrs`: extra keys allowed for the category.
# The DOT (week and year a tyre was made) belongs to one tyre, not to the consumable: interventions record it, the car sheet does not.
SHEET_DROPS = ("dot",)
PART_CATEGORIES: List[Dict[str, Any]] = [
    {"key": "engine_oil", "label": "Aceite de motor", "group": "engine", "positions": [], "spec_label": "Viscosidad", "spec_hint": "0W-20", "qty_label": "Litros"},
    {"key": "coolant", "label": "Líquido refrigerante", "group": "engine", "positions": [], "spec_label": "Tipo", "spec_hint": "G12 / G13", "qty_label": "Litros"},
    {"key": "brake_fluid", "label": "Líquido de frenos", "group": "engine", "positions": [], "spec_label": "Norma", "spec_hint": "DOT 4", "qty_label": "Litros"},
    {"key": "transmission_fluid", "label": "Aceite de transmisión", "group": "engine", "positions": [], "spec_label": "Especificación", "spec_hint": "", "qty_label": "Litros"},
    {"key": "spark_plugs", "label": "Bujías", "group": "engine", "positions": [], "spec_label": "Tipo", "spec_hint": "", "qty_label": "Unidades"},
    {"key": "drive_belt", "label": "Correa de accesorios / distribución", "group": "engine", "positions": [], "spec_label": "Tipo", "spec_hint": "", "qty_label": "Unidades"},
    {"key": "oil_filter", "label": "Filtro de aceite", "group": "filters", "positions": [], "spec_label": "Tipo", "spec_hint": "", "qty_label": "Unidades"},
    {"key": "air_filter", "label": "Filtro de aire", "group": "filters", "positions": [], "spec_label": "Tipo", "spec_hint": "", "qty_label": "Unidades"},
    {"key": "cabin_filter", "label": "Filtro de habitáculo", "group": "filters", "positions": [], "spec_label": "Tipo", "spec_hint": "Carbón activo", "qty_label": "Unidades"},
    {"key": "fuel_filter", "label": "Filtro de combustible", "group": "filters", "positions": [], "spec_label": "Tipo", "spec_hint": "", "qty_label": "Unidades"},
    {"key": "brake_pads", "label": "Pastillas de freno", "group": "brakes", "positions": ["front", "rear"], "spec_label": "Tipo", "spec_hint": "Cerámicas / semimetálicas", "qty_label": "Juegos"},
    {"key": "brake_discs", "label": "Discos de freno", "group": "brakes", "positions": ["front", "rear"], "spec_label": "Medida", "spec_hint": "Diámetro x grosor", "qty_label": "Juegos"},
    {"key": "tyres", "label": "Neumáticos", "group": "tyres", "positions": ["front", "rear", "spare"], "spec_label": "Medida", "spec_hint": "235/55 R19 105V", "qty_label": "Unidades",
     "attrs": ["season", "dot"]},
    {"key": "battery_12v", "label": "Batería de 12 V", "group": "electrical", "positions": [], "spec_label": "Capacidad", "spec_hint": "60 Ah / 540 A", "qty_label": "Unidades"},
    {"key": "bulbs", "label": "Bombillas", "group": "electrical", "positions": [], "spec_label": "Tipo", "spec_hint": "H7 / LED", "qty_label": "Unidades", "multiple": True},
    {"key": "wiper_blades", "label": "Escobillas", "group": "electrical", "positions": ["front", "rear"], "spec_label": "Medida", "spec_hint": "650 / 400 mm", "qty_label": "Unidades"},
    {"key": "other", "label": "Otro consumible", "group": "other", "positions": [], "spec_label": "Especificación", "spec_hint": "", "qty_label": "Cantidad", "multiple": True},
]
CATEGORY_BY_KEY: Dict[str, Dict[str, Any]] = {c["key"]: c for c in PART_CATEGORIES}
DOT_RE = re.compile(r"^\d{4}$")           # week + year of manufacture, e.g. 2324 = week 23 of 2024


class PartIn(BaseModel):
    """A part as sent by the client (both when editing the car sheet and when recording an intervention)."""
    category: str
    position: Optional[str] = None
    name: str = Field(default="", max_length=150)
    brand: Optional[str] = Field(default=None, max_length=80)
    reference: Optional[str] = Field(default=None, max_length=120)
    oem_reference: Optional[str] = Field(default=None, max_length=120)
    spec: Optional[str] = Field(default=None, max_length=120)
    quantity: Optional[float] = Field(default=None, ge=0, le=1000)
    notes: Optional[str] = Field(default=None, max_length=1000)
    attrs: Optional[Dict[str, str]] = None
    catalog_id: Optional[int] = Field(default=None, ge=1)          # set when the part is copied from an approved catalogue entry

    @field_validator("category")
    @classmethod
    def _known_category(cls, v: str) -> str:
        if v not in CATEGORY_BY_KEY:
            raise ValueError(f"Categoría no válida. Usa una de: {', '.join(CATEGORY_BY_KEY)}.")
        return v

    def normalized(self) -> "PartIn":
        """Validates the category-dependent bits and returns a cleaned copy (blank strings -> None, unknown attrs dropped)."""
        cat = CATEGORY_BY_KEY[self.category]
        position = (self.position or "").strip() or None
        if position and position not in cat["positions"]:
            raise ValueError(f"La posición «{position}» no es válida para {cat['label'].lower()}.")
        attrs: Dict[str, str] = {}
        for k, v in (self.attrs or {}).items():
            v = (v or "").strip()
            if k not in cat.get("attrs", []) or not v:
                continue
            if k == "season" and v not in TYRE_SEASONS:
                raise ValueError("La estación del neumático debe ser summer, winter o all_season.")
            if k == "dot" and not DOT_RE.match(v):
                raise ValueError("La fecha de fabricación (DOT) son 4 cifras: semana y año, p. ej. 2324.")
            attrs[k] = v
        clean = lambda s: (s or "").strip() or None  # noqa: E731
        return PartIn(category=self.category, position=position, name=(self.name or "").strip() or cat["label"], brand=clean(self.brand),
                      reference=clean(self.reference), oem_reference=clean(self.oem_reference), spec=clean(self.spec), quantity=self.quantity,
                      notes=clean(self.notes), attrs=attrs or None, catalog_id=self.catalog_id)


def serialize_part(p: VehiclePart) -> Dict[str, Any]:
    cat = CATEGORY_BY_KEY.get(p.category, {})
    return {
        "id": p.id, "category": p.category, "category_label": cat.get("label", p.category), "group": cat.get("group", "other"),
        "position": p.position, "position_label": POSITIONS.get(p.position or "", None), "name": p.name, "brand": p.brand,
        "reference": p.reference, "oem_reference": p.oem_reference, "spec": p.spec, "quantity": p.quantity, "attrs": _sheet_attrs(p.attrs) or {}, "notes": p.notes,
        "installed_at": p.installed_at.isoformat() if p.installed_at else None, "installed_odometer": p.installed_odometer,
        "source_log_id": p.source_log_id, "catalog_id": p.catalog_id,
    }


def part_matches(p: Dict[str, Any], q: str) -> bool:
    """Case-insensitive search across the text fields a person would look for (name, brand, reference, spec, notes, category)."""
    needle = q.strip().lower()
    hay = " ".join(str(p.get(k) or "") for k in ("name", "brand", "reference", "oem_reference", "spec", "notes", "category_label", "position_label"))
    return needle in hay.lower()


async def find_existing(session: AsyncSession, vin: str, part: PartIn) -> Optional[VehiclePart]:
    cat = CATEGORY_BY_KEY[part.category]
    rows = (await session.execute(select(VehiclePart).where(VehiclePart.vin == vin, VehiclePart.category == part.category))).scalars().all()
    for row in rows:
        if (row.position or None) != part.position:
            continue
        if cat.get("multiple") and (row.name or "").lower() != part.name.lower():
            continue
        return row
    return None


def _sheet_attrs(attrs: Optional[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    kept = {k: v for k, v in (attrs or {}).items() if k not in SHEET_DROPS}
    return kept or None


def _numbers(p: Any) -> tuple:
    return tuple((getattr(p, k) or "").strip().lower() for k in ("brand", "reference", "oem_reference"))


def apply_fields(row: VehiclePart, part: PartIn) -> None:
    """Copies the editable fields. `catalog_id` only changes when the part comes from the catalogue: editing a part never unlinks it."""
    row.name, row.brand, row.reference, row.oem_reference, row.spec = part.name, part.brand, part.reference, part.oem_reference, part.spec
    row.quantity, row.notes, row.attrs = part.quantity, part.notes, _sheet_attrs(part.attrs)
    if part.catalog_id is not None:
        row.catalog_id = part.catalog_id


async def upsert_part(session: AsyncSession, vin: str, part: PartIn, *, installed_at: Optional[datetime.datetime] = None,
                      installed_odometer: Optional[float] = None, source_log_id: Optional[int] = None) -> VehiclePart:
    """Creates the part or updates the one the car already has in that category/position (a new tyre replaces the old one)."""
    row = await find_existing(session, vin, part)
    if row is None:
        row = VehiclePart(vin=vin, category=part.category, position=part.position)
        session.add(row)
    elif part.catalog_id is None and _numbers(row) != _numbers(part):
        row.catalog_id = None                                 # a different part replaces it: the old proposal or entry no longer applies
    apply_fields(row, part)
    if installed_at is not None:
        row.installed_at = installed_at
    if installed_odometer is not None:
        row.installed_odometer = installed_odometer
    if source_log_id is not None:
        row.source_log_id = source_log_id
    return row


async def record_parts_used(session: AsyncSession, vin: str, log: MaintenanceLog, parts: Optional[List[PartIn]], update_sheet: bool) -> None:
    """Stores the parts of an intervention on its log and, if asked, brings the car sheet up to date with them.

    The log keeps the parts exactly as they were fitted that day (history); the sheet only keeps what the car carries now."""
    if not parts:
        return
    clean = [p.normalized() for p in parts]
    for p in clean:
        p.catalog_id = None                                   # an intervention never links to the catalogue (the sheet does it, validated)
    await session.flush()                                     # the log needs an id before the sheet points at it
    log.parts_used = [p.model_dump(exclude_none=True, exclude={"catalog_id"}) for p in clean]
    if update_sheet:
        for p in clean:
            await upsert_part(session, vin, p, installed_at=log.service_date, installed_odometer=log.odometer, source_log_id=log.id)
