"""Main entry point of the EVbro application."""
from __future__ import annotations

import hashlib
import json
import logging
import os
from contextlib import asynccontextmanager
from pathlib import Path
from urllib.parse import urlparse

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from starlette.middleware.gzip import DEFAULT_EXCLUDED_CONTENT_TYPES, GZipMiddleware

from app import __version__, i18n
from app.admin_app import AdminConsole
from app.api import api_router
from app.api.errors import install_error_handlers
from app.config import get_data_dir, settings
from app.db.session import init_db
from app.logging_setup import LOG_FORMAT, install_file_logging
from app.paths import frontend_dir
from app.security.auth import announce_setup_if_needed
from app.services.poller import VehiclePoller
from app.services.telemetry import TelemetryService
from app.services.traccar import TraccarHub

# Every file the app creates (tokens, invoices, encryption key) is private to the user running it.
os.umask(0o077)

# Logging setup
logging.basicConfig(
    level=logging.DEBUG if settings.DEBUG else logging.INFO,
    format=LOG_FORMAT,
)
_LOGGER = logging.getLogger("app")
# urllib3 writes every request line at DEBUG, query string included: the Ebro login sends the account's phone and the refresh token
# there (core/ebro_login.py). DEBUG is for EVbro's own messages, never for those.
logging.getLogger("urllib3").setLevel(logging.INFO)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Application lifecycle: startup and shutdown."""
    # 0. a copy of the log in the data volume: Docker's goes away with the container on every deploy (logging_setup.py)
    install_file_logging(get_data_dir())
    _LOGGER.info("Arrancando EVbro Server...")

    # 1. initialise the database schema
    try:
        await init_db()
    except Exception as e:
        # Fatal on purpose: running on an old schema looks alive but breaks every new feature quietly (2026-10-30, a rolled-back
        # migration left the app up for minutes). Docker restarts the container and the health check shows it red.
        _LOGGER.error(f"Error initialising the database: {e}")
        raise RuntimeError(f"La base de datos no se pudo migrar: {e}") from e

    # 1b. if there is no user yet, explain in the log how to create one
    try:
        await announce_setup_if_needed()
    except Exception as e:
        _LOGGER.error(f"Could not check the initial user: {e}")

    # 2. start the polling and state engine
    poller = VehiclePoller.get_instance()
    await poller.start()
    await TraccarHub.get_instance().start()             # live GPS from Traccar servers (cars that have one)

    # 3. admin console (another port, another interface)
    console = AdminConsole()
    await console.start()

    # 4. anonymous reports: only collected and sent if the administrator turned them on (services/telemetry.py)
    await TelemetryService.get_instance().start()

    yield

    # 5. stop background tasks
    _LOGGER.info("Deteniendo EVbro...")
    await TelemetryService.get_instance().stop()
    await console.stop()
    await TraccarHub.get_instance().stop()
    await poller.stop()


app = FastAPI(
    title="EVbro",
    description="Servidor autoalojado de telemetría, gestión y seguimiento para vehículos Ebro",
    version=__version__,
    lifespan=lifespan,
    # The interactive docs describe the whole API: DEBUG mode only.
    docs_url="/docs" if settings.DEBUG else None,
    redoc_url=None,
    openapi_url="/openapi.json" if settings.DEBUG else None,
)
install_error_handlers(app)


# Largest JSON body the API accepts. Uploads are multipart and have their own limits (`services/uploads.py`); a JSON body this
# size is never legitimate, and the login hashes whatever it gets with scrypt, so it must not be fed megabytes.
MAX_JSON_BODY_BYTES = 1024 * 1024

# Content Security Policy of the web app. **No inline script at all**: handlers are declared with `data-on-*` attributes
# (`core/actions.js`) and the theme bootstrap is a file, so a text that slipped into the HTML can never run. No inline styles either:
# the `style=` attributes became classes (`utilities.css`) and the dynamic ones go through `data-css` (CSSOM). The page cannot load code
# from, send data to or be framed by anybody but ourselves and the map tiles' hosts (Google Fonts went on 27-11-2026: the app uses
# the system's font). **Scripts are only our own**: Leaflet is served from `static/vendor/` (a CDN that publishes anybody's packages would turn any slip in escaping into code). `connect-src` is completed per request with the WebSocket origin (older browsers do not
# count `ws(s):` as `'self'`).
_PAGE_CSP = (
    "default-src 'self'; "
    "script-src 'self'; "
    "style-src 'self'; "
    "font-src 'self'; "
    "img-src 'self' data: blob: {tile_origins}; "
    "connect-src 'self' {ws_origin}; "
    "worker-src 'self'; manifest-src 'self'; frame-src 'self'; "
    "object-src 'none'; base-uri 'self'; form-action 'self'; frame-ancestors 'none'"
)
# API responses are data: nothing may run in them. `frame-ancestors 'self'` lets the app's own viewer show an uploaded PDF in a frame.
_API_CSP = "default-src 'none'; frame-ancestors 'self'"


def tile_origins() -> str:
    """The origins the map tiles come from (MAP_TILE_URL and its fallback), for `img-src`. `{s}` (Leaflet's subdomain) becomes `*`."""
    origins = []
    for template in (settings.MAP_TILE_URL, settings.MAP_TILE_FALLBACK_URL):
        parsed = urlparse((template or "").strip().replace("{s}", "*"))
        if parsed.scheme in ("http", "https") and parsed.netloc and not any(c in parsed.netloc for c in " ;,'\""):
            origin = f"{parsed.scheme}://{parsed.netloc}"
            if origin not in origins:
                origins.append(origin)
    return " ".join(origins)


def page_csp(request: Request) -> str:
    scheme = "wss" if request.url.scheme == "https" else "ws"
    return _PAGE_CSP.format(ws_origin=f"{scheme}://{request.headers.get('host', request.url.netloc)}", tile_origins=tile_origins())


@app.middleware("http")
async def security_headers(request: Request, call_next):
    """Basic security headers; API responses are not cached. Also sets the language of the request."""
    i18n.set_language(i18n.pick(request.cookies, request.headers.get("accept-language")))
    if request.url.path.startswith("/api/") and request.method in ("POST", "PUT", "PATCH"):
        length = request.headers.get("content-length", "")
        if request.headers.get("content-type", "").startswith("application/json") and length.isdigit() and int(length) > MAX_JSON_BODY_BYTES:
            return JSONResponse(status_code=413, content={"detail": i18n.translate("La petición es demasiado grande.")})
    response = await call_next(request)
    response.headers.setdefault("X-Content-Type-Options", "nosniff")
    response.headers.setdefault("X-Frame-Options", "DENY")
    content_type = response.headers.get("content-type", "")
    if request.url.path.startswith("/api/"):
        # Uploaded images already carry `sandbox`; a PDF only gets the framing rule (the browser's viewer renders it).
        response.headers.setdefault("Content-Security-Policy", "frame-ancestors 'self'" if content_type.startswith("application/pdf") else _API_CSP)
    elif content_type.startswith("text/html"):
        response.headers.setdefault("Content-Security-Policy", page_csp(request))
    # do NOT use `same-origin` or `no-referrer`: OpenStreetMap requires a valid Referer and, without it, answers with an
    # "Access blocked" tile (HTTP 200, so `tileerror` does not even fire). With this policy external sites
    # only get the origin, never the path.
    response.headers.setdefault("Referrer-Policy", "strict-origin-when-cross-origin")
    response.headers.setdefault("Permissions-Policy", "camera=(), microphone=(), geolocation=()")
    if request.url.path.startswith("/api/") and "no-store" not in response.headers.get("Cache-Control", ""):
        response.headers["Cache-Control"] = "no-store"        # uploaded files already say `private, no-store`
    elif request.url.path.startswith("/static/") and request.query_params.get("v") == ASSET_VERSION and response.status_code == 200:
        # The scripts and sheets the page names with `?v=` change name when their content changes: they can be kept for good.
        # Without this the browser asked again for each of them (about 190) on every opening. Only the current version: an old
        # page asking for an old `v` gets today's file, which must not be kept under that name.
        response.headers["Cache-Control"] = "public, max-age=31536000, immutable"
    elif request.url.path.startswith("/static/images/"):
        # The style sheets name these pictures without the `?v=` the scripts and sheets carry, and with no header a browser
        # keeps a picture for days on its own guess (a tenth of its age): a replaced banner kept showing the old one. `no-cache`
        # is «ask before using it»: one cheap 304 per picture.
        response.headers.setdefault("Cache-Control", "no-cache")
    if request.url.scheme == "https":
        response.headers.setdefault("Strict-Transport-Security", "max-age=31536000")
    return response

# Compression of what is text (the page, scripts, sheets, JSON): production sent everything as it is (1.8 MB on a first visit,
# 0.8 MB compressed). Pictures, fonts and PDF are compressed already. The sign-in routes are left alone: their answers may carry a
# token or an invitation link, and a compressed answer that also echoes what the client sent can leak it through its size (BREACH).
class _Compression:
    _SKIP = ("/api/auth", "/api/passkeys", "/api/members")      # sign-in, passkeys, invitation links

    def __init__(self, app_) -> None:
        self.app = app_
        self.gzip = GZipMiddleware(app_, minimum_size=1024, compresslevel=6,
                                   exclude_content_types=(*DEFAULT_EXCLUDED_CONTENT_TYPES, "application/pdf"))

    async def __call__(self, scope, receive, send) -> None:
        if scope["type"] == "http" and not scope.get("path", "").startswith(self._SKIP):
            await self.gzip(scope, receive, send)
        else:
            await self.app(scope, receive, send)


app.add_middleware(_Compression)

# Static routes and HTML templates
FRONTEND_DIR = frontend_dir()
STATIC_DIR = os.path.join(FRONTEND_DIR, "static")
TEMPLATES_DIR = os.path.join(FRONTEND_DIR, "templates")

if os.path.exists(STATIC_DIR):
    app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")

templates = Jinja2Templates(directory=TEMPLATES_DIR)


def _asset_version() -> str:
    """Hash of the JS/CSS/HTML content: it goes in `?v=` so browsers (phones above all) reload only
    when the frontend changes, without bumping numbers by hand."""
    digest = hashlib.sha1()
    root = Path(FRONTEND_DIR)
    for path in sorted([*root.joinpath("static", "js").rglob("*.js"), *root.joinpath("static", "css").rglob("*.css"),
                        *root.joinpath("static", "vendor").rglob("*.js"), *root.joinpath("static", "vendor").rglob("*.css"),
                        root / "static" / "sw.js", root / "templates" / "index.html"]):
        digest.update(path.relative_to(root).as_posix().encode())
        digest.update(path.read_bytes())
    return digest.hexdigest()[:10]


ASSET_VERSION = _asset_version()
templates.env.globals["asset_version"] = ASSET_VERSION

# Mount the REST API and WebSockets
app.include_router(api_router)


@app.get("/", response_class=HTMLResponse)
async def serve_dashboard(request: Request):
    """Serves the main web dashboard without persistent caching so changes show up immediately.
    The page goes out in the language of the request (cookie of the account or the browser's) with its web dictionary inlined."""
    lang = i18n.get_language()
    page = templates.TemplateResponse(
        request=request,
        name="index.html",
        context={"lang": lang, "i18n_json": i18n.page_dictionary_json(lang), "map_tiles": settings.MAP_TILE_URL.strip(),
                 "map_tiles_fallback": settings.MAP_TILE_FALLBACK_URL.strip(), "map_attribution": settings.MAP_ATTRIBUTION.strip()},
    )
    # `no-cache` + ETag instead of `no-store`: the browser still asks every time (a deploy shows at once), but an unchanged page
    # is a 304 of a few bytes instead of the whole page (283 KB, its dictionary inside). It holds nothing of the user.
    etag = '"' + hashlib.sha1(page.body).hexdigest()[:20] + '"'
    headers = {"Cache-Control": "no-cache", "ETag": etag, "Vary": "Cookie, Accept-Language"}
    if etag in [t.strip() for t in request.headers.get("if-none-match", "").split(",")]:
        return Response(status_code=304, headers=headers)
    page.headers.update(headers)
    return page


@app.get("/manifest.json")
async def get_manifest():
    """Serves the PWA manifest."""
    manifest_path = os.path.join(STATIC_DIR, "manifest.json")
    return FileResponse(manifest_path, media_type="application/manifest+json")


@app.get("/sw.js")
async def get_service_worker():
    """Serves the Service Worker for offline support and PWA install."""
    # It carries the version of the files (`ASSET_VERSION`): it precaches exactly what the page asks for, keeps one cache per
    # version, and a deploy changes its bytes, which is what makes the browser install the new one and drop the old cache.
    return Response(
        _service_worker_source(),
        media_type="application/javascript",
        headers={"Cache-Control": "no-cache, no-store, must-revalidate", "Pragma": "no-cache", "Expires": "0"}
    )


def _service_worker_source() -> str:
    with open(os.path.join(STATIC_DIR, "sw.js"), encoding="utf-8") as fh:
        return fh.read().replace("__ASSET_VERSION__", ASSET_VERSION)


@app.get("/favicon.ico")
async def get_favicon():
    """Favicon for web browsers."""
    icon_path = os.path.join(STATIC_DIR, "icons", "favicon.png")
    return FileResponse(icon_path, media_type="image/png")


@app.get("/health")
async def health_check():
    """Liveness for Docker: the process answers. It checks nothing else on purpose (a database outage must not make Docker
    restart the app in a loop); `/health/ready` is the one an outside monitor should watch."""
    return {"status": "healthy", "service": "EVbro"}


# A pass of the polling loop takes about 10 s plus the reads; this long without one means the reads of every car have stopped
POLL_LOOP_STALE_S = 300


@app.get("/health/ready")
async def readiness_check():
    """For an outside monitor: 200 when the database answers and the polling loop has finished a pass recently, 503 otherwise.
    Says which check failed, nothing more (it is public)."""
    from sqlalchemy import text

    from app.db.session import async_session_factory
    from app.timeutil import utcnow

    checks = {"database": True, "polling": True}
    try:
        async with async_session_factory() as session:
            await session.execute(text("SELECT 1"))
    except Exception:                                         # noqa: BLE001 — the answer is the check itself
        checks["database"] = False
    poller = VehiclePoller.get_instance()
    if poller._running:
        beat = VehiclePoller.loop_heartbeat
        checks["polling"] = beat is not None and (utcnow() - beat).total_seconds() < POLL_LOOP_STALE_S
    ok = all(checks.values())
    return JSONResponse(status_code=200 if ok else 503, content={"status": "ok" if ok else "degraded", "checks": checks})


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(
        "app.main:app",
        host=settings.WEB_HOST,
        port=settings.WEB_PORT,
        reload=settings.DEBUG,
    )


@app.get("/.well-known/assetlinks.json")
async def android_assetlinks():
    """Digital Asset Links for the Android app (Trusted Web Activity): Android checks it to open the site without browser bars.

    Public by nature (Google fetches it anonymously). Set ANDROID_ASSETLINKS_JSON to the JSON Bubblewrap prints; empty = 404."""
    raw = (settings.ANDROID_ASSETLINKS_JSON or "").strip()
    if not raw:
        raise HTTPException(status_code=404, detail="No configurado.")
    try:
        data = json.loads(raw)
    except ValueError as ex:
        raise HTTPException(status_code=500, detail="ANDROID_ASSETLINKS_JSON no es JSON válido.") from ex
    return JSONResponse(data, headers={"Cache-Control": "public, max-age=3600"})


@app.get("/legal", response_class=HTMLResponse)
async def legal_page(request: Request):
    """Legal notice and privacy: what this server stores, what for, who sees it and how to request or delete your data. Public."""
    return templates.TemplateResponse(request, "legal.html", {"operator": settings.LEGAL_OPERATOR.strip(), "contact": settings.LEGAL_CONTACT.strip(),
                                                              "host": request.headers.get("x-forwarded-host", request.headers.get("host", ""))})
