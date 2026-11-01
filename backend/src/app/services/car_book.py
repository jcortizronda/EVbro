"""The car book («Libro del coche»): a PDF with everything EVBro knows about a car, section by section, made with reportlab.

Sections (the user picks): `sheet` (identity, purchase, insurance, tyres, energy), `maintenance` (plan and interventions),
`parts` (consumables and references), `drives`, `charges`, `refuels`, `expenses` (with the cost balance), `geofences`,
`documents` (the list, not the files). Layout: cover with the car's photo, a colour band per section, KPI tiles, zebra
tables and simple bar charts per month. Standard Helvetica (Latin-1 covers Spanish, English, Portuguese and the euro sign).

Every text goes through `_()` (app.i18n): the book comes out in the language of the request, i.e. of the account that asks for it.
"""
from __future__ import annotations

import datetime
import io
from collections import defaultdict
from typing import Any, Dict, Iterable, List, Optional

from reportlab.graphics.charts.barcharts import VerticalBarChart
from reportlab.graphics.shapes import Drawing, String
from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER, TA_LEFT
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.platypus import Image, PageBreak, Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import (
    ChargingSession,
    Drive,
    Expense,
    Geofence,
    MaintenanceItem,
    MaintenanceLog,
    MaintenancePlan,
    Refuel,
    Vehicle,
    VehicleDocument,
    VehiclePart,
)
from app.i18n import _, get_language
from app.services import refuel_stats, uploads
from app.services.parts import CATEGORY_BY_KEY, POSITIONS

SECTIONS = ("sheet", "maintenance", "parts", "drives", "charges", "refuels", "expenses", "geofences", "documents")
# Spanish source texts (translated with `_()` when used)
SECTION_TITLES = {"sheet": "Ficha del coche", "maintenance": "Mantenimiento", "parts": "Consumibles y referencias", "drives": "Viajes",
                  "charges": "Cargas", "refuels": "Repostajes", "expenses": "Gastos y costes", "geofences": "Geocercas", "documents": "Documentos"}
DOC_KINDS = {"purchase_invoice": "Factura de compra", "insurance_policy": "Póliza del seguro", "registration": "Permiso de circulación",
             "itv": "ITV", "other": "Otro documento"}
EXPENSE_CATS = {"insurance": "Seguro", "tax": "Impuestos", "parking": "Garaje y aparcamiento", "tolls": "Peajes", "fines": "Multas",
                "wash": "Lavado", "accessories": "Accesorios", "other": "Otros"}
MONTHS = ("ene", "feb", "mar", "abr", "may", "jun", "jul", "ago", "sep", "oct", "nov", "dic")

INK = colors.HexColor("#0b0c0e")
MUTED = colors.HexColor("#6e6e73")
LINE = colors.HexColor("#e3e3e8")
ZEBRA = colors.HexColor("#f5f5f7")
BAND = {"sheet": "#234f70", "maintenance": "#8a6428", "parts": "#5e5ce6", "drives": "#0071e3", "charges": "#2d6a4f", "refuels": "#b45309",
        "expenses": "#7c2d12", "geofences": "#0e7490", "documents": "#374151"}

_ss = getSampleStyleSheet()
H1 = ParagraphStyle("h1", parent=_ss["Title"], fontName="Helvetica-Bold", fontSize=30, leading=36, textColor=colors.white, alignment=TA_LEFT)
H2 = ParagraphStyle("h2", parent=_ss["Heading2"], fontName="Helvetica-Bold", fontSize=16, leading=20, textColor=colors.white, spaceAfter=0)
H3 = ParagraphStyle("h3", parent=_ss["Heading3"], fontName="Helvetica-Bold", fontSize=11.5, leading=14, textColor=INK, spaceBefore=8, spaceAfter=4)
BODY = ParagraphStyle("body", parent=_ss["BodyText"], fontName="Helvetica", fontSize=9.5, leading=13, textColor=INK)
SMALL = ParagraphStyle("small", parent=BODY, fontSize=8, leading=10, textColor=MUTED)
COVER_SUB = ParagraphStyle("coversub", parent=BODY, fontSize=12, leading=16, textColor=colors.white)
KPI_V = ParagraphStyle("kpiv", parent=BODY, fontName="Helvetica-Bold", fontSize=15, leading=18, alignment=TA_CENTER)
KPI_L = ParagraphStyle("kpil", parent=SMALL, alignment=TA_CENTER)
CELL = ParagraphStyle("cell", parent=BODY, fontSize=8.5, leading=11)
CELL_B = ParagraphStyle("cellb", parent=CELL, fontName="Helvetica-Bold")


