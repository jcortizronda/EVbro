"""Charges from a spreadsheet: an Excel book (.xlsx) or a CSV with one charge per row (Carga → «Importar»).

Three steps, and nothing is stored before the last one:
  1. the file is read into a table of cells (`read_table`);
  2. its columns are understood from their titles, whatever they are called and in whatever order (`guess_mapping`; the user
     corrects it on screen), and every row is turned into a charge or into the reason it cannot be one (`review`);
  3. the rows that are ready are stored as manual charges, all with the same batch so the import can be undone whole.

Only the kWh are required in a row. A row that looks like a charge the car already has (same day, about the same kWh) is not
brought in again. The file is read in memory and never kept: only the charges are.
"""
from __future__ import annotations

import csv
import datetime
import io
import re
import secrets
import unicodedata
from typing import Any, Dict, List, Optional, Tuple

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import ChargingSession, Geofence, Vehicle
from app.services import manual_charges
from app.services.manual_charges import ChargeData, ChargeError

MAX_BYTES = 2 * 1024 * 1024
MAX_ROWS = 1000
MAX_COLUMNS = 40
PREVIEW_ROWS = 200                    # how many rows the review screen lists (all of them are counted and imported)

FIELDS = ("date", "time", "kwh", "price", "total", "place", "battery", "type")
REQUIRED = ("kwh",)             # or the total and the price per kWh, or the battery %: see `energy_columns`
# Words a column title may carry, without accents and in lower case. The first field whose words match takes the column.
_TITLES: Dict[str, Tuple[str, ...]] = {
    "price": ("precio", "price", "preco", "€/kwh", "eur/kwh", "tarifa", "rate"),
    "total": ("total", "importe", "coste", "costo", "cost", "pagado", "amount", "custo", "valor"),
    "kwh": ("kwh", "energia", "energy", "consumo"),
    "battery": ("bateria", "battery", "soc", "%", "porcentaje"),
    "time": ("hora", "time", "inicio", "start"),
    "date": ("fecha", "date", "dia", "data", "day"),
    "place": ("lugar", "sitio", "ubicacion", "cargador", "donde", "location", "place", "station", "estacion", "local", "direccion", "address"),
    "type": ("tipo", "type", "ac/dc", "corriente", "conector"),
}


class ImportError_(ValueError):
    """The file cannot be read as a table of charges (the message is shown to the user)."""


def _plain(text: Any) -> str:
    return "".join(c for c in unicodedata.normalize("NFD", str(text or "").strip().lower()) if unicodedata.category(c) != "Mn")


# ───────────────────────── 1. the file as a table ─────────────────────────
def _read_xlsx(data: bytes) -> List[List[Any]]:
    from openpyxl import load_workbook

    try:
        book = load_workbook(io.BytesIO(data), read_only=True, data_only=True)
    except Exception as exc:                                  # noqa: BLE001 — a broken or disguised file
        raise ImportError_("No se pudo abrir el Excel. Guárdalo como .xlsx o como CSV y vuelve a probar.") from exc
    try:
        sheet = book.worksheets[0] if book.worksheets else None
        if sheet is None:
            return []
        rows: List[List[Any]] = []
        for row in sheet.iter_rows(values_only=True, max_col=MAX_COLUMNS):
            rows.append(list(row))
            if len(rows) > MAX_ROWS + 50:                      # blank rows are dropped later; this only stops a runaway sheet
                break
        return rows
    finally:
        book.close()


def _read_csv(data: bytes) -> List[List[Any]]:
    for encoding in ("utf-8-sig", "cp1252"):
        try:
            text = data.decode(encoding)
            break
        except UnicodeDecodeError:
            continue
    else:
        raise ImportError_("No se pudo leer el fichero de texto.")
    head = text[:4096]
    delimiter = max((";", ",", "\t", "|"), key=head.count)       # the one the file uses most: «1,5;2,0» is semicolon-separated
    return [row[:MAX_COLUMNS] for row in csv.reader(io.StringIO(text), delimiter=delimiter)][:MAX_ROWS + 50]


