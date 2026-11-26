"""The car book: a PDF with the sections the user picks (Coches → ficha → «Libro del coche»)."""
from __future__ import annotations

import datetime
from typing import List

from fastapi import APIRouter, Depends, HTTPException, Query
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


async def _book(session: AsyncSession, vin: str, sections: List[str], attachment: bool) -> Response:
    vehicle = (await session.execute(select(Vehicle).where(Vehicle.vin == vin))).scalar_one_or_none()
    if vehicle is None:
        raise HTTPException(status_code=404, detail="Vehículo no encontrado.")
    unknown = [s for s in sections if s not in car_book.SECTIONS]
    if unknown:
        raise HTTPException(status_code=422, detail=_("Secciones desconocidas: {p0}", p0=', '.join(unknown)))
    pdf = await car_book.build(session, vehicle, sections)
    name = f"libro-{(vehicle.license_plate or vehicle.name or vin).replace(' ', '-')}-{datetime.date.today().isoformat()}.pdf"
    return Response(content=pdf, media_type="application/pdf", headers={"Content-Disposition": uploads.content_disposition(name, attachment)})


@router.get("/vehicles/{vin}/book")
async def car_book_download(vin: str, sections: str = Query(default="", max_length=400), session: AsyncSession = Depends(get_db)):
    """The book as a file the browser saves by itself (`Content-Disposition: attachment`): what the app's button opens. Building
    the PDF in the page and saving it from memory (a `blob:` link) left a black screen on some phone browsers (Firefox for Android,
    26-11-2026); a plain download is handled by every browser's own download manager. `sections` is a comma-separated list; empty = all."""
    chosen = [s for s in (part.strip() for part in sections.split(",")) if s] or list(car_book.SECTIONS)
    return await _book(session, vin, chosen[:20], attachment=True)


@router.post("/vehicles/{vin}/book")
async def car_book_pdf(vin: str, req: BookRequest, session: AsyncSession = Depends(get_db)):
    """The same book answered to a request made by code (kept for pages loaded before the button changed, and for scripts)."""
    return await _book(session, vin, req.sections, attachment=False)
