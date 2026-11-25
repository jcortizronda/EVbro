"""Refuels from a spreadsheet: an Excel book (.xlsx) or a CSV with one refuel per row (Repostajes → «Importar»).

The same three steps as the charges (services/charge_import.py, whose reading of the file, numbers and dates it uses), and nothing
is stored before the last one: the file is read into a table, its columns are understood from their titles (the user corrects
them on screen) and every row becomes a refuel or the reason it cannot be one; then the rows that are ready are stored as refuels
added by hand, all with one batch so the import can be undone whole.

What a refuel needs: the day (the consumption between fill-ups is worked out in date order, so a refuel without one cannot be
placed) and the litres, or the total and the price per litre. Nothing of the charges' battery, AC/DC or efficiency applies. A row
that looks like a refuel the car already has (same day, about the same litres) is not brought in again.
"""
from __future__ import annotations

import datetime
import io
import re
import secrets
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Tuple

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Refuel, Vehicle
from app.services import manual_charges
from app.services.charge_import import MAX_BYTES, ImportError_, _plain, day_and_hour, hour_of, number, read_table  # noqa: F401  (re-exported)
from app.timeutil import utcnow

PREVIEW_ROWS = 200
LITERS_MAX = 300.0
PRICE_MAX = 10.0
COST_MAX = 5000.0
ODOMETER_MAX = 5_000_000.0
STATION_MAX = 150
NOTES_MAX = 2000
TOO_MANY = "El fichero tiene más de 1.000 repostajes: pártelo en varios."

FIELDS = ("date", "time", "liters", "price", "total", "odometer", "station", "full", "notes")
# Words a column title may carry, without accents and in lower case. The first field whose words match takes the column.
_TITLES: Dict[str, Tuple[str, ...]] = {
    "price": ("precio", "price", "preco", "€/l", "eur/l", "€/litro", "por litro", "per litre", "per liter"),
    "total": ("total", "importe", "coste", "costo", "cost", "pagado", "amount", "custo", "valor"),
    "liters": ("litros", "litro", "liters", "litres", "liter", "litre", "cantidad", "volumen", "volume", "quantidade"),
    "odometer": ("cuentakilometros", "odometro", "odometer", "kilometraje", "kilometros", "km", "mileage", "quilometr"),
    "time": ("hora", "time"),
    "date": ("fecha", "date", "dia", "data", "day"),
    "full": ("lleno", "full", "completo", "cheio", "deposito"),
    "station": ("gasolinera", "estacion", "station", "surtidor", "lugar", "sitio", "donde", "place", "posto", "bomba", "direccion", "address"),
    "notes": ("nota", "notes", "observ", "comentario", "comment"),
}
_YES = ("si", "sí", "s", "yes", "y", "x", "1", "true", "verdadero", "lleno", "full", "completo", "sim", "✓", "✔")
_NO = ("no", "n", "0", "false", "falso", "nao", "não")


@dataclass
class RefuelData:
    day: Optional[datetime.date]
    hour: Optional[datetime.time]
    liters: Optional[float]
    price_per_liter: Optional[float]
    total_cost: Optional[float]
    odometer: Optional[float] = None
    station: Optional[str] = None
    full_tank: Optional[bool] = True
    notes: Optional[str] = None


class RefuelError(ValueError):
    """A refuel that cannot be stored; `code` is what the page explains."""

    def __init__(self, code: str, message: str = "") -> None:
        super().__init__(message or code)
        self.code = code


def settle(data: RefuelData) -> RefuelData:
    """Any two of the litres, the price per litre and the total give the third (the total wins over the price: it is what was paid)."""
    liters, price, total = data.liters, data.price_per_liter, data.total_cost
    if liters and total is not None:
        price = round(total / liters, 3)
    elif liters and price is not None:
        total = round(price * liters, 2)
    elif not liters and price and total is not None:
        liters = round(total / price, 2)
    data.liters, data.price_per_liter, data.total_cost = liters, price, total
    return data


def check(data: RefuelData) -> None:
    """Raises RefuelError when the refuel makes no sense. Call it after `settle`."""
    if data.day is None:
        raise RefuelError("no_date", "Falta la fecha del repostaje.")
    if data.day > manual_charges.local_day(utcnow()):
        raise RefuelError("future", "La fecha es futura.")
    if not data.liters:
        raise RefuelError("no_liters", "Faltan los litros (o el total y el precio por litro).")
    if not 0 < data.liters <= LITERS_MAX:
        raise RefuelError("liters", "Los litros no son válidos.")
    if data.price_per_liter is not None and not 0 <= data.price_per_liter <= PRICE_MAX:
        raise RefuelError("cost", "El precio por litro no es válido.")
    if data.total_cost is not None and not 0 <= data.total_cost <= COST_MAX:
        raise RefuelError("cost", "El coste no es válido.")
    if data.odometer is not None and not 0 <= data.odometer <= ODOMETER_MAX:
        raise RefuelError("odometer", "El cuentakilómetros no es válido.")


