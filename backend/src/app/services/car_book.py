"""The car book («Libro del coche»): a PDF with everything EVbro knows about a car, section by section, made with reportlab.

Sections (the user picks): `sheet` (identity, purchase, insurance, tyres, energy), `maintenance` (plan and interventions),
`parts` (consumables and references), `drives`, `charges`, `refuels`, `expenses` (with the cost balance), `geofences`,
`documents` (the list, not the files). Layout (redone on 25-11-2026 to look like a professional report): a full-page cover in
EVbro's colours with the logo, the car (its photo or, failing that, the top view of the 3D model shown on Inicio), the plate and
its key data; a contents page with page numbers and links (and PDF bookmarks); every page with the EVbro header and «page X of
Y»; numbered section openers in the section's colour, KPI cards, tinted tables and bar charts. Short sections share a page.
Standard Helvetica (Latin-1 covers Spanish, English, Portuguese and the euro sign).

Every text goes through `_()` (app.i18n): the book comes out in the language of the request, i.e. of the account that asks for it.
"""
from __future__ import annotations

import contextvars
import datetime
import functools
import io
import json
from collections import defaultdict
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional

from reportlab.graphics.charts.barcharts import VerticalBarChart
from reportlab.graphics.shapes import Drawing, String
from reportlab.lib import colors
from reportlab.lib.enums import TA_LEFT
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.lib.utils import ImageReader
from reportlab.pdfgen import canvas as rl_canvas
from reportlab.platypus import (
    BaseDocTemplate,
    CondPageBreak,
    Flowable,
    Frame,
    NextPageTemplate,
    PageBreak,
    PageTemplate,
    Paragraph,
    Spacer,
    Table,
    TableStyle,
)
from reportlab.platypus.tableofcontents import TableOfContents
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_timezone
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
from app.paths import frontend_dir
from app.services import battery_capacity, refuel_stats, stats, uploads
from app.services.manual_charges import energy_kwh
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

INK = colors.HexColor("#0f172a")
MUTED = colors.HexColor("#64748b")
LINE = colors.HexColor("#e2e8f0")
ZEBRA = colors.HexColor("#f8fafc")
# EVbro's colours (the logo: a night-blue tile, cyan → blue → indigo → purple)
NAVY, NAVY_2 = colors.HexColor("#0b1020"), colors.HexColor("#1a2150")
BRAND = [colors.HexColor(c) for c in ("#22d3ee", "#3b82f6", "#6366f1", "#a855f7")]
SOFT_ON_NAVY = colors.HexColor("#c7d2fe")
BAND = {"sheet": "#2563eb", "maintenance": "#b45309", "parts": "#6366f1", "drives": "#0284c7", "charges": "#059669", "refuels": "#ea580c",
        "expenses": "#be123c", "geofences": "#0891b2", "documents": "#475569"}
TAGLINE = "El compañero de tu coche"

# the colour of the section being written: KPI cards, table heads and charts take it when they are made
_tone: contextvars.ContextVar[str] = contextvars.ContextVar("car_book_tone", default="#2563eb")


def _tint(hex_colour: str, amount: float = 0.88) -> colors.Color:
    """The colour mixed with white (`amount` of white): table heads and card backgrounds."""
    c = colors.HexColor(hex_colour)
    return colors.Color(c.red + (1 - c.red) * amount, c.green + (1 - c.green) * amount, c.blue + (1 - c.blue) * amount)