# ───────────────────────── formatting ─────────────────────────
def _d(dt: Optional[datetime.datetime]) -> str:
    return dt.strftime("%d/%m/%Y") if dt else "—"


def _dt(dt: Optional[datetime.datetime]) -> str:
    return dt.strftime("%d/%m/%Y %H:%M") if dt else "—"


def _n(v: Optional[float], digits: int = 1, unit: str = "") -> str:
    """Number with the separators of the language (1.234,5 in Spanish and Portuguese, 1,234.5 in English)."""
    if v is None:
        return "—"
    s = f"{v:,.{digits}f}"
    if get_language() != "en":
        s = s.replace(",", "X").replace(".", ",").replace("X", ".")
    return f"{s} {unit}".strip()


def _eur(v: Optional[float]) -> str:
    return _n(v, 2, "€")


def _dur(seconds: Optional[int]) -> str:
    if not seconds:
        return "—"
    h, m = divmod(int(seconds) // 60, 60)
    return f"{h} h {m:02d} min" if h else f"{m} min"


def _p(text: Any, style: ParagraphStyle = CELL) -> Paragraph:
    text = "—" if text is None or text == "" else str(text)
    return Paragraph(text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;"), style)


def _month_label(key: str) -> str:
    y, m = key.split("-")
    return f"{_(MONTHS[int(m) - 1])} {y[2:]}"


def _yes_no(flag: bool) -> str:
    return _("sí") if flag else _("no")


# ───────────────────────── building blocks ─────────────────────────
def _band(title: str, key: str, width: float) -> Table:
    t = Table([[Paragraph(title, H2)]], colWidths=[width], rowHeights=[11 * mm])
    t.setStyle(TableStyle([("BACKGROUND", (0, 0), (-1, -1), colors.HexColor(BAND[key])), ("LEFTPADDING", (0, 0), (-1, -1), 10),
                           ("VALIGN", (0, 0), (-1, -1), "MIDDLE"), ("ROUNDEDCORNERS", [6, 6, 6, 6])]))
    return t


def _kpis(items: List[tuple], width: float) -> Table:
    """[(value, label), ...] as up to four tiles per row."""
    rows, row = [], []
    for value, label in items:
        row.append([Paragraph(value, KPI_V), Paragraph(label, KPI_L)])
        if len(row) == 4:
            rows.append(row)
            row = []
    if row:
        row += [""] * (4 - len(row))
        rows.append(row)
    t = Table(rows, colWidths=[width / 4] * 4, hAlign="LEFT")
    t.setStyle(TableStyle([("BOX", (0, 0), (-1, -1), 0, colors.white), ("INNERGRID", (0, 0), (-1, -1), 3, colors.white),
                           ("BACKGROUND", (0, 0), (-1, -1), ZEBRA), ("TOPPADDING", (0, 0), (-1, -1), 7), ("BOTTOMPADDING", (0, 0), (-1, -1), 7),
                           ("ROUNDEDCORNERS", [5, 5, 5, 5])]))
    return t


def _table(head: List[str], rows: List[List[Any]], widths: List[float], align_right: Iterable[int] = ()) -> Table:
    data = [[_p(_(h), CELL_B) for h in head]] + [[c if isinstance(c, Paragraph) else _p(c) for c in r] for r in rows]
    t = Table(data, colWidths=widths, repeatRows=1, hAlign="LEFT")
    style = [("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#e9eef2")), ("LINEBELOW", (0, 0), (-1, 0), 0.8, colors.HexColor("#c5ccd3")),
             ("VALIGN", (0, 0), (-1, -1), "TOP"), ("TOPPADDING", (0, 0), (-1, -1), 4), ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
             ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, ZEBRA]), ("LINEBELOW", (0, -1), (-1, -1), 0.5, LINE)]
    for i in align_right:
        style.append(("ALIGN", (i, 0), (i, -1), "RIGHT"))
    t.setStyle(TableStyle(style))
    return t


