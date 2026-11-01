"""EBRO's official maintenance plan and its calculation (pure functions, no DB).

**What it is.** The factory table has six steps (15,000 km / 12 months, 30,000 / 24 … 90,000 / 72). Each one has
operations to **replace (R)** and **inspect (I)** and a labour time.

**How the next service is worked out.** It used to come only from the current odometer, so it did not remember when the
last one was done nor look at the months. Now:
  * the next step is the first one whose mileage is GREATER than that of the last recorded step;
  * it is due on **whichever comes first**: the step's km or its date (car delivery + the step's months);
  * without a delivery date configured only the kilometres are watched (the interface asks for it).
"""
from __future__ import annotations

import datetime
from typing import Any, Dict, List, Optional


def is_ebro(brand: Optional[str]) -> bool:
    """The official plan is EBRO's: it only applies to cars whose brand is Ebro (any case)."""
    return (brand or "").strip().lower() == "ebro"


WARNING_KM = 1500        # «próximo» (upcoming): fewer than these kilometres left…
WARNING_DAYS = 30        # …or fewer than these days

STEPS: List[Dict[str, Any]] = [
    {"months": 12, "km": 15000, "label": "15.000 km / 12 meses (Año 1)"},
    {"months": 24, "km": 30000, "label": "30.000 km / 24 meses (Año 2)"},
    {"months": 36, "km": 45000, "label": "45.000 km / 36 meses (Año 3)"},
    {"months": 48, "km": 60000, "label": "60.000 km / 48 meses (Año 4)"},
    {"months": 60, "km": 75000, "label": "75.000 km / 60 meses (Año 5)"},
    {"months": 72, "km": 90000, "label": "90.000 km / 72 meses (Año 6)"},
]

LABOR_HOURS = {
    "S400": {15000: 0.8, 30000: 1.2, 45000: 1.0, 60000: 1.6, 75000: 0.8, 90000: 1.4},
    "OTHER": {15000: 1.0, 30000: 1.4, 45000: 1.2, 60000: 1.8, 75000: 1.0, 90000: 1.6},
}

RULES: List[Dict[str, Any]] = [
    {"id": "aceite", "name": "Aceite (4,5 Litros) 0W-20", "category": "Motor",
     "spec": "4,5L Sintético 0W-20 (Norma Ebro / Chery)",
     "operations": {15000: "R", 30000: "R", 45000: "R", 60000: "R", 75000: "R", 90000: "R"}},
    {"id": "filtro_aceite", "name": "Filtro de aceite", "category": "Motor",
     "spec": "Cartucho filtrante original OEM",
     "operations": {15000: "R", 30000: "R", 45000: "R", 60000: "R", 75000: "R", 90000: "R"}},
    {"id": "filtro_aa", "name": "Filtro A/A (Habitáculo)", "category": "Filtros",
     "spec": "Filtro de polen y carbón activo antibacteriano",
     "operations": {15000: "I", 30000: "R", 45000: "I", 60000: "R", 75000: "I", 90000: "R"}},
    {"id": "filtro_aire", "name": "Filtro Aire (Motor)", "category": "Filtros",
     "spec": "Elemento filtrante de admisión motor térmico",
     "operations": {15000: "I", 30000: "I", 45000: "R", 60000: "I", 75000: "I", 90000: "R"}},
    {"id": "liquido_frenos", "name": "Líquido de frenos", "category": "Frenos",
     "spec": "Líquido de frenos sintético DOT 4",
     "operations": {15000: "I", 30000: "R", 45000: "I", 60000: "R", 75000: "I", 90000: "R"}},
    {"id": "bujias", "name": "Bujías de encendido", "category": "Motor",
     "spec": "Juego de bujías de iridio de alto rendimiento",
     "operations": {15000: "I", 30000: "I", 45000: "R", 60000: "I", 75000: "I", 90000: "R"}},
    {"id": "anticongelante", "name": "Anticongelante", "category": "Motor",
     "spec": "4 años / 60 mil km (+0,3h ICE+HEV, +0,5h PHEV)",
     "operations": {15000: "-", 30000: "-", 45000: "-", 60000: "R", 75000: "-", 90000: "-"}},
    {"id": "aceite_transmision", "name": "Aceite Transmisión (DCT y HCT) + Filtro", "category": "Transmisión",
     "spec": "4 años / 60 mil km - Fluido embrague doble + filtro",
     "operations": {15000: "-", 30000: "-", 45000: "-", 60000: "R", 75000: "-", 90000: "-"}},
]

# tasks offered as suggestions in custom mode (never created on their own)
SUGGESTIONS: List[Dict[str, Any]] = [
    {"key": "aceite", "name": "Aceite y filtro de aceite", "category": "Motor", "interval_km": 15000, "interval_months": 12},
    {"key": "neumaticos", "name": "Rotación de neumáticos", "category": "Neumáticos", "interval_km": 10000, "interval_months": 12},
    {"key": "frenos", "name": "Líquido de frenos", "category": "Frenos", "interval_km": 30000, "interval_months": 24},
    {"key": "filtro_aa", "name": "Filtro de habitáculo", "category": "Filtros", "interval_km": 20000, "interval_months": 12},
    {"key": "escobillas", "name": "Escobillas limpiaparabrisas", "category": "General", "interval_km": None, "interval_months": 12},
    {"key": "bateria12", "name": "Batería auxiliar de 12 V", "category": "General", "interval_km": None, "interval_months": 36},
    {"key": "itv", "name": "Inspección técnica (ITV)", "category": "Legal", "interval_km": None, "interval_months": 24},
]


def labor_key(model: Optional[str]) -> str:
    return "S400" if "S400" in (model or "").upper() else "OTHER"