_ss = getSampleStyleSheet()
H3 = ParagraphStyle("h3", parent=_ss["Heading3"], fontName="Helvetica-Bold", fontSize=11.5, leading=14, textColor=INK, spaceBefore=10, spaceAfter=5)
BODY = ParagraphStyle("body", parent=_ss["BodyText"], fontName="Helvetica", fontSize=9.5, leading=13.5, textColor=INK)
SMALL = ParagraphStyle("small", parent=BODY, fontSize=8, leading=10.5, textColor=MUTED)
KPI_V = ParagraphStyle("kpiv", parent=BODY, fontName="Helvetica-Bold", fontSize=14.5, leading=18, alignment=TA_LEFT)
KPI_L = ParagraphStyle("kpil", parent=SMALL, fontSize=7.5, leading=9.5, alignment=TA_LEFT)
CELL = ParagraphStyle("cell", parent=BODY, fontSize=8.5, leading=11)
CELL_B = ParagraphStyle("cellb", parent=CELL, fontName="Helvetica-Bold")
TOC_0 = ParagraphStyle("toc0", parent=BODY, fontName="Helvetica", fontSize=12, leading=16, leftIndent=0, firstLineIndent=0, spaceBefore=6, spaceAfter=6)
PAGE_TITLE = ParagraphStyle("pagetitle", parent=BODY, fontName="Helvetica-Bold", fontSize=22, leading=26, textColor=INK, spaceAfter=4)


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
class SectionHeader(Flowable):
    """The opener of a section: its number in the section's colour, the title and a rule; a PDF bookmark and a contents entry."""

    def __init__(self, number: int, title: str, key: str, width: float, space_before: float = 0):
        super().__init__()
        self.number, self.title, self.key, self.width, self.height = number, title, key, width, 18 * mm
        self.spaceBefore = space_before                   # dropped by reportlab at the top of a page: no gap there

    def wrap(self, avail_w, avail_h):
        return self.width, self.height

    def draw(self):
        c = self.canv
        tone = colors.HexColor(BAND[self.key])
        c.setFillColor(tone)
        c.setFont("Helvetica-Bold", 26)
        c.drawString(0, 6 * mm, f"{self.number:02d}")
        c.setFillColor(INK)
        c.setFont("Helvetica-Bold", 19)
        c.drawString(17 * mm, 6.6 * mm, self.title)
        c.setStrokeColor(tone)
        c.setLineWidth(2.2)
        c.line(0, 2 * mm, 30 * mm, 2 * mm)
        c.setStrokeColor(LINE)
        c.setLineWidth(0.6)
        c.line(30 * mm, 2 * mm, self.width, 2 * mm)
        c.bookmarkPage(self.key)
        c.addOutlineEntry(f"{self.number:02d}  {self.title}", self.key, level=0)


def _kpis(items: List[tuple], width: float) -> Table:
    """[(value, label), ...] as cards, up to four per row, each with a stripe of the section's colour on top (each card is a
    table of its own, so the stripes do not run into one another over the gutters)."""
    tone = _tone.get()
    gap = 2.4 * mm
    card_w = width / 4 - gap

    def card(value: str, label: str) -> Table:
        t = Table([[Paragraph(value, KPI_V)], [Paragraph(label, KPI_L)]], colWidths=[card_w])
        t.setStyle(TableStyle([("BACKGROUND", (0, 0), (-1, -1), _tint(tone, 0.93)), ("LINEABOVE", (0, 0), (-1, 0), 2.4, colors.HexColor(tone)),
                               ("LEFTPADDING", (0, 0), (-1, -1), 9), ("RIGHTPADDING", (0, 0), (-1, -1), 6),
                               ("TOPPADDING", (0, 0), (-1, 0), 8), ("BOTTOMPADDING", (0, 0), (-1, 0), 1),
                               ("TOPPADDING", (0, 1), (-1, 1), 0), ("BOTTOMPADDING", (0, 1), (-1, 1), 8)]))
        return t

    cards = [card(v, label) for v, label in items]
    rows = [cards[i:i + 4] + [""] * (4 - len(cards[i:i + 4])) for i in range(0, len(cards), 4)]
    grid = Table(rows, colWidths=[width / 4] * 4, hAlign="LEFT")
    grid.setStyle(TableStyle([("VALIGN", (0, 0), (-1, -1), "TOP"), ("LEFTPADDING", (0, 0), (-1, -1), 0), ("RIGHTPADDING", (0, 0), (-1, -1), gap),
                              ("TOPPADDING", (0, 0), (-1, -1), 0), ("BOTTOMPADDING", (0, 0), (-1, -1), gap)]))
    return grid


