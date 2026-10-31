"""Export everything a manager's account holds as a ZIP, and import such a ZIP into an account (another server, or after a wipe).

The ZIP has `evbro-export.json` (format `evbro-account/1`) and `files/…` with the decrypted documents, invoices and photos.
Rows are exported column by column (SQLAlchemy inspection), so nothing is forgotten when a table grows; secrets never travel:
no Ebro credentials, no API tokens, no notification channels, no passkeys. Importing creates **manual cars** (no Ebro account
attached; connecting the account later links them by VIN) and skips VINs that already exist on the server, remapping every
internal id (drives, charges, geofences, maintenance items...) to the new ones.
"""
from __future__ import annotations

import datetime
import io
import json
import logging
import zipfile
from typing import Any, Dict, List, Optional, Tuple

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import (
    ChargePoint,
    ChargingSession,
    ClimateSchedule,
    Drive,
    DrivePosition,
    Expense,
    Geofence,
    GeofenceEvent,
    GpsPosition,
    MaintenanceItem,
    MaintenanceLog,
    MaintenancePlan,
    Refuel,
    TraccarLink,
    Vehicle,
    VehicleDocument,
    VehiclePart,
)
from app.services import uploads

_LOGGER = logging.getLogger(__name__)
FORMAT = "evbro-account/1"
EXPORT_FILE = "evbro-export.json"
MAX_IMPORT_BYTES = 500 * 1024 * 1024
MAX_FILE_BYTES = 20 * 1024 * 1024
VEHICLE_SKIP = {"id", "owner_id", "photo_filename", "photo_size", "state", "last_seen"}
TRACCAR_SKIP = {"id", "vin", "api_token", "last_error"}


def row(obj, skip: Tuple[str, ...] = ("id",)) -> Dict[str, Any]:
    out = {}
    for c in obj.__table__.columns:
        if c.name in skip:
            continue
        v = getattr(obj, c.name)
        out[c.name] = v.isoformat() if isinstance(v, (datetime.datetime, datetime.date)) else v
    return out


def _parse(model, data: Dict[str, Any], skip: Tuple[str, ...] = ("id",)) -> Dict[str, Any]:
    """Keep only real columns, parse ISO dates back. Unknown keys (from a newer export) are ignored."""
    out = {}
    for c in model.__table__.columns:
        if c.name in skip or c.name not in data:
            continue
        v = data[c.name]
        if v is not None and isinstance(v, str) and c.type.python_type in (datetime.datetime, datetime.date):
            try:
                v = datetime.datetime.fromisoformat(v) if c.type.python_type is datetime.datetime else datetime.date.fromisoformat(v)
            except ValueError:
                v = None
        out[c.name] = v
    return out