def add_months(date: datetime.datetime, months: int) -> datetime.datetime:
    """Adds calendar months (the day is clamped to the last of the month when needed)."""
    total = date.month - 1 + months
    year, month = date.year + total // 12, total % 12 + 1
    for day in (date.day, 30, 29, 28):
        try:
            return date.replace(year=year, month=month, day=day)
        except ValueError:
            continue
    return date.replace(year=year, month=month, day=28)


def next_step(last_done_km: Optional[int]) -> Optional[Dict[str, Any]]:
    """First step with more km than the last recorded one; None when all are done."""
    for step in STEPS:
        if last_done_km is None or step["km"] > last_done_km:
            return step
    return None


def step_detail(step_km: int, model: Optional[str]) -> Dict[str, Any]:
    """What is replaced/inspected at a step and how much labour it takes."""
    base = LABOR_HOURS[labor_key(model)].get(step_km, 1.0)
    extra, notes = 0.0, []
    if step_km == 60000:
        phev = "PHEV" in (model or "").upper()
        extra = 0.5 if phev else 0.3
        notes.append("Incluye +0,5 h de sustitución de anticongelante (PHEV)" if phev
                     else "Incluye +0,3 h de sustitución de anticongelante (ICE/HEV)")
        notes.append("Sustitución del aceite de transmisión (DCT/HCT) y su filtro")
    replace, inspect = [], []
    for rule in RULES:
        op = rule["operations"].get(step_km, "-")
        item = {"id": rule["id"], "name": rule["name"], "category": rule["category"], "spec": rule["spec"], "operation": op}
        if op == "R":
            replace.append(item)
        elif op == "I":
            inspect.append(item)
    return {"replace_items": replace, "inspect_items": inspect, "base_labor_hours": base,
            "extra_labor_hours": extra, "labor_hours": round(base + extra, 1), "extra_notes": notes}


def status_for(remaining_km: Optional[float], remaining_days: Optional[int]) -> str:
    """due (overdue) · warning (upcoming) · ok. Whichever comes first, km or days."""
    if (remaining_km is not None and remaining_km <= 0) or (remaining_days is not None and remaining_days <= 0):
        return "due"
    if (remaining_km is not None and remaining_km <= WARNING_KM) or (remaining_days is not None and remaining_days <= WARNING_DAYS):
        return "warning"
    return "ok"


def official_next(
    odometer: float,
    today: datetime.date,
    last_done_km: Optional[int],
    baseline_date: Optional[datetime.datetime],
    baseline_odometer: float,
    model: Optional[str],
) -> Optional[Dict[str, Any]]:
    """Next official service with its status; None when the official plan is complete."""
    step = next_step(last_done_km)
    if step is None:
        return None
    prev = next((s for s in reversed(STEPS) if s["km"] < step["km"]), None)
    prev_km = prev["km"] if prev else baseline_odometer
    due_date = add_months(baseline_date, step["months"]) if baseline_date else None
    remaining_km = round(step["km"] - odometer, 1)
    remaining_days = (due_date.date() - today).days if due_date else None

    span_km = max(1.0, step["km"] - prev_km)
    progress_km = min(100.0, max(0.0, (odometer - prev_km) / span_km * 100.0))
    progress_time = None
    if baseline_date:
        start = add_months(baseline_date, prev["months"]) if prev else baseline_date
        total = max(1, (due_date.date() - start.date()).days)
        progress_time = min(100.0, max(0.0, (today - start.date()).days / total * 100.0))

    return {
        "km": step["km"], "months": step["months"], "label": step["label"],
        "due_date": due_date.isoformat() if due_date else None,
        "remaining_km": remaining_km, "remaining_days": remaining_days,
        "status": status_for(remaining_km, remaining_days),
        "progress_km_pct": round(progress_km, 1),
        "progress_time_pct": None if progress_time is None else round(progress_time, 1),
        **step_detail(step["km"], model),
    }


# ───────────────────────── ITV (counted from the registration date) ─────────────────────────
ITV_FIRST_YEARS = 4          # passenger cars: the first one 4 years after registration
ITV_EVERY_YEARS = 2          # …then every 2 years until the car is 10 years old
ITV_YEARLY_AFTER_YEARS = 10  # …and every year from then on
ITV_WARNING_DAYS = 60


def _years_between(a: datetime.datetime, b: datetime.datetime) -> float:
    return (b - a).days / 365.25


def itv_next_due(
    registration_date: Optional[datetime.datetime],
    last_itv_date: Optional[datetime.datetime],
    valid_until: Optional[datetime.datetime] = None,
) -> Dict[str, Any]:
    """Next ITV. Priority: the «valid until» date of the last sticker (the official one); otherwise the age-based calculation.

    Calculation: without a previous ITV, registration + 4 years; with one, that date + 2 years (+ 1 year if the car was 10 or more
    years old when it passed). It is an estimate: the date on the sticker wins."""
    if valid_until is not None:
        return {"due_date": valid_until, "basis": "valid_until"}
    if last_itv_date is not None:
        age = _years_between(registration_date, last_itv_date) if registration_date else 0.0
        years = 1 if age >= ITV_YEARLY_AFTER_YEARS else ITV_EVERY_YEARS
        return {"due_date": add_months(last_itv_date, 12 * years), "basis": "calculated"}
    if registration_date is not None:
        return {"due_date": add_months(registration_date, 12 * ITV_FIRST_YEARS), "basis": "registration"}
    return {"due_date": None, "basis": "unknown"}


def itv_status(due_date: Optional[datetime.datetime], today: datetime.date) -> Dict[str, Any]:
    if due_date is None:
        return {"remaining_days": None, "status": "unknown"}
    days = (due_date.date() - today).days
    status = "due" if days <= 0 else ("warning" if days <= ITV_WARNING_DAYS else "ok")
    return {"remaining_days": days, "status": status}