def _table(head: List[str], rows: List[List[Any]], widths: List[float], align_right: Iterable[int] = ()) -> Table:
    tone = _tone.get()
    data = [[_p(_(h), CELL_B) for h in head]] + [[c if isinstance(c, Paragraph) else _p(c) for c in r] for r in rows]
    t = Table(data, colWidths=widths, repeatRows=1, hAlign="LEFT")
    style = [("BACKGROUND", (0, 0), (-1, 0), _tint(tone, 0.86)), ("LINEBELOW", (0, 0), (-1, 0), 1.2, colors.HexColor(tone)),
             ("VALIGN", (0, 0), (-1, -1), "TOP"), ("TOPPADDING", (0, 0), (-1, -1), 4.5), ("BOTTOMPADDING", (0, 0), (-1, -1), 4.5),
             ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, ZEBRA]), ("LINEBELOW", (0, 1), (-1, -1), 0.4, LINE)]
    for i in align_right:
        style.append(("ALIGN", (i, 0), (i, -1), "RIGHT"))
    t.setStyle(TableStyle(style))
    return t


def _bars(values: Dict[str, float], unit: str, width: float, color: str, title: str) -> Drawing:
    """Bars per month (the last twelve) in the colour of the section being written."""
    keys = sorted(values)[-12:]
    d = Drawing(width, 158)
    chart = VerticalBarChart()
    chart.x, chart.y, chart.width, chart.height = 38, 24, width - 52, 108
    chart.data = [[round(values[k], 1) for k in keys]]
    chart.categoryAxis.categoryNames = [_month_label(k) for k in keys]
    chart.categoryAxis.labels.fontName, chart.categoryAxis.labels.fontSize = "Helvetica", 7
    chart.categoryAxis.labels.fillColor = MUTED
    chart.categoryAxis.strokeColor = LINE
    chart.valueAxis.labels.fontName, chart.valueAxis.labels.fontSize = "Helvetica", 7
    chart.valueAxis.labels.fillColor = MUTED
    chart.valueAxis.strokeColor = None
    chart.valueAxis.visibleGrid = 1
    chart.valueAxis.gridStrokeColor = LINE
    chart.valueAxis.gridStrokeWidth = 0.5
    chart.valueAxis.valueMin = 0
    chart.bars[0].fillColor = colors.HexColor(_tone.get() or color)
    chart.bars[0].strokeColor = None
    chart.useAbsolute = 1                                 # fixed bar width: two months do not become two slabs
    chart.barWidth = 16
    chart.groupSpacing = max(8.0, (chart.width - 16 * len(keys)) / max(1, len(keys)))
    d.add(chart)
    d.add(String(0, 146, f"{_(title)} ({unit})", fontName="Helvetica-Bold", fontSize=9, fillColor=INK))
    return d