# ───────────────────────── what each column is ─────────────────────────
def guess_mapping(titles: List[str]) -> Dict[str, Optional[int]]:
    """{field: column index or None}, from the titles. Each column is given to one field at most."""
    mapping: Dict[str, Optional[int]] = {f: None for f in FIELDS}
    taken: set = set()
    for field, words in _TITLES.items():
        for index, title in enumerate(titles):
            plain = _plain(title)
            if index in taken or not any(w in plain for w in words):
                continue
            # «Precio por litro» is a price although it names the litres; «Depósito lleno» is not where it was filled
            if field == "liters" and any(w in plain for w in _TITLES["price"]):
                continue
            if field == "odometer" and ("€" in plain or "precio" in plain or "/km" in plain):
                continue
            mapping[field] = index
            taken.add(index)
            break
    return mapping


def clean_mapping(mapping: Any, width: int) -> Dict[str, Optional[int]]:
    out: Dict[str, Optional[int]] = {f: None for f in FIELDS}
    if isinstance(mapping, dict):
        for field in FIELDS:
            value = mapping.get(field)
            if isinstance(value, int) and not isinstance(value, bool) and 0 <= value < width:
                out[field] = value
    return out


def missing_columns(mapping: Dict[str, Optional[int]]) -> List[str]:
    """What the columns lack for any row to be a refuel: the day, and the litres (or the total and the price per litre)."""
    has = lambda f: mapping.get(f) is not None      # noqa: E731
    out = [] if has("date") else ["date"]
    if not (has("liters") or (has("total") and has("price"))):
        out.append("liters")
    return out


def full_of(cell: Any) -> Optional[bool]:
    """«Sí», «x», «lleno», 1 → True; «No», 0 → False; empty → None (not said)."""
    if cell is None or str(cell).strip() == "":
        return None
    if isinstance(cell, bool):
        return cell
    plain = _plain(str(cell))                                   # a number 0 is «no», not an empty cell
    if plain in _YES:
        return True
    if plain in _NO:
        return False
    return None


def _cell(row: List[Any], mapping: Dict[str, Optional[int]], field: str) -> Any:
    index = mapping.get(field)
    return row[index] if index is not None and index < len(row) else None


def _text(cell: Any, limit: int) -> Optional[str]:
    text = re.sub(r"\s+", " ", str(cell or "")).strip()
    return text[:limit] or None


def row_to_refuel(row: List[Any], mapping: Dict[str, Optional[int]]) -> Tuple[Optional[RefuelData], Optional[str]]:
    """(the refuel of the row, None) or (None, the code of why it is not one: the page has the words)."""
    day, hour, understood = day_and_hour(_cell(row, mapping, "date"))
    if not understood:
        return None, "bad_date"
    hour = hour_of(_cell(row, mapping, "time")) or hour
    liters, price, total = number(_cell(row, mapping, "liters")), number(_cell(row, mapping, "price")), number(_cell(row, mapping, "total"))
    odometer = number(_cell(row, mapping, "odometer"))
    # a file without the «lleno» column: a fill-up, as the form assumes; with it, what the cell says (empty = not said)
    full = full_of(_cell(row, mapping, "full")) if mapping.get("full") is not None else True
    data = settle(RefuelData(day=day, hour=hour, liters=liters or None, price_per_liter=price, total_cost=total,
                             odometer=odometer if odometer else None, station=_text(_cell(row, mapping, "station"), STATION_MAX),
                             full_tank=full, notes=_text(_cell(row, mapping, "notes"), NOTES_MAX)))
    try:
        check(data)
    except RefuelError as exc:
        return None, exc.code
    return data, None


def _same_liters(a: float, b: float) -> bool:
    """A detected refuel's litres are the gauge's estimate: the ticket's may be some 15 % apart for the same refuel."""
    return abs(a - b) <= max(2.0, 0.15 * max(a, b))


async def review(db: AsyncSession, vehicle: Vehicle, rows: List[List[Any]], mapping: Dict[str, Optional[int]]) -> List[Dict[str, Any]]:
    """Every row with its verdict: `ok` (with the refuel), `duplicate` or `error` (with the code of the reason)."""
    existing: Dict[datetime.date, List[float]] = {}
    for moment, liters in (await db.execute(select(Refuel.timestamp, Refuel.liters).where(Refuel.vin == vehicle.vin))).all():
        if moment is not None and liters:
            existing.setdefault(manual_charges.local_day(moment), []).append(liters)
    seen: set = set()
    out: List[Dict[str, Any]] = []
    for line, row in enumerate(rows, start=2):                  # line 1 of the file is the titles
        data, problem = row_to_refuel(row, mapping)
        item: Dict[str, Any] = {"line": line, "status": "ok", "problem": None, "data": data}
        if data is None:
            item.update(status="error", problem=problem)
        elif any(_same_liters(data.liters, known) for known in existing.get(data.day, [])):
            item.update(status="duplicate", problem="already")
        else:
            key = (data.day, data.hour, data.liters, data.total_cost, data.odometer)
            if key in seen:
                item.update(status="duplicate", problem="repeated")
            seen.add(key)
        out.append(item)
    return out