def read_table(filename: str, data: bytes, too_many: str = "El fichero tiene más de 1.000 cargas: pártelo en varios.") -> Tuple[List[str], List[List[Any]]]:
    """(titles of the columns, rows of cells). The first row with something in it is the titles. Also read for the refuels
    (services/refuel_import.py), which say «repostajes» in `too_many`."""
    if not data:
        raise ImportError_("El fichero está vacío.")
    if len(data) > MAX_BYTES:
        raise ImportError_("El fichero es demasiado grande (máximo 2 MB).")
    name = (filename or "").lower()
    if name.endswith(".xlsx") or data[:2] == b"PK":
        rows = _read_xlsx(data)
    elif name.endswith(".xls"):
        raise ImportError_("Ese es el formato antiguo de Excel (.xls). Guárdalo como .xlsx o como CSV.")
    else:
        rows = _read_csv(data)
    rows = [r for r in rows if any(str(c).strip() for c in r if c is not None)]
    if not rows:
        raise ImportError_("El fichero no tiene filas.")
    # a sheet is read as wide as it may be: the columns nobody wrote in are not columns
    used = [any(i < len(r) and r[i] is not None and str(r[i]).strip() for r in rows) for i in range(max(len(r) for r in rows))]
    while used and not used[-1]:
        used.pop()
    rows = [r[:len(used)] for r in rows]
    width = len(used)
    titles = [str(c).strip() if c is not None else "" for c in rows[0]] + [""] * (width - len(rows[0]))
    body = [list(r) + [None] * (width - len(r)) for r in rows[1:]]
    if not body:
        raise ImportError_("El fichero solo tiene la fila de títulos.")
    if len(body) > MAX_ROWS:
        raise ImportError_(too_many)
    return [t or f"Columna {i + 1}" for i, t in enumerate(titles)], body


# ───────────────────────── 2. what each column is ─────────────────────────
def guess_mapping(titles: List[str]) -> Dict[str, Optional[int]]:
    """{field: column index or None}, from the titles. Each column is given to one field at most."""
    mapping: Dict[str, Optional[int]] = {f: None for f in FIELDS}
    taken: set = set()
    for field, words in _TITLES.items():
        for index, title in enumerate(titles):
            plain = _plain(title)
            if index not in taken and any(w in plain for w in words):
                # «Precio por kWh» is a price although it names the kWh; «Total (€)» is not a price although it carries a €
                if field == "kwh" and any(w in plain for w in _TITLES["price"]):
                    continue
                mapping[field] = index
                taken.add(index)
                break
    return mapping


def energy_columns(mapping: Dict[str, Optional[int]]) -> bool:
    """Whether the columns can give a charge its energy: the kWh, the total and the price, or the battery %."""
    has = lambda f: mapping.get(f) is not None      # noqa: E731
    return has("kwh") or (has("total") and has("price")) or has("battery")


def clean_mapping(mapping: Any, width: int) -> Dict[str, Optional[int]]:
    """A mapping sent back by the page, kept to the known fields and to columns that exist."""
    out: Dict[str, Optional[int]] = {f: None for f in FIELDS}
    if isinstance(mapping, dict):
        for field in FIELDS:
            value = mapping.get(field)
            if isinstance(value, int) and not isinstance(value, bool) and 0 <= value < width:
                out[field] = value
    return out


_NUMBER = re.compile(r"-?\d+(?:[.,]\d+)?")


def number(cell: Any) -> Optional[float]:
    """«22,4», «22.4 kWh», «11,20 €», 22.4 → a float; None when there is no number in it."""
    if cell is None or isinstance(cell, bool):
        return None
    if isinstance(cell, (int, float)):
        return float(cell)
    text = str(cell).strip().replace(" ", " ")
    if re.fullmatch(r"-?\d{1,3}(\.\d{3})+(,\d+)?", text.split(" ")[0]):       # 1.234,56: the dot groups thousands
        text = text.replace(".", "")
    found = _NUMBER.search(text)
    return float(found.group(0).replace(",", ".")) if found else None


_DATE_FORMATS = ("%d/%m/%Y", "%d-%m-%Y", "%d.%m.%Y", "%Y-%m-%d", "%Y/%m/%d", "%d/%m/%y", "%d-%m-%y")


def day_and_hour(cell: Any) -> Tuple[Optional[datetime.date], Optional[datetime.time], bool]:
    """(day, hour if the cell carries one, understood). An empty cell is understood: the day is simply not known."""
    if cell is None or str(cell).strip() == "":
        return None, None, True
    if isinstance(cell, datetime.datetime):
        return cell.date(), (cell.time().replace(second=0, microsecond=0) if (cell.hour or cell.minute) else None), True
    if isinstance(cell, datetime.date):
        return cell, None, True
    text = str(cell).strip()
    hour = None
    parts = re.split(r"[ T]", text, maxsplit=1)
    if len(parts) == 2:
        hour = hour_of(parts[1])
    for fmt in _DATE_FORMATS:
        try:
            return datetime.datetime.strptime(parts[0], fmt).date(), hour, True
        except ValueError:
            continue
    return None, None, False


