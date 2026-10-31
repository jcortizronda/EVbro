"""Documents kept with a car: purchase invoice, insurance policy, registration certificate, technical sheet and others.

Safety (the app may be on the internet): see `services/uploads.py` (type from the content, size checked while reading, random names,
encrypted at rest, safe headers). Only PDF, JPEG, PNG and WebP up to 15 MB; each upload counts towards the owner's storage quota.
Who may do what is decided in `security/access.py` (listing and downloading need `view`; uploading and deleting are the owner's).
"""
from __future__ import annotations

import datetime
from typing import List, Optional

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import AppUser, Vehicle, VehicleDocument
from app.db.session import get_db
from app.i18n import _
from app.security import auth
from app.services import storage, uploads

router = APIRouter(tags=["Documents"])

DOCUMENTS_FOLDER = uploads.DOCUMENTS_FOLDER
DOCUMENT_KINDS = ("purchase_invoice", "insurance_policy", "registration", "technical_sheet", "other")
MAX_DOCUMENT_BYTES = 15 * 1024 * 1024
MAX_DOCUMENTS_PER_VEHICLE = 50
MAX_TITLE_LENGTH = 120


class DocumentOut(BaseModel):
    id: int
    vin: str
    kind: str
    title: Optional[str] = None
    original_name: str
    content_type: str
    size: int
    uploaded_at: datetime.datetime
    url: str


def to_out(doc: VehicleDocument) -> DocumentOut:
    return DocumentOut(id=doc.id, vin=doc.vin, kind=doc.kind, title=doc.title, original_name=doc.original_name, content_type=doc.content_type,
                       size=doc.size, uploaded_at=doc.uploaded_at, url=f"/api/documents/{doc.id}")


@router.get("/vehicles/{vin}/documents", response_model=List[DocumentOut])
async def list_documents(vin: str, session: AsyncSession = Depends(get_db)):
    """The car's documents, newest first (without their contents)."""
    rows = (await session.execute(select(VehicleDocument).where(VehicleDocument.vin == vin)
                                  .order_by(VehicleDocument.uploaded_at.desc(), VehicleDocument.id.desc()))).scalars().all()
    return [to_out(d) for d in rows]


@router.post("/vehicles/{vin}/documents", response_model=DocumentOut)
async def upload_document(vin: str, file: UploadFile = File(...), kind: str = Form(...), title: Optional[str] = Form(None),
                          session: AsyncSession = Depends(get_db), user: AppUser = Depends(auth.require_auth)):
    """Adds a document to the car (owner only). PDF, JPG, PNG or WebP, up to 15 MB."""
    if (await session.execute(select(Vehicle.id).where(Vehicle.vin == vin))).scalar_one_or_none() is None:
        raise HTTPException(status_code=404, detail="Vehículo no encontrado")
    if kind not in DOCUMENT_KINDS:
        raise HTTPException(status_code=422, detail="Tipo de documento no válido.")
    title = (title or "").strip() or None
    if title and len(title) > MAX_TITLE_LENGTH:
        raise HTTPException(status_code=422, detail=_("El título no puede pasar de {p0} caracteres.", p0=MAX_TITLE_LENGTH))
    count = (await session.execute(select(func.count()).select_from(VehicleDocument).where(VehicleDocument.vin == vin))).scalar_one()
    if count >= MAX_DOCUMENTS_PER_VEHICLE:
        raise HTTPException(status_code=409, detail=_("Este coche ya tiene {p0} documentos: borra alguno antes de añadir más.", p0=MAX_DOCUMENTS_PER_VEHICLE))

    data = await uploads.read_limited(file, MAX_DOCUMENT_BYTES, "El documento pesa más de 15 MB.")
    sniffed = uploads.sniff_type(data)
    if sniffed is None:
        raise HTTPException(status_code=415, detail="Solo se admiten documentos PDF o imágenes JPG, PNG o WebP.")
    content_type, extension = sniffed
    await storage.check_quota(session, await storage.owner_of_vehicle(session, vin), len(data))

    stored = uploads.store(DOCUMENTS_FOLDER, data, extension)
    doc = VehicleDocument(vin=vin, kind=kind, title=title, filename=stored, original_name=uploads.clean_original_name(file.filename, extension),
                          content_type=content_type, size=len(data), uploaded_at=datetime.datetime.utcnow(), uploaded_by=user.id)
    session.add(doc)
    try:
        await session.commit()
    except Exception:
        uploads.remove(DOCUMENTS_FOLDER, stored)
        raise
    await session.refresh(doc)
    return to_out(doc)


@router.get("/documents/{document_id}")
async def get_document(document_id: int, session: AsyncSession = Depends(get_db)):
    """The file, inline (the browser shows a PDF or an image in a new tab)."""
    doc = await session.get(VehicleDocument, document_id)
    if doc is None:
        raise HTTPException(status_code=404, detail="Documento no encontrado.")
    data = uploads.load(DOCUMENTS_FOLDER, doc.filename, missing="El fichero del documento no está en el servidor.")
    return uploads.file_response(data, doc.content_type, doc.original_name)


@router.delete("/documents/{document_id}")
async def delete_document(document_id: int, session: AsyncSession = Depends(get_db)):
    """Deletes the document and its file (owner only)."""
    doc = await session.get(VehicleDocument, document_id)
    if doc is None:
        raise HTTPException(status_code=404, detail="Documento no encontrado.")
    stored = doc.filename
    await session.delete(doc)
    await session.commit()
    uploads.remove(DOCUMENTS_FOLDER, stored)
    return {"ok": True}