def summary(titles: List[str], mapping: Dict[str, Optional[int]], reviewed: List[Dict[str, Any]]) -> Dict[str, Any]:
    """What the review screen shows."""
    def shown(item: Dict[str, Any]) -> Dict[str, Any]:
        d: Optional[RefuelData] = item["data"]
        row = {"line": item["line"], "status": item["status"], "problem": item["problem"]}
        if d is not None:
            row.update(day=d.day.isoformat() if d.day else None, hour=d.hour.strftime("%H:%M") if d.hour else None, liters=d.liters,
                       total_cost=d.total_cost, price_per_liter=d.price_per_liter, odometer=d.odometer, station=d.station, full_tank=d.full_tank)
        return row
    counts = {s: sum(1 for i in reviewed if i["status"] == s) for s in ("ok", "duplicate", "error")}
    ordered = sorted(reviewed, key=lambda i: {"error": 0, "duplicate": 1, "ok": 2}[i["status"]])
    return {"columns": titles, "mapping": mapping, "fields": list(FIELDS), "total": len(reviewed), "counts": counts,
            "missing": missing_columns(mapping), "rows": [shown(i) for i in ordered[:PREVIEW_ROWS]]}


# ───────────────────────── storing and undoing ─────────────────────────
def build(vehicle: Vehicle, data: RefuelData, batch: Optional[str] = None) -> Refuel:
    """The refuel of a row (added by hand: `detected` false), with its local day and hour as the UTC the database keeps."""
    return Refuel(vin=vehicle.vin, timestamp=manual_charges.to_utc(data.day, data.hour), liters=data.liters,
                  price_per_liter=data.price_per_liter, total_cost=data.total_cost, odometer=data.odometer, station=data.station,
                  notes=data.notes, detected=False, full_tank=data.full_tank, import_batch=batch)


async def store(db: AsyncSession, vehicle: Vehicle, reviewed: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Stores the rows that are ready, all with one batch. Does not commit."""
    ready = [i["data"] for i in reviewed if i["status"] == "ok"]
    if not ready:
        raise ImportError_("No hay ningún repostaje listo para importar.")
    batch = secrets.token_hex(8)
    for data in ready:
        db.add(build(vehicle, data, batch))
    days = sorted(d.day for d in ready)
    return {"batch": batch, "imported": len(ready), "liters": round(sum(d.liters for d in ready), 1),
            "cost": round(sum(d.total_cost or 0.0 for d in ready), 2), "from": days[0].isoformat(), "to": days[-1].isoformat(),
            "unpriced": sum(1 for d in ready if d.total_cost is None),
            "skipped": {s: sum(1 for i in reviewed if i["status"] == s) for s in ("duplicate", "error")}}


async def undo(db: AsyncSession, vin: str, batch: str) -> int:
    """Deletes the refuels of one import. Does not commit."""
    if not re.fullmatch(r"[0-9a-f]{16}", batch or ""):
        return 0
    result = await db.execute(delete(Refuel).where(Refuel.vin == vin, Refuel.detected.is_(False), Refuel.import_batch == batch))
    return result.rowcount or 0


TEMPLATE_TITLES = ["Fecha", "Hora", "Litros", "Precio por litro", "Total pagado", "Cuentakilómetros (km)", "Gasolinera", "Depósito lleno", "Notas"]
TEMPLATE_ROWS = [
    [datetime.date(2026, 3, 14), "09:30", 38.4, 1.589, None, 12450, "Gasolinera del pueblo", "Sí", None],
    [datetime.date(2026, 4, 2), "18:10", None, 1.619, 52.40, 13210, "Área de servicio de la autovía", "Sí", "Viaje a la costa"],
    [datetime.date(2026, 4, 20), None, 15.0, None, 24.15, None, None, "No", "Solo media carga"],
]


def template() -> bytes:
    """An example book: the titles EVbro understands best and three rows showing what may be left empty."""
    from openpyxl import Workbook
    from openpyxl.styles import Font

    book = Workbook()
    sheet = book.active
    sheet.title = "Repostajes"
    sheet.append(TEMPLATE_TITLES)
    for cell in sheet[1]:
        cell.font = Font(bold=True)
    for row in TEMPLATE_ROWS:
        sheet.append(row)
    for row in sheet.iter_rows(min_row=2, max_col=1):
        row[0].number_format = "DD/MM/YYYY"
    for column, width in zip("ABCDEFGHI", (13, 8, 9, 16, 13, 21, 32, 15, 24), strict=True):
        sheet.column_dimensions[column].width = width
    buffer = io.BytesIO()
    book.save(buffer)
    return buffer.getvalue()
