"""Factory figures of the Ebro models, applied to a car's sheet when its owner has not filled them in.

Every connected car used to start with the same sheet (19.27 kWh of battery, 50 l of tank): the kWh of charges and trips of an
S900 came out at half until its owner corrected the capacity, and the litres of every model were off by a fifth. The cloud names
the model («S900 PHEV») and the VIN carries the model year, so the sheet can start right; the owner can still change anything.

Figures from the public data sheets (2025-2026 range; the S800 battery is the least confirmed one). A model year that changes a
figure would get its own entry in `YEARS`; so far the three models keep the same figures across years.
"""
from __future__ import annotations

import re
from typing import Any, Dict, List, Optional

# The values a sheet carries before anyone touches it (db/models.py). Only these are replaced.
DEFAULT_BATTERY_KWH = 19.27
DEFAULT_TANK_LITERS = 50.0

SPECS: Dict[str, Dict[str, Any]] = {
    "S700 PHEV": {"battery_capacity_kwh": 18.3, "fuel_tank_liters": 60.0, "seats": 5, "electric_range_km": 90, "env_label": "0", "fuel_type": "phev"},
    "S800 PHEV": {"battery_capacity_kwh": 18.3, "fuel_tank_liters": 60.0, "seats": 7, "electric_range_km": 90, "env_label": "0", "fuel_type": "phev"},
    "S900 PHEV": {"battery_capacity_kwh": 34.5, "fuel_tank_liters": 70.0, "seats": 7, "electric_range_km": 140, "env_label": "0", "fuel_type": "phev"},
}
# (model, first year, last year) -> figures that differ from `SPECS` for those years. Empty so far.
YEARS: List[tuple] = []

_MODEL = re.compile(r"S\s*([789]00)\s*PHEV", re.IGNORECASE)
# ISO 3779 model-year code (10th character of the VIN): I, O, Q, U, Z and 0 are never used
_YEAR_CODES = "ABCDEFGHJKLMNPRSTVWXY123456789"
_FIRST_YEAR = 2010


def normalize_model(model: Optional[str]) -> Optional[str]:
    """«Ebro s900 phev», «S900 PHEV» → «S900 PHEV»; anything else → None."""
    m = _MODEL.search(model or "")
    return f"S{m.group(1)} PHEV" if m else None


def model_year_from_vin(vin: Optional[str]) -> Optional[int]:
    """The model year coded in the VIN (its 10th character), for a 17-character VIN; None otherwise or for a manual car's id."""
    v = (vin or "").strip().upper()
    if len(v) != 17 or v[9] not in _YEAR_CODES:
        return None
    year = _FIRST_YEAR + _YEAR_CODES.index(v[9])
    return year


def spec_for(model: Optional[str], year: Optional[int] = None) -> Optional[Dict[str, Any]]:
    """The factory figures of a model (and year), or None when the model is not an Ebro we know."""
    key = normalize_model(model)
    if key is None or key not in SPECS:
        return None
    spec = dict(SPECS[key])
    for name, first, last, extra in YEARS:
        if name == key and year is not None and first <= year <= last:
            spec.update(extra)
    return spec


def is_ebro(brand: Optional[str]) -> bool:
    return (brand or "").strip().lower() == "ebro"


def fill_defaults(vehicle) -> List[str]:
    """Writes on `vehicle` the figures its owner has not set: the model year from the VIN, and the battery, tank and environmental
    label of the model while they still carry the generic default. Returns the names of the fields changed. Nothing is written on
    a car of another make or an unknown model."""
    changed: List[str] = []
    if getattr(vehicle, "model_year", None) is None and not getattr(vehicle, "is_manual", False):
        year = model_year_from_vin(vehicle.vin)
        if year is not None:
            vehicle.model_year = year
            changed.append("model_year")
    if not is_ebro(vehicle.brand):
        return changed
    spec = spec_for(vehicle.model, vehicle.model_year)
    if spec is None:
        return changed
    if vehicle.battery_capacity_kwh in (None, DEFAULT_BATTERY_KWH) and vehicle.battery_capacity_kwh != spec["battery_capacity_kwh"]:
        vehicle.battery_capacity_kwh = spec["battery_capacity_kwh"]
        changed.append("battery_capacity_kwh")
    if vehicle.fuel_tank_liters in (None, DEFAULT_TANK_LITERS) and vehicle.fuel_tank_liters != spec["fuel_tank_liters"]:
        vehicle.fuel_tank_liters = spec["fuel_tank_liters"]
        changed.append("fuel_tank_liters")
    if not getattr(vehicle, "env_label", None):
        vehicle.env_label = spec["env_label"]
        changed.append("env_label")
    return changed
