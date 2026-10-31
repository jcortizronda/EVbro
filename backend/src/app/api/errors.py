"""Error responses shared by the app and the admin console.

A request body that fails validation must answer 422, never 500. FastAPI's default handler echoes the offending `input` back, and
when that input is raw bytes (a multipart or binary body sent to a JSON route) the JSON encoding of the response itself fails and the
client gets a 500. The handler below drops `input` altogether: the client does not need its own body back, and a response never
mirrors what was sent.
"""
from __future__ import annotations

from fastapi import FastAPI, Request
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from app import i18n

_KEEP = ("type", "loc", "msg", "ctx")


def validation_error_response(exc: RequestValidationError) -> JSONResponse:
    errors = [{k: e[k] for k in _KEEP if k in e} for e in exc.errors()]
    return JSONResponse(status_code=422, content={"detail": jsonable_encoder(errors)})


def install_error_handlers(app: FastAPI) -> None:
    @app.exception_handler(RequestValidationError)
    async def _validation(request: Request, exc: RequestValidationError) -> JSONResponse:
        return validation_error_response(exc)

    @app.exception_handler(StarletteHTTPException)
    async def _http(request: Request, exc: StarletteHTTPException) -> JSONResponse:
        """Same answer as FastAPI's default handler, with a text `detail` translated to the language of the request
        (app.i18n): the routes keep raising their Spanish texts and the dictionaries do the rest."""
        detail = i18n.translate(exc.detail) if isinstance(exc.detail, str) else exc.detail
        return JSONResponse(status_code=exc.status_code, content={"detail": detail}, headers=getattr(exc, "headers", None))