def _bars(values: Dict[str, float], unit: str, width: float, color: str, title: str) -> Drawing:
    keys = sorted(values)[-12:]
    d = Drawing(width, 150)
    chart = VerticalBarChart()
    chart.x, chart.y, chart.width, chart.height = 35, 25, width - 50, 105
    chart.data = [[round(values[k], 1) for k in keys]]
    chart.categoryAxis.categoryNames = [_month_label(k) for k in keys]
    chart.categoryAxis.labels.fontName, chart.categoryAxis.labels.fontSize = "Helvetica", 7
    chart.valueAxis.labels.fontName, chart.valueAxis.labels.fontSize = "Helvetica", 7
    chart.valueAxis.valueMin = 0
    chart.bars[0].fillColor = colors.HexColor(color)
    chart.bars[0].strokeColor = None
    chart.useAbsolute = 1                                 # fixed bar width: two months do not become two slabs
    chart.barWidth = 16
    chart.groupSpacing = max(8.0, (chart.width - 16 * len(keys)) / max(1, len(keys)))
    d.add(chart)
    d.add(String(0, 140, f"{_(title)} ({unit})", fontName="Helvetica-Bold", fontSize=8.5, fillColor=MUTED))
    return d


def _kv(pairs: List[tuple], width: float) -> Table:
    rows = [[_p(_(k), CELL_B), _p(v)] for k, v in pairs]
    t = Table(rows, colWidths=[width * 0.38, width * 0.62], hAlign="LEFT")
    t.setStyle(TableStyle([("ROWBACKGROUNDS", (0, 0), (-1, -1), [colors.white, ZEBRA]), ("TOPPADDING", (0, 0), (-1, -1), 3),
                           ("BOTTOMPADDING", (0, 0), (-1, -1), 3), ("LINEBELOW", (0, -1), (-1, -1), 0.5, LINE)]))
    return t


# ───────────────────────── data ─────────────────────────
async def collect(session: AsyncSession, vehicle: Vehicle, sections: Iterable[str]) -> Dict[str, Any]:
    wanted = set(sections)
    vin = vehicle.vin
    data: Dict[str, Any] = {}
    if "maintenance" in wanted:
        data["plan"] = await session.get(MaintenancePlan, vin)
        data["items"] = (await session.execute(select(MaintenanceItem).where(MaintenanceItem.vin == vin).order_by(MaintenanceItem.name))).scalars().all()
        data["logs"] = (await session.execute(select(MaintenanceLog, MaintenanceItem.name).join(MaintenanceItem, MaintenanceItem.id == MaintenanceLog.item_id)
                                              .where(MaintenanceItem.vin == vin).order_by(MaintenanceLog.service_date.desc()))).all()
    if "parts" in wanted:
        data["parts"] = (await session.execute(select(VehiclePart).where(VehiclePart.vin == vin).order_by(VehiclePart.category, VehiclePart.position))).scalars().all()
    if "drives" in wanted:
        data["drives"] = (await session.execute(select(Drive).where(Drive.vin == vin, Drive.end_time.is_not(None)).order_by(Drive.start_time.desc()))).scalars().all()
    if "charges" in wanted:
        data["charges"] = (await session.execute(select(ChargingSession).where(ChargingSession.vin == vin, ChargingSession.end_time.is_not(None))
                                                 .order_by(ChargingSession.start_time.desc()))).scalars().all()
    if "refuels" in wanted:
        refuels = (await session.execute(select(Refuel).where(Refuel.vin == vin))).scalars().all()
        data["refuels"] = sorted(refuels, key=lambda r: r.timestamp, reverse=True)
        data["refuel_stats"] = refuel_stats.compute(refuels, vehicle.fuel_tank_liters or 50.0)
    if "expenses" in wanted:
        data["expenses"] = (await session.execute(select(Expense).where(Expense.vin == vin).order_by(Expense.date.desc()))).scalars().all()
        from app.api.routes_costs import get_costs
        data["costs"] = await get_costs(vin, "all", None, None, session)
    if "geofences" in wanted:
        data["geofences"] = (await session.execute(select(Geofence).where(Geofence.owner_id == vehicle.owner_id).order_by(Geofence.name))).scalars().all()
    if "documents" in wanted:
        data["documents"] = (await session.execute(select(VehicleDocument).where(VehicleDocument.vin == vin).order_by(VehicleDocument.kind, VehicleDocument.uploaded_at))).scalars().all()
    return data