# ───────────────────────── export ─────────────────────────
async def export_account(session: AsyncSession, user_id: int, username: str) -> bytes:
    vehicles = (await session.execute(select(Vehicle).where(Vehicle.owner_id == user_id).order_by(Vehicle.id))).scalars().all()
    geofences = (await session.execute(select(Geofence).where(Geofence.owner_id == user_id))).scalars().all()
    buf = io.BytesIO()
    missing: List[str] = []
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        def put(folder: str, stored: Optional[str], zip_name: str) -> Optional[str]:
            if not stored:
                return None
            try:
                zf.writestr(zip_name, uploads.load(folder, stored))
                return zip_name
            except Exception:                              # noqa: BLE001 — a lost file is noted, the export goes on
                missing.append(zip_name)
                return None

        out_vehicles = []
        for v in vehicles:
            vin = v.vin
            items = (await session.execute(select(MaintenanceItem).where(MaintenanceItem.vin == vin))).scalars().all()
            item_ids = [i.id for i in items]
            logs = (await session.execute(select(MaintenanceLog).where(MaintenanceLog.item_id.in_(item_ids)))).scalars().all() if item_ids else []
            drives = (await session.execute(select(Drive).where(Drive.vin == vin).order_by(Drive.start_time))).scalars().all()
            drive_ids = [d.id for d in drives]
            positions = (await session.execute(select(DrivePosition).where(DrivePosition.drive_id.in_(drive_ids)))).scalars().all() if drive_ids else []
            charges = (await session.execute(select(ChargingSession).where(ChargingSession.vin == vin).order_by(ChargingSession.start_time))).scalars().all()
            charge_ids = [c.id for c in charges]
            points = (await session.execute(select(ChargePoint).where(ChargePoint.charging_session_id.in_(charge_ids)))).scalars().all() if charge_ids else []
            docs = (await session.execute(select(VehicleDocument).where(VehicleDocument.vin == vin))).scalars().all()
            plan = await session.get(MaintenancePlan, vin)
            traccar = (await session.execute(select(TraccarLink).where(TraccarLink.vin == vin))).scalar_one_or_none()
            out_vehicles.append({
                "vehicle": row(v, tuple(VEHICLE_SKIP)),
                "photo": put(uploads.PHOTOS_FOLDER, v.photo_filename, f"files/photos/{vin}.jpg"),
                "maintenance_plan": row(plan, ("vin",)) if plan else None,
                "maintenance_items": [{**row(i, ("vin",)), "id": i.id} for i in items],
                "maintenance_logs": [{**row(log, ("invoice_filename",)), "invoice_file": put(uploads.INVOICES_FOLDER, log.invoice_filename, f"files/invoices/{vin}/{log.id}")} for log in logs],
                "parts": [row(p, ("id", "vin", "catalog_id")) for p in (await session.execute(select(VehiclePart).where(VehiclePart.vin == vin))).scalars()],
                "drives": [{**row(d, ("vin",)), "id": d.id} for d in drives],
                "drive_positions": [row(p) for p in positions],
                "charges": [{**row(c, ("vin",)), "id": c.id} for c in charges],
                "charge_points": [row(p) for p in points],
                "refuels": [row(r, ("id", "vin")) for r in (await session.execute(select(Refuel).where(Refuel.vin == vin))).scalars()],
                "expenses": [row(e, ("id", "vin")) for e in (await session.execute(select(Expense).where(Expense.vin == vin))).scalars()],
                "geofence_events": [row(e, ("id", "vin")) for e in (await session.execute(select(GeofenceEvent).where(GeofenceEvent.vin == vin))).scalars()],
                "gps_positions": [row(p, ("id", "vin")) for p in (await session.execute(select(GpsPosition).where(GpsPosition.vin == vin))).scalars()],
                "climate_schedules": [row(s, ("id", "vin")) for s in (await session.execute(select(ClimateSchedule).where(ClimateSchedule.vin == vin))).scalars()],
                "traccar": row(traccar, tuple(TRACCAR_SKIP)) if traccar else None,
                "documents": [{**row(d, ("id", "vin", "filename")), "file": put(uploads.DOCUMENTS_FOLDER, d.filename, f"files/documents/{vin}/{d.id}")} for d in docs],
            })
        payload = {"format": FORMAT, "exported_at": datetime.datetime.utcnow().isoformat() + "Z", "username": username,
                   "geofences": [{**row(g, ("owner_id",)), "id": g.id} for g in geofences], "vehicles": out_vehicles,
                   "missing_files": missing,
                   "note": "Sin secretos: ni credenciales de Ebro, ni tokens, ni canales de aviso. Los coches se importan como coches sin cuenta."}
        zf.writestr(EXPORT_FILE, json.dumps(payload, ensure_ascii=False, indent=1))
    _LOGGER.info("Exportación de la cuenta %s: %d coches, %d ficheros perdidos", username, len(vehicles), len(missing))
    return buf.getvalue()


# ───────────────────────── import ─────────────────────────
class ImportError_(Exception):
    def __init__(self, status_code: int, detail: str):
        super().__init__(detail)
        self.status_code, self.detail = status_code, detail