def hour_of(cell: Any) -> Optional[datetime.time]:
    if isinstance(cell, datetime.datetime):
        return cell.time().replace(second=0, microsecond=0)
    if isinstance(cell, datetime.time):
        return cell.replace(second=0, microsecond=0)
    found = re.search(r"(\d{1,2})[:.h](\d{2})", str(cell or ""))
    if not found:
        return None
    hours, minutes = int(found.group(1)), int(found.group(2))
    return datetime.time(hours, minutes) if hours < 24 and minutes < 60 else None


def kind_of(cell: Any) -> str:
    plain = _plain(cell)
    return "dc" if any(w in plain for w in ("dc", "rapid", "continua", "fast", "ccs", "chademo")) else "ac"


def _cell(row: List[Any], mapping: Dict[str, Optional[int]], field: str) -> Any:
    index = mapping.get(field)
    return row[index] if index is not None and index < len(row) else None


def row_to_charge(row: List[Any], mapping: Dict[str, Optional[int]], geofences: Dict[str, int]) -> Tuple[Optional[ChargeData], Optional[str]]:
    """(the charge of the row, None) or (None, the code of why it is not one: the page has the words)."""
    kwh = number(_cell(row, mapping, "kwh"))
    day, hour, understood = day_and_hour(_cell(row, mapping, "date"))
    if not understood:
        return None, "bad_date"
    hour = hour_of(_cell(row, mapping, "time")) or hour
    price, total = number(_cell(row, mapping, "price")), number(_cell(row, mapping, "total"))
    if kwh is not None and total is not None:
        price = None                                           # the total is what was paid; the price per kWh follows from it
    place = manual_charges.clean_place(str(_cell(row, mapping, "place") or ""))
    geofence_id = geofences.get(_plain(place)) if place else None
    battery = number(_cell(row, mapping, "battery"))
    if not kwh and not (total and price) and not battery:
        return None, "no_energy"
    data = ChargeData(kwh=kwh or None, day=day, hour=hour, battery_gain_pct=battery if battery else None, price_per_kwh=price, total_cost=total,
                      free=total == 0 and mapping.get("total") is not None and str(_cell(row, mapping, "total")).strip() != "",
                      geofence_id=geofence_id, place=None if geofence_id else place,
                      charge_type=kind_of(_cell(row, mapping, "type")) if mapping.get("type") is not None else "ac")
    if data.free:
        data.total_cost = data.price_per_kwh = None
    try:
        manual_charges.check(data)
    except ChargeError as exc:
        return None, exc.code
    return data, None


def energy_of(data: ChargeData, vehicle: Vehicle) -> float:
    """The charger's kWh of a row, or what its % says reached the battery."""
    kwh = manual_charges.charger_kwh(data)
    if kwh is not None:
        return kwh
    return round((data.battery_gain_pct or 0.0) / 100.0 * (vehicle.battery_capacity_kwh or 0.0), 2)


def _same_kwh(a: float, b: float) -> bool:
    """A file holds the charger's kWh and a detected charge the battery's: some 10 % apart for the same charge."""
    return abs(a - b) <= max(0.5, 0.15 * max(a, b))


async def review(db: AsyncSession, vehicle: Vehicle, titles: List[str], rows: List[List[Any]], mapping: Dict[str, Optional[int]]) -> List[Dict[str, Any]]:
    """Every row with its verdict: `ok` (with the charge), `duplicate` or `error` (with the code of the reason)."""
    geofences = {_plain(g.name): g.id for g in (await db.execute(select(Geofence).where(Geofence.owner_id == vehicle.owner_id))).scalars()
                 if vehicle.owner_id is not None}
    existing: Dict[datetime.date, List[float]] = {}
    for start, added, used, unknown in (await db.execute(select(ChargingSession.start_time, ChargingSession.kwh_added, ChargingSession.kwh_used,
                                                                ChargingSession.date_unknown).where(ChargingSession.vin == vehicle.vin))).all():
        if not unknown and start is not None:
            existing.setdefault(manual_charges.local_day(start), []).extend(k for k in (added, used) if k)
    seen: set = set()
    out: List[Dict[str, Any]] = []
    for line, row in enumerate(rows, start=2):                  # line 1 of the file is the titles
        data, problem = row_to_charge(row, mapping, geofences)
        item: Dict[str, Any] = {"line": line, "status": "ok", "problem": None, "data": data}
        if data is None:
            item.update(status="error", problem=problem)
        elif data.day is not None and any(_same_kwh(energy_of(data, vehicle), k) for k in existing.get(data.day, [])):
            item.update(status="duplicate", problem="already")
        else:
            key = (data.day, data.hour, energy_of(data, vehicle), data.total_cost, data.price_per_kwh, data.place, data.geofence_id)
            if data.day is not None and key in seen:
                item.update(status="duplicate", problem="repeated")
            seen.add(key)
        out.append(item)
    return out


