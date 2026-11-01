"""Main entry point of the EVBro application."""
from __future__ import annotations

import hashlib
import json
import logging
import os
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from app import __version__, i18n
from app.admin_app import AdminConsole
from app.api import api_router
from app.api.errors import install_error_handlers
from app.config import settings
from app.db.session import init_db
from app.paths import frontend_dir
from app.security.auth import announce_setup_if_needed
from app.services.poller import VehiclePoller
from app.services.traccar import TraccarHub

# Every file the app creates (tokens, invoices, encryption key) is private to the user running it.
os.umask(0o077)

# Logging setup
logging.basicConfig(
    level=logging.DEBUG if settings.DEBUG else logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
_LOGGER = logging.getLogger("app")


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Application lifecycle: startup and shutdown."""
    _LOGGER.info("Arrancando EVBro Server...")

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

    yield

    # 4. stop background tasks
    _LOGGER.info("Deteniendo EVBro...")
    await console.stop()
    await TraccarHub.get_instance().stop()
    await poller.stop()


app = FastAPI(
    title="EVBro",
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
# (`core/actions.js`) and the theme bootstrap is a file, so a text that slipped into the HTML can never run. Styles still allow
# `'unsafe-inline'` because the interface uses `style=` attributes (moving them to classes is the next step); what the policy already
# guarantees is that the page cannot load code from, send data to or be framed by anybody but ourselves and the few hosts listed
# (Leaflet, OpenStreetMap, Google Fonts). `connect-src` is completed per request with the WebSocket origin (older browsers do not
# count `ws(s):` as `'self'`).
_PAGE_CSP = (
    "default-src 'self'; "
    "script-src 'self' https://unpkg.com; "
    "style-src 'self' https://fonts.googleapis.com https://unpkg.com; "
    "font-src 'self' https://fonts.gstatic.com; "
    "img-src 'self' data: blob: https://tile.openstreetmap.org https://*.tile.openstreetmap.org https://*.tile.openstreetmap.fr https://unpkg.com; "
    "connect-src 'self' {ws_origin}; "
    "worker-src 'self'; manifest-src 'self'; frame-src 'self'; "
    "object-src 'none'; base-uri 'self'; form-action 'self'; frame-ancestors 'none'"
)
# API responses are data: nothing may run in them. `frame-ancestors 'self'` lets the app's own viewer show an uploaded PDF in a frame.
_API_CSP = "default-src 'none'; frame-ancestors 'self'"


def page_csp(request: Request) -> str:
    scheme = "wss" if request.url.scheme == "https" else "ws"
    return _PAGE_CSP.format(ws_origin=f"{scheme}://{request.headers.get('host', request.url.netloc)}")


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
    if request.url.scheme == "https":
        response.headers.setdefault("Strict-Transport-Security", "max-age=31536000")
    return response

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
                        root / "templates" / "index.html"]):
        digest.update(path.relative_to(root).as_posix().encode())
        digest.update(path.read_bytes())
    return digest.hexdigest()[:10]


templates.env.globals["asset_version"] = _asset_version()

# Mount the REST API and WebSockets
app.include_router(api_router)


@app.get("/", response_class=HTMLResponse)
async def serve_dashboard(request: Request):
    """Serves the main web dashboard without persistent caching so changes show up immediately.
    The page goes out in the language of the request (cookie of the account or the browser's) with its web dictionary inlined."""
    lang = i18n.get_language()
    return templates.TemplateResponse(
        request=request,
        name="index.html",
        context={"lang": lang, "i18n_json": i18n.page_dictionary_json(lang)},
        headers={"Cache-Control": "no-cache, no-store, must-revalidate", "Pragma": "no-cache", "Expires": "0", "Vary": "Cookie, Accept-Language"}
    )


@app.get("/manifest.json")
async def get_manifest():
    """Serves the PWA manifest."""
    manifest_path = os.path.join(STATIC_DIR, "manifest.json")
    return FileResponse(manifest_path, media_type="application/manifest+json")


@app.get("/sw.js")
async def get_service_worker():
    """Serves the Service Worker for offline support and PWA install."""
    sw_path = os.path.join(STATIC_DIR, "sw.js")
    return FileResponse(
        sw_path,
        media_type="application/javascript",
        headers={"Cache-Control": "no-cache, no-store, must-revalidate", "Pragma": "no-cache", "Expires": "0"}
    )


@app.get("/favicon.ico")
async def get_favicon():
    """Favicon for web browsers."""
    icon_path = os.path.join(STATIC_DIR, "icons", "favicon.png")
    return FileResponse(icon_path, media_type="image/png")


@app.get("/health")
async def health_check():
    """Health endpoint for Docker / Kubernetes."""
    return {"status": "healthy", "service": "EVBro"}


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