def _photo(vehicle: Vehicle, max_w: float, max_h: float) -> Optional[Image]:
    if not vehicle.photo_filename:
        return None
    try:
        raw = uploads.load(uploads.PHOTOS_FOLDER, vehicle.photo_filename)
        img = Image(io.BytesIO(raw))
        ratio = min(max_w / img.imageWidth, max_h / img.imageHeight)
        img.drawWidth, img.drawHeight = img.imageWidth * ratio, img.imageHeight * ratio
        img.hAlign = "LEFT"
        return img
    except Exception:                                      # noqa: BLE001 — a missing or unreadable photo never breaks the book
        return None


# ───────────────────────── sections ─────────────────────────
def _sheet(v: Vehicle, w: float) -> List[Any]:
    out = [Paragraph(_("Identidad"), H3), _kv([
        ("Nombre", v.name), ("Marca y modelo", f"{v.brand or ''} {v.model or ''}".strip()), ("Versión", v.trim), ("Año", v.model_year),
        ("Color", v.color), ("Etiqueta ambiental", v.env_label), ("Matrícula", v.license_plate), ("Bastidor (VIN)", v.chassis_number or v.vin),
        ("Matriculación", _d(v.registration_date)), ("Entrega", _d(v.delivery_date)), ("Cuentakilómetros", _n(v.odometer, 0, "km"))], w)]
    out += [Paragraph(_("Compra"), H3), _kv([("Precio de compra", _eur(v.purchase_price) if v.purchase_price else "—"),
                                             ("Km al comprarlo", _n(v.purchase_odometer, 0, "km"))], w)]
    out += [Paragraph(_("Seguro"), H3), _kv([("Compañía", v.insurance_company), ("Nº de póliza", v.insurance_policy), ("Cobertura", v.insurance_coverage),
                                             ("Teléfono de asistencia", v.insurance_assistance_phone), ("Renovación", _d(v.insurance_renewal_date))], w)]
    out += [Paragraph(_("Neumáticos"), H3), _kv([("Medida delantera", v.tyre_front_size), ("Medida trasera", v.tyre_rear_size),
                                                 ("Presión delantera", _n(v.tyre_front_pressure_bar, 1, "bar") if v.tyre_front_pressure_bar else "—"),
                                                 ("Presión trasera", _n(v.tyre_rear_pressure_bar, 1, "bar") if v.tyre_rear_pressure_bar else "—")], w)]
    out += [Paragraph(_("Energía"), H3), _kv([("Batería útil", _n(v.battery_capacity_kwh, 2, "kWh")), ("Depósito", _n(v.fuel_tank_liters, 0, "l"))], w)]
    return out


def _maintenance(d: Dict[str, Any], w: float) -> List[Any]:
    out: List[Any] = []
    plan = d.get("plan")
    mode = _("oficial de EBRO") if not plan or plan.mode == "official" else _("personalizado")
    base = _(" · base {p0} / {p1}", p0=_d(plan.baseline_date), p1=_n(plan.baseline_odometer, 0, "km")) if plan and plan.baseline_date else ""
    out.append(Paragraph(_("Plan: {p0}", p0=mode) + base, BODY))
    items = d.get("items") or []
    if items:
        out += [Paragraph(_("Tareas y próximos vencimientos"), H3), _table(
            ["Tarea", "Categoría", "Última vez", "Cada", "Próxima"],
            [[i.name, _(i.category) if i.category else "", f"{_d(i.last_service_date)}{' · ' + _n(i.last_service_odometer, 0, 'km') if i.last_service_odometer else ''}",
              " / ".join(x for x in [(_n(i.interval_km, 0, "km") if i.interval_km else ""), (_("{p0} meses", p0=i.interval_months) if i.interval_months else "")] if x) or "—",
              f"{_d(i.next_due_date)}{' · ' + _n(i.next_due_odometer, 0, 'km') if i.next_due_odometer else ''}"] for i in items],
            [w * 0.30, w * 0.14, w * 0.2, w * 0.14, w * 0.22])]
    logs = d.get("logs") or []
    if logs:
        total = sum(log.cost or 0 for log, _name in logs)
        out += [Paragraph(_("Intervenciones realizadas ({p0} · {p1})", p0=len(logs), p1=_eur(total)), H3), _table(
            ["Fecha", "Intervención", "Km", "Taller", "Coste", "Notas"],
            [[_d(log.service_date), log.title or name, _n(log.odometer, 0), log.workshop, _eur(log.cost) if log.cost else "—", log.notes] for log, name in logs],
            [w * 0.11, w * 0.27, w * 0.1, w * 0.17, w * 0.1, w * 0.25], align_right=(2, 4))]
    if not items and not logs:
        out.append(Paragraph(_("Sin tareas ni intervenciones registradas."), SMALL))
    return out