def _kv(pairs: List[tuple], width: float) -> Table:
    rows = [[_p(_(k), CELL_B), _p(v)] for k, v in pairs]
    t = Table(rows, colWidths=[width * 0.36, width * 0.64], hAlign="LEFT")
    t.setStyle(TableStyle([("ROWBACKGROUNDS", (0, 0), (-1, -1), [colors.white, ZEBRA]), ("TOPPADDING", (0, 0), (-1, -1), 4),
                           ("BOTTOMPADDING", (0, 0), (-1, -1), 4), ("LINEBELOW", (0, 0), (-1, -1), 0.4, LINE),
                           ("LINEBEFORE", (0, 0), (0, -1), 2.2, colors.HexColor(_tone.get()))]))
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
        # Battery health measured from the charge curves, the same figures as Estadísticas: a certificate when selling the car
        data["battery_health"], data["battery_summary"] = await stats.measured_battery_health(session, vin, list(data["charges"]), get_timezone())
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
    return [_table(["Pieza", "Nombre", "Marca", "Referencias", "Detalles", "Montado"], rows,
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
    # Electric consumption only over fully electric drives (the petrol engine charges the battery on the way: services/stats.py)
    ev_drives = [x for x in drives if stats.counts_for_ev_consumption(x)]
    ev_drive_km = sum(x.distance_km or 0 for x in ev_drives)
    ev_drive_kwh = sum(x.energy_consumed_kwh or 0 for x in ev_drives)
    # Share of electric km since new: the two odometers of the latest drive that has both (drives come newest first)
    latest = next((x for x in drives if x.end_ev_odometer and x.end_odometer), None)
    lifetime_ev = (_n(latest.end_ev_odometer / latest.end_odometer * 100, 0, "%"), _("en eléctrico desde nuevo")) if latest else None
    out: List[Any] = [_kpis([(str(len(drives)), _("viajes")), (_n(km, 0, "km"), _("recorridos")), (_n(kwh, 1, "kWh"), _("eléctrico")),
                             (_n(litres, 1, "l"), _("gasolina")), (_dur(secs), _("al volante")),
                             (_n(km / len(drives), 1, "km"), _("por viaje")),
                             (_n(ev_drive_kwh / ev_drive_km * 100, 1, "kWh/100 km") if ev_drive_km >= stats.MIN_DRIVE_KM else "—", _("consumo eléctrico medio")),
                             (_n(max((x.speed_max_kmh or 0) for x in drives), 0, "km/h"), _("velocidad máxima"))] + ([lifetime_ev] if lifetime_ev else []), w),
                     Spacer(1, 6), _bars(months, "km", w, BAND["drives"], "Kilómetros por mes"), Spacer(1, 4),
                     Paragraph(_("Últimos viajes ({p0} de {p1})", p0=min(len(drives), 40), p1=len(drives)), H3)]
    out.append(_table(["Fecha", "Origen → destino", "Km", "Duración", "Batería", "Energía"],
                      [[_dt(x.start_time), f"{x.start_address or '—'} → {x.end_address or '—'}", _n(x.distance_km, 1), _dur(x.duration_seconds),
                        f"{_n(x.start_battery_pct, 0)} → {_n(x.end_battery_pct, 0)} %" if x.start_battery_pct is not None and x.end_battery_pct is not None else "—",
                        " · ".join(v for v in [_n(x.energy_consumed_kwh, 1, "kWh") if x.energy_consumed_kwh else "", _n(x.fuel_used_l, 1, "l") if x.fuel_used_l else ""] if v) or "—"]
                       for x in drives[:40]], [w * 0.14, w * 0.38, w * 0.08, w * 0.12, w * 0.14, w * 0.14], align_right=(2,)))
    return out


def _battery_health(d: Dict[str, Any], w: float) -> List[Any]:
    """Usable capacity measured from the charge curves, now and per month (services/stats.py, `measured_battery_health`)."""
    summary = d.get("battery_summary") or {}
    health = d.get("battery_health") or []
    out: List[Any] = [Paragraph(_("Salud de la batería"), H3)]
    if not summary.get("measured_kwh"):
        out.append(Paragraph(_("Todavía no hay cargas de al menos {p0} puntos con la curva completa para medir la capacidad.",
                               p0=int(battery_capacity.MIN_POINTS_GAINED)), SMALL))
        return out
    out.append(_kpis([(_n(summary["measured_kwh"], 1, "kWh"), _("útiles medidos")),
                      (_n(summary.get("nominal_kwh"), 1, "kWh") if summary.get("nominal_kwh") else "—", _("según la ficha")),
                      (_n(summary["pct_of_nominal"], 0, "%") if summary.get("pct_of_nominal") else "—", _("de la ficha")),
                      (str(summary.get("charges") or 0), _("cargas medidas"))], w))
    if len(health) > 1:
        out += [Spacer(1, 6), _bars({h["month"]: h["capacity_kwh"] for h in health}, "kWh", w, BAND["charges"], "Capacidad útil medida por mes")]
    how = _("Energía que entró en la batería (la curva de potencia de la carga) entre los puntos ganados, en cargas de al menos {p0} puntos; la cifra actual es la media de las últimas cargas medidas.", p0=int(battery_capacity.MIN_POINTS_GAINED))  # noqa: E501
    out += [Paragraph(how, SMALL), Spacer(1, 4)]
    return out


def _charges(d: Dict[str, Any], w: float) -> List[Any]:
    charges = d.get("charges") or []
    if not charges:
        return [Paragraph(_("Sin cargas registradas."), SMALL)]
    kwh = sum(energy_kwh(c) for c in charges)
    cost = sum(c.charge_cost or 0 for c in charges)
    months: Dict[str, float] = defaultdict(float)
    for c in charges:
        months[c.start_time.strftime("%Y-%m")] += energy_kwh(c)
    out: List[Any] = [_kpis([(str(len(charges)), _("cargas")), (_n(kwh, 1, "kWh"), _("a la batería")), (_eur(cost), _("gastado")),
                             (_n(cost / kwh, 3, "€/kWh") if kwh else "—", _("precio medio")),
                             (_n(max((c.max_charge_power_kw or 0) for c in charges), 1, "kW"), _("potencia máxima")),
                             (_dur(sum(c.duration_seconds or 0 for c in charges)), _("enchufado"))], w),
                     Spacer(1, 6), _bars(months, "kWh", w, BAND["charges"], "Energía cargada por mes"), Spacer(1, 4)]
    out += _battery_health(d, w)
    out.append(Paragraph(_("Últimas cargas ({p0} de {p1})", p0=min(len(charges), 40), p1=len(charges)), H3))
    out.append(_table(["Inicio", "Lugar", "Tipo", "Batería", "kWh", "Duración", "Coste"],
                      [[_dt(c.start_time), c.address or "—", "DC" if c.charge_type == "dc" else "AC",
                        f"{_n(c.start_battery_pct, 0)} → {_n(c.end_battery_pct, 0)} %" if c.start_battery_pct is not None and c.end_battery_pct is not None else "—",
                        _n(energy_kwh(c), 1), _dur(c.duration_seconds), _eur(c.charge_cost) if c.charge_cost else "—"] for c in charges[:40]],
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
                             (_n(s.get("avg_days_between_refuels"), 0, _("días")) if s.get("avg_days_between_refuels") else "—", _("días entre repostajes")),
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
PAGE_W, PAGE_H = A4
MARGIN = 16 * mm


def _static(*parts: str) -> Path:
    return Path(frontend_dir(), "static", *parts)


@functools.lru_cache(maxsize=1)
def _logo() -> Optional[ImageReader]:
    """EVbro's app icon (the same as the home screen's)."""
    path = _static("icons", "icon-512.png")
    return ImageReader(str(path)) if path.is_file() else None


@functools.lru_cache(maxsize=4)
def _car_render(model: str) -> Optional[bytes]:
    """The top view of the 3D model shown on Inicio, all parts closed, cropped: the cover's car when the user has no photo. Only on
    a server that has the pictures (they are not in the public repository)."""
    folder = _static("car", model)
    try:
        from PIL import Image as PILImage
        layout = json.loads((folder / "layout.json").read_text(encoding="utf-8"))
        img = PILImage.open(folder / "base.webp").convert("RGBA")
        for key in layout["layers"]:
            if key.endswith(".closed"):
                img.alpha_composite(PILImage.open(folder / f"{key}.webp").convert("RGBA"))
        img = img.crop(img.getbbox())
        out = io.BytesIO()
        img.save(out, format="PNG")
        return out.getvalue()
    except Exception:                                      # noqa: BLE001 — no pictures here: the cover goes without the car
        return None


COVER_PHOTO_PX = 1600          # longest side of the cover photo: a phone's photo as it comes made a 4 MB book (26-11-2026)


def _cover_photo(raw: bytes) -> bytes:
    """The user's photo ready for the cover: the right way up (phones store the rotation apart), at most COVER_PHOTO_PX on its
    longest side and as JPEG, on white where it was transparent."""
    from PIL import Image as PILImage
    from PIL import ImageOps

    img = ImageOps.exif_transpose(PILImage.open(io.BytesIO(raw)))
    img.thumbnail((COVER_PHOTO_PX, COVER_PHOTO_PX))
    if img.mode in ("RGBA", "LA", "P"):
        rgba = img.convert("RGBA")
        img = PILImage.new("RGB", rgba.size, "white")
        img.paste(rgba, mask=rgba.split()[-1])
    else:
        img = img.convert("RGB")
    out = io.BytesIO()
    img.save(out, format="JPEG", quality=85, optimize=True)
    return out.getvalue()


def _cover_picture(vehicle: Vehicle) -> Optional[tuple]:
    """(image reader, is the user's photo) for the cover: the car's photo if it has one, else the 3D model's top view."""
    if vehicle.photo_filename:
        try:
            return ImageReader(io.BytesIO(_cover_photo(uploads.load(uploads.PHOTOS_FOLDER, vehicle.photo_filename)))), True
        except Exception:                                  # noqa: BLE001 — a missing or unreadable photo never breaks the book
            pass
    if not vehicle.is_manual:
        raw = _car_render("s900")                          # the S900 pictures stand for the three models (as on Inicio)
        if raw:
            return ImageReader(io.BytesIO(raw)), False
    return None


def _wordmark(c, x: float, y: float, size: float, on_dark: bool) -> None:
    """The logo and «EVbro» beside it."""
    logo = _logo()
    if logo:
        c.drawImage(logo, x, y - size * 0.25, size * 1.5, size * 1.5, mask="auto")
    c.setFont("Helvetica-Bold", size)
    c.setFillColor(colors.white if on_dark else INK)
    c.drawString(x + size * 1.85, y + size * 0.12, "EVbro")


def _brand_rule(c, x: float, y: float, w: float, h: float) -> None:
    """The cyan → purple stripe of the logo."""
    c.saveState()
    p = c.beginPath()
    p.rect(x, y, w, h)
    c.clipPath(p, stroke=0, fill=0)
    c.linearGradient(x, y, x + w, y, BRAND, extend=False)
    c.restoreState()


def _plate(c, x: float, y: float, text: str) -> None:
    """A European number plate: blue strip with «E», white field, black letters."""
    w, h = 40 * mm, 9.5 * mm
    c.setFillColor(colors.white)
    c.setStrokeColor(colors.HexColor("#111827"))
    c.setLineWidth(1)
    c.roundRect(x, y, w, h, 1.8 * mm, stroke=1, fill=1)
    c.setFillColor(colors.HexColor("#1d4ed8"))
    c.roundRect(x + 0.6, y + 0.6, 6 * mm, h - 1.2, 1.4 * mm, stroke=0, fill=1)
    c.rect(x + 3 * mm, y + 0.6, 3.6 * mm, h - 1.2, stroke=0, fill=1)
    c.setFillColor(colors.HexColor("#facc15"))
    c.circle(x + 3.3 * mm, y + h - 3 * mm, 1.1 * mm, stroke=0, fill=1)
    c.setFillColor(colors.white)
    c.setFont("Helvetica-Bold", 7)
    c.drawCentredString(x + 3.3 * mm, y + 1.6 * mm, "E")
    c.setFillColor(colors.HexColor("#111827"))
    c.setFont("Helvetica-Bold", 15)
    c.drawCentredString(x + 6.5 * mm + (w - 6.5 * mm) / 2, y + 2.6 * mm, text.upper())


def _draw_cover(vehicle: Vehicle):
    model = f"{vehicle.brand or ''} {vehicle.model or ''}".strip() or "Ebro"
    now = datetime.datetime.now(get_timezone())
    key_data = [("Bastidor (VIN)", vehicle.chassis_number or vehicle.vin), ("Matriculación", _d(vehicle.registration_date)),
                ("Entrega", _d(vehicle.delivery_date)), ("Cuentakilómetros", _n(vehicle.odometer, 0, "km") if vehicle.odometer else "—"),
                ("Batería útil", _n(vehicle.battery_capacity_kwh, 2, "kWh") if vehicle.battery_capacity_kwh else "—"),
                ("Depósito", _n(vehicle.fuel_tank_liters, 0, "l") if vehicle.fuel_tank_liters else "—")]

    def draw(c, doc):
        c.saveState()
        panel_y = PAGE_H * 0.36                               # the dark panel covers the top 64 %
        c.linearGradient(0, PAGE_H, 0, panel_y, [NAVY, NAVY_2], extend=False)
        _brand_rule(c, 0, panel_y - 1.6 * mm, PAGE_W, 1.6 * mm)
        c.restoreState()
        c.saveState()
        p = c.beginPath()
        p.rect(0, panel_y, PAGE_W, PAGE_H - panel_y)
        c.clipPath(p, stroke=0, fill=0)
        # a faint glow of the brand colours behind the car
        for radius, alpha in ((110 * mm, 0.035), (85 * mm, 0.045), (60 * mm, 0.055)):     # rings that fade outwards, not one hard disc
            c.setFillColor(colors.Color(0.39, 0.4, 0.95, alpha=alpha))
            c.circle(PAGE_W * 0.72, panel_y + 62 * mm, radius, stroke=0, fill=1)
        c.restoreState()

        top = PAGE_H - 22 * mm
        _wordmark(c, MARGIN, top, 18, on_dark=True)
        c.setFont("Helvetica", 9)
        c.setFillColor(SOFT_ON_NAVY)
        c.drawRightString(PAGE_W - MARGIN, top + 2, _(TAGLINE))

        c.setFillColor(colors.white)
        c.setFont("Helvetica-Bold", 38)
        c.drawString(MARGIN, PAGE_H - 62 * mm, _("Libro del coche"))
        c.setFont("Helvetica", 16)
        c.setFillColor(SOFT_ON_NAVY)
        c.drawString(MARGIN, PAGE_H - 73 * mm, model + (f" · {vehicle.name}" if vehicle.name and vehicle.name not in model else ""))
        if vehicle.license_plate:
            _plate(c, MARGIN, PAGE_H - 89 * mm, vehicle.license_plate)

        picture = _cover_picture(vehicle)
        if picture:
            img, own_photo = picture
            iw, ih = img.getSize()
            box_w, box_h = PAGE_W - 2 * MARGIN, PAGE_H - 100 * mm - panel_y - 10 * mm
            ratio = min(box_w / iw, box_h / ih)
            w, h = iw * ratio, ih * ratio
            x, y = (PAGE_W - w) / 2, panel_y + 8 * mm + (box_h - h) / 2
            if own_photo:
                c.saveState()
                clip = c.beginPath()
                clip.roundRect(x, y, w, h, 4 * mm)
                c.clipPath(clip, stroke=0, fill=0)
                c.drawImage(img, x, y, w, h)
                c.restoreState()
            else:
                c.drawImage(img, x, y, w, h, mask="auto")

        # key data on the white part
        c.setFillColor(INK)
        c.setFont("Helvetica-Bold", 11)
        y0 = panel_y - 16 * mm
        c.drawString(MARGIN, y0, _("Datos clave"))
        col_w = (PAGE_W - 2 * MARGIN) / 3
        for i, (label, value) in enumerate(key_data):
            cx = MARGIN + (i % 3) * col_w
            cy = y0 - 13 * mm - (i // 3) * 17 * mm
            c.setStrokeColor(LINE)
            c.setLineWidth(0.6)
            c.line(cx, cy + 10 * mm, cx + col_w - 5 * mm, cy + 10 * mm)
            c.setFont("Helvetica", 7.5)
            c.setFillColor(MUTED)
            c.drawString(cx, cy + 6 * mm, _(label).upper())
            c.setFont("Helvetica-Bold", 11.5)
            c.setFillColor(INK)
            c.drawString(cx, cy + 0.5 * mm, str(value))

        # foot of the cover
        _brand_rule(c, MARGIN, 22 * mm, PAGE_W - 2 * MARGIN, 0.8)
        c.setFont("Helvetica", 8)
        c.setFillColor(MUTED)
        c.drawString(MARGIN, 16 * mm, _("Generado con EVbro el {p0} a las {p1}", p0=now.strftime("%d/%m/%Y"), p1=now.strftime("%H:%M")))
        c.setFont("Helvetica-Bold", 8)
        c.setFillColor(colors.HexColor("#4f46e5"))
        c.drawRightString(PAGE_W - MARGIN, 16 * mm, "evbro.app")
    return draw


def _draw_page(vehicle: Vehicle):
    label = " · ".join(x for x in [_("Libro del coche"), vehicle.name or vehicle.model or "Ebro", vehicle.license_plate] if x)

    def draw(c, doc):
        c.saveState()
        top = PAGE_H - 11 * mm
        _wordmark(c, MARGIN, top - 1, 9, on_dark=False)
        c.setFont("Helvetica", 7.5)
        c.setFillColor(MUTED)
        c.drawRightString(PAGE_W - MARGIN, top, label)
        _brand_rule(c, MARGIN, top - 4 * mm, PAGE_W - 2 * MARGIN, 0.9)
        c.setStrokeColor(LINE)
        c.setLineWidth(0.5)
        c.line(MARGIN, 14 * mm, PAGE_W - MARGIN, 14 * mm)
        c.setFont("Helvetica", 7.5)
        c.setFillColor(MUTED)
        c.drawString(MARGIN, 9.5 * mm, _("Generado con EVbro · evbro.app"))
        c.restoreState()
    return draw


class _NumberedCanvas(rl_canvas.Canvas):
    """Writes «Página X de Y» once the total is known (every page but the cover)."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._pages: List[dict] = []

    def showPage(self):
        self._pages.append(dict(self.__dict__))
        self._startPage()

    def save(self):
        total = len(self._pages)
        for state in self._pages:
            self.__dict__.update(state)
            if self._pageNumber > 1:
                self.setFont("Helvetica", 7.5)
                self.setFillColor(MUTED)
                self.drawRightString(PAGE_W - MARGIN, 9.5 * mm, _("Página {p0} de {p1}", p0=self._pageNumber, p1=total))
            super().showPage()
        super().save()


class _Book(BaseDocTemplate):
    def afterFlowable(self, flowable):
        if isinstance(flowable, SectionHeader):
            self.notify("TOCEntry", (0, f"{flowable.number:02d}&nbsp;&nbsp;&nbsp;{flowable.title}", self.page, flowable.key))


def _contents(chosen: List[str]) -> List[Any]:
    toc = TableOfContents()
    toc.levelStyles = [TOC_0]
    toc.dotsMinLevel = 0
    return [Paragraph(_("Índice"), PAGE_TITLE), Spacer(1, 2 * mm), toc, Spacer(1, 10 * mm),
            Paragraph(_("Los consumos, kWh y litros de los viajes y las cargas son estimaciones a partir de lo que informa el coche; los repostajes detectados llevan litros estimados salvo que se corrigieran con el ticket."), SMALL)]


async def build(session: AsyncSession, vehicle: Vehicle, sections: Iterable[str]) -> bytes:
    chosen = [s for s in SECTIONS if s in set(sections)] or list(SECTIONS)
    data = await collect(session, vehicle, chosen)
    buf = io.BytesIO()
    doc = _Book(buf, pagesize=A4, leftMargin=MARGIN, rightMargin=MARGIN, topMargin=24 * mm, bottomMargin=20 * mm,
                title=f"{_('Libro del coche')} · {vehicle.name or vehicle.vin}", author="EVbro", creator="EVbro · evbro.app",
                subject=f"{vehicle.brand or ''} {vehicle.model or ''}".strip())
    width = PAGE_W - 2 * MARGIN
    body_frame = Frame(MARGIN, doc.bottomMargin, width, PAGE_H - doc.topMargin - doc.bottomMargin, id="body", leftPadding=0, rightPadding=0)
    cover_frame = Frame(0, 0, PAGE_W, PAGE_H, id="cover")
    doc.addPageTemplates([PageTemplate(id="cover", frames=[cover_frame], onPage=_draw_cover(vehicle)),
                          PageTemplate(id="body", frames=[body_frame], onPage=_draw_page(vehicle))])
    story: List[Any] = [Spacer(1, 1), NextPageTemplate("body"), PageBreak()]
    story += _contents(chosen)
    story.append(PageBreak())
    for i, key in enumerate(chosen):
        token = _tone.set(BAND[key])
        try:
            if i:
                story.append(CondPageBreak(110 * mm))        # a section starts on a new page unless there is plenty of room left
            story.append(SectionHeader(i + 1, _(SECTION_TITLES[key]), key, width, space_before=10 * mm if i else 0))
            story.append(Spacer(1, 3 * mm))
            story.extend(_sheet(vehicle, width) if key == "sheet" else RENDERERS[key](data, width))
        finally:
            _tone.reset(token)
    doc.multiBuild(story, canvasmaker=_NumberedCanvas)
    return buf.getvalue()
