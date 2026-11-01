"""Admin console: a second web application, on its own port, for the server administrator.

It is a different door from the app on purpose: its own port (published only on the local network, never behind the public proxy),
its own cookie, its own interface (made for a computer) and only the administrator's routes. The administrator owns no cars: here they
manage manager accounts, invitations, limits and the server's settings, and see counts and health but never a manager's data.
"""
from __future__ import annotations

import asyncio
import logging
import os
from typing import Optional

import uvicorn
from fastapi import APIRouter, Depends, FastAPI, Request
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles

from app import i18n
from app.api.errors import install_error_handlers
from app.api.routes_admin import router as admin_router
from app.api.routes_admin_auth import router as admin_auth_router
from app.api.routes_server import router as server_router
from app.api.routes_settings import router as settings_router
from app.config import settings
from app.paths import frontend_dir
from app.security.auth import require_admin, verify_origin

_LOGGER = logging.getLogger("app.admin")


def create_admin_app() -> FastAPI:
    app = FastAPI(title="EVBro Admin", docs_url=None, redoc_url=None, openapi_url=None)
    install_error_handlers(app)

    @app.middleware("http")
    async def headers(request: Request, call_next):
        i18n.set_language(i18n.pick(request.cookies, request.headers.get("accept-language")))     # same cookie as the app
        response = await call_next(request)
        response.headers.setdefault("X-Content-Type-Options", "nosniff")
        response.headers.setdefault("X-Frame-Options", "DENY")
        response.headers.setdefault("Referrer-Policy", "no-referrer")
        response.headers.setdefault("Content-Security-Policy", "default-src 'self'; img-src 'self' data:; style-src 'self'; script-src 'self'; frame-ancestors 'none'")
        if request.url.path.startswith("/api/"):
            response.headers["Cache-Control"] = "no-store"
        return response

    api = APIRouter(prefix="/api")
    api.include_router(admin_auth_router, dependencies=[Depends(verify_origin)])
    protected = APIRouter(dependencies=[Depends(verify_origin), Depends(require_admin)])
    protected.include_router(admin_router)
    protected.include_router(settings_router)
    protected.include_router(server_router)
    api.include_router(protected)
    app.include_router(api)

    root = os.path.join(frontend_dir(), "admin")
    if os.path.isdir(os.path.join(root, "assets")):
        app.mount("/assets", StaticFiles(directory=os.path.join(root, "assets")), name="admin-assets")
    static_dir = os.path.join(frontend_dir(), "static")
    if os.path.isdir(static_dir):
        app.mount("/static", StaticFiles(directory=static_dir), name="static")       # the console reuses core/i18n.js and the icons

    @app.get("/", include_in_schema=False)
    async def index():
        """The console page in the language of the request: `<html lang>` and the web dictionary inlined, like the app (docs/i18n.md)."""
        lang = i18n.get_language()
        with open(os.path.join(root, "index.html"), encoding="utf-8") as fh:
            html = fh.read()
        html = html.replace('<html lang="es">', f'<html lang="{lang}">', 1).replace(
            "</head>", f'  <script type="application/json" id="i18nData">{i18n.page_dictionary_json(lang)}</script>\n'
                       '  <script src="/static/js/core/i18n.js"></script>\n</head>', 1)
        return HTMLResponse(html, headers={"Cache-Control": "no-store"})

    return app


admin_app = create_admin_app()


class AdminConsole:
    """Runs the admin app inside this process (same database, same event loop) on `ADMIN_BIND:ADMIN_PORT`."""

    def __init__(self) -> None:
        self._server: Optional[uvicorn.Server] = None
        self._task: Optional[asyncio.Task] = None

    async def start(self) -> None:
        if not settings.ADMIN_PORT:
            _LOGGER.info("Admin console disabled (ADMIN_PORT=0).")
            return
        config = uvicorn.Config(admin_app, host=settings.ADMIN_BIND, port=settings.ADMIN_PORT, log_level="warning", access_log=False,
                                proxy_headers=False)
        self._server = uvicorn.Server(config)
        self._server.install_signal_handlers = lambda: None          # the main server owns the signals
        self._task = asyncio.create_task(self._server.serve())
        _LOGGER.info("Admin console at %s:%s (local network only: do not publish it on the proxy).", settings.ADMIN_BIND, settings.ADMIN_PORT)

    async def stop(self) -> None:
        if self._server is not None:
            self._server.should_exit = True
        if self._task is not None:
            try:
                await asyncio.wait_for(self._task, timeout=5)
            except (asyncio.TimeoutError, Exception):         # noqa: BLE001 — stopping must not fail
                self._task.cancel()