def _parts(d: Dict[str, Any], w: float) -> List[Any]:
    parts = d.get("parts") or []
    if not parts:
        return [Paragraph(_("Sin consumibles anotados."), SMALL)]
    rows = []
    for p in parts:
        cat = CATEGORY_BY_KEY.get(p.category, {})
        spec = f"{_(cat.get('spec_label', 'Espec.'))}: {p.spec}" if p.spec else ""
        qty = f"{_(cat.get('qty_label', 'Cantidad'))}: {_n(p.quantity, 1)}" if p.quantity is not None else ""
        rows.append([f"{_(cat.get('label', p.category))}{' · ' + _(POSITIONS.get(p.position, '')).lower() if p.position else ''}", p.name if p.name != cat.get("label") else "",
                     p.brand, " · ".join(x for x in (p.reference, _("Fab. {p0}", p0=p.oem_reference) if p.oem_reference else "") if x), " · ".join(x for x in (spec, qty) if x), _d(p.installed_at) if p.installed_at else "—"])
    return [_table(["Pieza", "Nombre", "Marca", "Referencia", "Detalles", "Montado"], rows,
                   [w * 0.2, w * 0.17, w * 0.13, w * 0.15, w * 0.23, w * 0.12])]


def _drives(d: Dict[str, Any], w: float) -> List[Any]:
    drives = d.get("drives") or []
    if not drives:
        return [Paragraph(_("Sin viajes registrados."), SMALL)]
    km = sum(x.distance_km or 0 for x in drives)
    kwh = sum(x.energy_consumed_kwh or 0 for x in drives)
    litres = sum(x.fuel_used_l or 0 for x in drives)
    secs = sum(x.duration_seconds or 0 for x in drives)
    months: Dict[str, float] = defaultdict(float)
    for x in drives:
        months[x.start_time.strftime("%Y-%m")] += x.distance_km or 0
    out: List[Any] = [_kpis([(str(len(drives)), _("viajes")), (_n(km, 0, "km"), _("recorridos")), (_n(kwh, 1, "kWh"), _("eléctrico")),
                             (_n(litres, 1, "l"), _("gasolina")), (_dur(secs), _("al volante")),
                             (_n(km / len(drives), 1, "km"), _("por viaje")),
                             (_n(kwh / km * 100, 1, "kWh/100 km") if km else "—", _("consumo eléctrico medio")),
                             (_n(max((x.speed_max_kmh or 0) for x in drives), 0, "km/h"), _("velocidad máxima"))], w),
                     Spacer(1, 6), _bars(months, "km", w, BAND["drives"], "Kilómetros por mes"), Spacer(1, 4),
                     Paragraph(_("Últimos viajes ({p0} de {p1})", p0=min(len(drives), 40), p1=len(drives)), H3)]
    out.append(_table(["Fecha", "Origen → destino", "Km", "Duración", "Batería", "Energía"],
                      [[_dt(x.start_time), f"{x.start_address or '—'} → {x.end_address or '—'}", _n(x.distance_km, 1), _dur(x.duration_seconds),
                        f"{_n(x.start_battery_pct, 0)} → {_n(x.end_battery_pct, 0)} %" if x.start_battery_pct is not None and x.end_battery_pct is not None else "—",
                        " · ".join(v for v in [_n(x.energy_consumed_kwh, 1, "kWh") if x.energy_consumed_kwh else "", _n(x.fuel_used_l, 1, "l") if x.fuel_used_l else ""] if v) or "—"]
                       for x in drives[:40]], [w * 0.14, w * 0.38, w * 0.08, w * 0.12, w * 0.14, w * 0.14], align_right=(2,)))
    return out