async def import_account(session: AsyncSession, user_id: int, blob: bytes, vehicle_room: Optional[int]) -> Dict[str, Any]:
    """`vehicle_room`: how many more cars the account may hold (None = unlimited). Returns a report of what was imported/skipped."""
    if len(blob) > MAX_IMPORT_BYTES:
        raise ImportError_(413, "El fichero es demasiado grande.")
    try:
        zf = zipfile.ZipFile(io.BytesIO(blob))
        payload = json.loads(zf.read(EXPORT_FILE))
    except (zipfile.BadZipFile, KeyError, ValueError) as ex:
        raise ImportError_(422, "No es una exportación de EVBro (falta evbro-export.json).") from ex
    if payload.get("format") != FORMAT:
        raise ImportError_(422, f"Formato no reconocido: {payload.get('format')!r}.")
    names = set(zf.namelist())

    def read(name: Optional[str]) -> Optional[bytes]:
        if not name or name not in names or zf.getinfo(name).file_size > MAX_FILE_BYTES:
            return None
        return zf.read(name)

    report: Dict[str, Any] = {"vehicles": [], "skipped": [], "geofences": 0, "files": 0, "errors": []}
    # geofences: by name within the account
    existing = {g.name.lower(): g.id for g in (await session.execute(select(Geofence).where(Geofence.owner_id == user_id))).scalars()}
    geofence_map: Dict[int, int] = {}
    for g in payload.get("geofences", []):
        old_id = g.get("id")
        name = (g.get("name") or "").strip()
        if not name:
            continue
        if name.lower() in existing:
            geofence_map[old_id] = existing[name.lower()]
            continue
        obj = Geofence(owner_id=user_id, **_parse(Geofence, g, ("id", "owner_id")))
        session.add(obj)
        await session.flush()
        existing[name.lower()] = obj.id
        geofence_map[old_id] = obj.id
        report["geofences"] += 1

    known_vins = {v for (v,) in (await session.execute(select(Vehicle.vin))).all()}
    room = vehicle_room
    for entry in payload.get("vehicles", []):
        vdata = entry.get("vehicle") or {}
        vin = (vdata.get("vin") or "").strip()
        if not vin:
            continue
        if vin in known_vins:
            report["skipped"].append({"vin": vin, "reason": "Ya existe un coche con ese bastidor en este servidor."})
            continue
        if room is not None and room <= 0:
            report["skipped"].append({"vin": vin, "reason": "La cuenta no admite más coches."})
            continue
        v = Vehicle(owner_id=user_id, is_manual=True, **{k: val for k, val in _parse(Vehicle, vdata, tuple(VEHICLE_SKIP)).items() if k != "is_manual"})
        session.add(v)
        await session.flush()
        known_vins.add(vin)
        if room is not None:
            room -= 1
        counts = {"drives": 0, "charges": 0, "refuels": 0, "expenses": 0, "maintenance": 0, "parts": 0, "documents": 0}
        photo = read(entry.get("photo"))
        if photo:
            v.photo_filename = uploads.store(uploads.PHOTOS_FOLDER, photo, ".jpg")
            v.photo_size = len(photo)
            report["files"] += 1
        if entry.get("maintenance_plan"):
            session.add(MaintenancePlan(vin=vin, **_parse(MaintenancePlan, entry["maintenance_plan"], ("vin",))))
        item_map: Dict[int, int] = {}
        for i in entry.get("maintenance_items", []):
            obj = MaintenanceItem(vin=vin, **_parse(MaintenanceItem, i, ("id", "vin")))
            session.add(obj)
            await session.flush()
            item_map[i.get("id")] = obj.id
        for log in entry.get("maintenance_logs", []):
            if log.get("item_id") not in item_map:
                continue
            fields = _parse(MaintenanceLog, log, ("id", "item_id", "invoice_filename"))
            obj = MaintenanceLog(item_id=item_map[log["item_id"]], **fields)
            data = read(log.get("invoice_file"))
            if data and log.get("invoice_content_type"):
                ext = {"application/pdf": ".pdf", "image/png": ".png", "image/jpeg": ".jpg", "image/webp": ".webp"}.get(log["invoice_content_type"], ".bin")
                obj.invoice_filename = uploads.store(uploads.INVOICES_FOLDER, data, ext)
                report["files"] += 1
            session.add(obj)
            counts["maintenance"] += 1
        for p in entry.get("parts", []):
            session.add(VehiclePart(vin=vin, **_parse(VehiclePart, p, ("id", "vin", "catalog_id"))))
            counts["parts"] += 1
        drive_map: Dict[int, int] = {}
        for d in entry.get("drives", []):
            fields = _parse(Drive, d, ("id", "vin"))
            fields["start_geofence_id"] = geofence_map.get(fields.get("start_geofence_id"))
            fields["end_geofence_id"] = geofence_map.get(fields.get("end_geofence_id"))
            obj = Drive(vin=vin, **fields)
            session.add(obj)
            await session.flush()
            drive_map[d.get("id")] = obj.id
            counts["drives"] += 1
        session.add_all([DrivePosition(**{**_parse(DrivePosition, p, ("id", "drive_id")), "drive_id": drive_map[p["drive_id"]]})
                         for p in entry.get("drive_positions", []) if p.get("drive_id") in drive_map])
        charge_map: Dict[int, int] = {}
        for c in entry.get("charges", []):
            fields = _parse(ChargingSession, c, ("id", "vin"))
            fields["geofence_id"] = geofence_map.get(fields.get("geofence_id"))
            obj = ChargingSession(vin=vin, **fields)
            session.add(obj)
            await session.flush()
            charge_map[c.get("id")] = obj.id
            counts["charges"] += 1
        session.add_all([ChargePoint(**{**_parse(ChargePoint, p, ("id", "charging_session_id")), "charging_session_id": charge_map[p["charging_session_id"]]})
                         for p in entry.get("charge_points", []) if p.get("charging_session_id") in charge_map])
        for r in entry.get("refuels", []):
            session.add(Refuel(vin=vin, **_parse(Refuel, r, ("id", "vin"))))
            counts["refuels"] += 1
        for e in entry.get("expenses", []):
            session.add(Expense(vin=vin, **_parse(Expense, e, ("id", "vin"))))
            counts["expenses"] += 1
        for e in entry.get("geofence_events", []):
            fields = _parse(GeofenceEvent, e, ("id", "vin"))
            fields["geofence_id"] = geofence_map.get(fields.get("geofence_id"))
            session.add(GeofenceEvent(vin=vin, **fields))
        session.add_all([GpsPosition(vin=vin, **_parse(GpsPosition, p, ("id", "vin"))) for p in entry.get("gps_positions", [])])
        session.add_all([ClimateSchedule(vin=vin, **_parse(ClimateSchedule, s, ("id", "vin"))) for s in entry.get("climate_schedules", [])])
        for d in entry.get("documents", []):
            data = read(d.get("file"))
            if not data:
                continue
            ext = {"application/pdf": ".pdf", "image/png": ".png", "image/jpeg": ".jpg", "image/webp": ".webp"}.get(d.get("content_type"), ".bin")
            fields = _parse(VehicleDocument, d, ("id", "vin", "filename"))
            fields["size"] = len(data)
            session.add(VehicleDocument(vin=vin, filename=uploads.store(uploads.DOCUMENTS_FOLDER, data, ext), **fields))
            counts["documents"] += 1
            report["files"] += 1
        report["vehicles"].append({"vin": vin, "name": v.name, **counts})
    await session.commit()
    return report
