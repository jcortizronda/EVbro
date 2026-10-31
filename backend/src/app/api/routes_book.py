"""El libro del coche: a PDF with the sections the user picks (Coches → ficha → «Libro del coche»)."""
from __future__ import annotations

import datetime
from typing import List

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import Response
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Vehicle
from app.db.session import get_db
from app.i18n import _
from app.services import car_book, uploads

router = APIRouter(tags=["Car book"])


class BookRequest(BaseModel):
    sections: List[str] = Field(default_factory=lambda: list(car_book.SECTIONS), max_length=20)


@router.post("/vehicles/{vin}/book")
async def car_book_pdf(vin: str, req: BookRequest, session: AsyncSession = Depends(get_db)):
    vehicle = (await session.execute(select(Vehicle).where(Vehicle.vin == vin))).scalar_one_or_none()
    if vehicle is None:
        raise HTTPException(status_code=404, detail="Vehículo no encontrado.")
    unknown = [s for s in req.sections if s not in car_book.SECTIONS]
    if unknown:
        raise HTTPException(status_code=422, detail=_("Secciones desconocidas: {p0}", p0=', '.join(unknown)))
    pdf = await car_book.build(session, vehicle, req.sections)
    name = f"libro-{(vehicle.license_plate or vehicle.name or vin).replace(' ', '-')}-{datetime.date.today().isoformat()}.pdf"
    return Response(content=pdf, media_type="application/pdf", headers={"Content-Disposition": uploads.content_disposition(name)})