def _charges(d: Dict[str, Any], w: float) -> List[Any]:
    charges = d.get("charges") or []
    if not charges:
        return [Paragraph(_("Sin cargas registradas."), SMALL)]
    kwh = sum(c.kwh_added or 0 for c in charges)
    cost = sum(c.charge_cost or 0 for c in charges)
    months: Dict[str, float] = defaultdict(float)
    for c in charges:
        months[c.start_time.strftime("%Y-%m")] += c.kwh_added or 0
    out: List[Any] = [_kpis([(str(len(charges)), _("cargas")), (_n(kwh, 1, "kWh"), _("a la batería")), (_eur(cost), _("gastado")),
                             (_n(cost / kwh, 3, "€/kWh") if kwh else "—", _("precio medio")),
                             (_n(max((c.max_charge_power_kw or 0) for c in charges), 1, "kW"), _("potencia máxima")),
                             (_dur(sum(c.duration_seconds or 0 for c in charges)), _("enchufado"))], w),
                     Spacer(1, 6), _bars(months, "kWh", w, BAND["charges"], "Energía cargada por mes"), Spacer(1, 4),
                     Paragraph(_("Últimas cargas ({p0} de {p1})", p0=min(len(charges), 40), p1=len(charges)), H3)]
    out.append(_table(["Inicio", "Lugar", "Tipo", "Batería", "kWh", "Duración", "Coste"],
                      [[_dt(c.start_time), c.address or "—", "DC" if c.charge_type == "dc" else "AC",
                        f"{_n(c.start_battery_pct, 0)} → {_n(c.end_battery_pct, 0)} %" if c.start_battery_pct is not None and c.end_battery_pct is not None else "—",
                        _n(c.kwh_added, 1), _dur(c.duration_seconds), _eur(c.charge_cost) if c.charge_cost else "—"] for c in charges[:40]],
                      [w * 0.15, w * 0.3, w * 0.07, w * 0.13, w * 0.09, w * 0.13, w * 0.13], align_right=(4, 6)))
    return out


def _refuels(d: Dict[str, Any], w: float) -> List[Any]:
    refuels = d.get("refuels") or []
    s = d.get("refuel_stats") or {}
    if not refuels:
        return [Paragraph(_("Sin repostajes registrados."), SMALL)]
    cons = s.get("l_per_100km") if s.get("l_per_100km") is not None else s.get("gauge_l_per_100km")
    out: List[Any] = [_kpis([(str(s.get("count", 0)), _("repostajes")), (_n(s.get("total_liters"), 1, "l"), _("litros")), (_eur(s.get("total_cost")), _("gastado")),
                             (_n(cons, 1, "l/100 km") if cons is not None else "—", _("consumo real") if s.get("l_per_100km") is not None else _("consumo estimado")),
                             (_n(s.get("avg_price_per_liter"), 3, "€/l") if s.get("avg_price_per_liter") is not None else "—", _("precio medio del litro")),
                             (_eur(s.get("fuel_cost_per_100km")) if s.get("fuel_cost_per_100km") is not None else "—", _("gasolina por 100 km")),
                             (_n(s.get("full_tank_range_km"), 0, "km") if s.get("full_tank_range_km") else "—", _("autonomía con depósito lleno")),
                             (_n(s.get("avg_km_between_refuels"), 0, "km") if s.get("avg_km_between_refuels") else "—", _("entre repostajes"))], w)]
    months = {m["month"]: m["liters"] for m in s.get("months", [])}
    if months:
        out += [Spacer(1, 6), _bars(months, "l", w, BAND["refuels"], "Litros por mes"), Spacer(1, 4)]
    per = s.get("per_refuel", {})
    out += [Paragraph(_("Repostajes"), H3), _table(["Fecha", "Gasolinera", "Litros", "€/l", "Total", "Lleno", "Consumo del tramo"],
                                                   [[_dt(r.timestamp), r.station or r.address or (_("Detectado") if r.detected else _("A mano")), _n(r.liters, 1),
                                                     _n(r.price_per_liter, 3) if r.price_per_liter else "—", _eur(r.total_cost) if r.total_cost is not None else _("sin precio"),
                                                     _yes_no(bool(per.get(r.id, {}).get("full"))),
                                                     _n(per.get(r.id, {}).get("l_per_100km"), 1, "l/100 km") if per.get(r.id, {}).get("l_per_100km") else "—"] for r in refuels[:60]],
                                                   [w * 0.15, w * 0.27, w * 0.09, w * 0.09, w * 0.12, w * 0.08, w * 0.2], align_right=(2, 3, 4))]
    return out