def summary(titles: List[str], mapping: Dict[str, Optional[int]], reviewed: List[Dict[str, Any]], geofence_names: Dict[int, str]) -> Dict[str, Any]:
    """What the review screen shows."""
    def shown(item: Dict[str, Any]) -> Dict[str, Any]:
        d: Optional[ChargeData] = item["data"]
        row = {"line": item["line"], "status": item["status"], "problem": item["problem"]}
        if d is not None:
            row.update(day=d.day.isoformat() if d.day else None, hour=d.hour.strftime("%H:%M") if d.hour else None, kwh=manual_charges.charger_kwh(d),
                       total_cost=d.total_cost, price_per_kwh=d.price_per_kwh, free=d.free, battery_gain_pct=d.battery_gain_pct,
                       place=geofence_names.get(d.geofence_id) if d.geofence_id else d.place, charge_type=d.charge_type)
        return row
    counts = {s: sum(1 for i in reviewed if i["status"] == s) for s in ("ok", "duplicate", "error")}
    # the ones that need a look first: the list is cut, and what will not be imported must not hide below the fold
    ordered = sorted(reviewed, key=lambda i: {"error": 0, "duplicate": 1, "ok": 2}[i["status"]])
    return {"columns": titles, "mapping": mapping, "fields": list(FIELDS), "required": list(REQUIRED), "total": len(reviewed), "counts": counts,
            "missing": [] if energy_columns(mapping) else ["energy"],
            "rows": [shown(i) for i in ordered[:PREVIEW_ROWS]]}


# ───────────────────────── 3. storing and undoing ─────────────────────────
async def store(db: AsyncSession, vehicle: Vehicle, reviewed: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Stores the rows that are ready, all with one batch. Does not commit."""
    ready = [i["data"] for i in reviewed if i["status"] == "ok"]
    if not ready:
        raise ImportError_("No hay ninguna carga lista para importar.")
    batch = secrets.token_hex(8)
    charges = [await manual_charges.create(db, vehicle, data, batch=batch) for data in ready]
    days = sorted(d.day for d in ready if d.day is not None)
    stored = list(ready)
    return {"batch": batch, "imported": len(stored), "kwh": round(sum(energy_of(d, vehicle) for d in stored), 1),
            "cost": round(sum(c.charge_cost or 0.0 for c in charges), 2),
            "from": days[0].isoformat() if days else None, "to": days[-1].isoformat() if days else None,
            "undated": sum(1 for d in ready if d.day is None),
            "skipped": {s: sum(1 for i in reviewed if i["status"] == s) for s in ("duplicate", "error")}}


async def undo(db: AsyncSession, vin: str, batch: str) -> int:
    """Deletes the charges of one import. Does not commit."""
    if not re.fullmatch(r"[0-9a-f]{16}", batch or ""):
        return 0
    result = await db.execute(delete(ChargingSession).where(ChargingSession.vin == vin, ChargingSession.manual.is_(True),
                                                            ChargingSession.import_batch == batch))
    return result.rowcount or 0


TEMPLATE_TITLES = ["Fecha", "Hora", "kWh", "Precio por kWh", "Total pagado", "Lugar", "Batería cargada (%)", "Tipo"]
TEMPLATE_ROWS = [
    [datetime.date(2026, 3, 14), "22:30", 18.8, 0.135, None, "Casa", 72, "AC"],
    [datetime.date(2026, 4, 2), "18:10", 22.4, None, 11.20, "Cargador rápido de la autovía", None, "DC"],
    [None, None, 9.6, None, 0, "Trabajo", None, "AC"],
]


def template() -> bytes:
    """An example book: the titles EVbro understands best and three rows showing what may be left empty."""
    from openpyxl import Workbook
    from openpyxl.styles import Font

    book = Workbook()
    sheet = book.active
    sheet.title = "Cargas"
    sheet.append(TEMPLATE_TITLES)
    for cell in sheet[1]:
        cell.font = Font(bold=True)
    for row in TEMPLATE_ROWS:
        sheet.append(row)
    for row in sheet.iter_rows(min_row=2, max_col=1):
        row[0].number_format = "DD/MM/YYYY"
    for column, width in zip("ABCDEFGH", (13, 8, 8, 15, 13, 32, 20, 8), strict=True):
        sheet.column_dimensions[column].width = width
    buffer = io.BytesIO()
    book.save(buffer)
    return buffer.getvalue()