def _expenses(d: Dict[str, Any], w: float) -> List[Any]:
    costs = d.get("costs") or {}
    t = costs.get("totals", {})
    out: List[Any] = [_kpis([(_eur(t.get("all")), _("total desde la compra")), (_eur(t.get("running")), _("gasto de uso")),
                             (_eur(costs.get("per_100km")) if costs.get("per_100km") else "—", _("por 100 km")),
                             (_n(costs.get("km"), 0, "km") if costs.get("km") else "—", _("km recorridos")),
                             (_eur(t.get("charges")), _("cargas")), (_eur(t.get("refuels")), _("repostajes")), (_eur(t.get("maintenance")), _("mantenimiento")),
                             (_eur(t.get("expenses")), _("gastos"))], w)]
    monthly = costs.get("monthly") or []
    if monthly:
        out += [Spacer(1, 6), _bars({m["month"]: m["charges"] + m["refuels"] + m["maintenance"] + m["expenses"] for m in monthly}, "€", w, BAND["expenses"], "Gasto por mes"), Spacer(1, 4)]
    expenses = d.get("expenses") or []
    if expenses:
        out += [Paragraph(_("Gastos ({p0})", p0=len(expenses)), H3), _table(["Fecha", "Concepto", "Categoría", "Importe", "Próximo vencimiento", "Notas"],
                                                                            [[_d(e.date), e.title, _(EXPENSE_CATS.get(e.category, e.category)), _eur(e.amount), _d(e.next_due_date), e.notes] for e in expenses],
                                                                            [w * 0.11, w * 0.27, w * 0.15, w * 0.11, w * 0.14, w * 0.22], align_right=(3,))]
    else:
        out.append(Paragraph(_("Sin gastos anotados."), SMALL))
    return out


def _geofences(d: Dict[str, Any], w: float) -> List[Any]:
    gfs = d.get("geofences") or []
    if not gfs:
        return [Paragraph(_("Sin geocercas."), SMALL)]
    modes = {"fixed": _("precio fijo"), "free": _("gratis"), "time_of_use": "2.0TD", "pvpc": "PVPC"}
    return [_table(["Nombre", "Coordenadas", "Radio", "Punto de carga", "Tarifa", "Avisos"],
                   [[g.name, f"{g.latitude:.5f}, {g.longitude:.5f}", _n(g.radius_meters, 0, "m"), _yes_no(bool(g.is_charging_spot)),
                     (modes.get(g.cost_mode, g.cost_mode) + (f" · {_n(g.cost_per_kwh, 3, '€/kWh')}" if g.cost_mode == "fixed" else "")) if g.is_charging_spot else "—",
                     " / ".join(x for x in [(_("entrada") if g.notify_enter is not False else ""), (_("salida") if g.notify_exit is not False else "")] if x) or _("ninguno")] for g in gfs],
                   [w * 0.22, w * 0.2, w * 0.1, w * 0.12, w * 0.2, w * 0.16])]


def _documents(d: Dict[str, Any], w: float) -> List[Any]:
    docs = d.get("documents") or []
    if not docs:
        return [Paragraph(_("Sin documentos subidos."), SMALL)]
    return [Paragraph(_("Los ficheros no van en el libro; están en la aplicación y en la exportación de la cuenta."), SMALL),
            _table(["Tipo", "Nombre", "Fichero", "Tamaño", "Subido"],
                   [[_(DOC_KINDS.get(x.kind, x.kind)), x.title, x.original_name, _n(x.size / 1024 / 1024, 1, "MB") if x.size > 512 * 1024 else _n(x.size / 1024, 0, "KB"), _d(x.uploaded_at)] for x in docs],
                   [w * 0.2, w * 0.25, w * 0.3, w * 0.1, w * 0.15])]


RENDERERS = {"maintenance": _maintenance, "parts": _parts, "drives": _drives, "charges": _charges, "refuels": _refuels, "expenses": _expenses,
             "geofences": _geofences, "documents": _documents}


# ───────────────────────── document ─────────────────────────
def _on_page(vehicle: Vehicle):
    label = _("Libro del coche") + f" · {vehicle.name or vehicle.model or 'Ebro'}" + (f" · {vehicle.license_plate}" if vehicle.license_plate else "")

    def draw(canvas, doc):
        canvas.saveState()
        canvas.setFont("Helvetica", 7.5)
        canvas.setFillColor(MUTED)
        canvas.drawString(doc.leftMargin, 10 * mm, label)
        canvas.drawRightString(A4[0] - doc.rightMargin, 10 * mm, _("EVBro · página {p0}", p0=doc.page))
        canvas.restoreState()
    return draw


def _cover(vehicle: Vehicle, sections: List[str], width: float) -> List[Any]:
    title = Table([[Paragraph(_("Libro del coche"), H1)], [Paragraph(f"{vehicle.brand or ''} {vehicle.model or ''}".strip() or "Ebro", COVER_SUB)],
                   [Paragraph(" · ".join(x for x in [vehicle.name, vehicle.license_plate, f"VIN {vehicle.chassis_number or vehicle.vin}"] if x), COVER_SUB)]],
                  colWidths=[width], rowHeights=[22 * mm, 8 * mm, 8 * mm])
    title.setStyle(TableStyle([("BACKGROUND", (0, 0), (-1, -1), colors.HexColor("#234f70")), ("LEFTPADDING", (0, 0), (-1, -1), 14),
                               ("VALIGN", (0, 0), (-1, -1), "MIDDLE"), ("ROUNDEDCORNERS", [10, 10, 10, 10]), ("BOTTOMPADDING", (0, -1), (-1, -1), 12)]))
    out: List[Any] = [Spacer(1, 30 * mm), title, Spacer(1, 10 * mm)]
    photo = _photo(vehicle, width, 95 * mm)
    if photo:
        out += [photo, Spacer(1, 8 * mm)]
    now = datetime.datetime.now()
    out.append(Paragraph(_("Generado por EVBro el {p0} a las {p1}.", p0=now.strftime("%d/%m/%Y"), p1=now.strftime("%H:%M")), BODY))
    out.append(Paragraph(_("Contenido: {p0}.", p0=", ".join(_(SECTION_TITLES[s]) for s in sections)), BODY))
    out.append(Paragraph(_("Los consumos, kWh y litros de los viajes y las cargas son estimaciones a partir de lo que informa el coche; los repostajes detectados llevan litros estimados salvo que se corrigieran con el ticket."), SMALL))
    out.append(PageBreak())
    return out


async def build(session: AsyncSession, vehicle: Vehicle, sections: Iterable[str]) -> bytes:
    chosen = [s for s in SECTIONS if s in set(sections)] or list(SECTIONS)
    data = await collect(session, vehicle, chosen)
    buf = io.BytesIO()
    doc = SimpleDocTemplate(buf, pagesize=A4, leftMargin=16 * mm, rightMargin=16 * mm, topMargin=16 * mm, bottomMargin=18 * mm,
                            title=f"{_('Libro del coche')} · {vehicle.name or vehicle.vin}", author="EVBro")
    width = A4[0] - doc.leftMargin - doc.rightMargin
    story: List[Any] = _cover(vehicle, chosen, width)
    for i, key in enumerate(chosen):
        story.append(_band(_(SECTION_TITLES[key]), key, width))
        story.append(Spacer(1, 6))
        body = _sheet(vehicle, width) if key == "sheet" else RENDERERS[key](data, width)
        story.extend(body)
        if i < len(chosen) - 1:
            story.append(PageBreak())
    on_page = _on_page(vehicle)
    doc.build(story, onFirstPage=on_page, onLaterPages=on_page)
    return buf.getvalue()
