"""Main REST API router.

**What is public and what is not.** Everything hanging from `protected` requires a session. The only public parts are
`/api/auth/*` (so that one can log in) and `/api/ws`, which authenticates itself before
accepting the connection. Adding a new router: put it in `protected`. `tests/test_api_auth.py`
walks every route and fails if any is left unprotected.
"""
from fastapi import APIRouter, Depends

from app import __version__, build_commit
from app.api.routes_account import router as account_router
from app.api.routes_account_data import router as account_data_router
from app.api.routes_auth import router as auth_router
from app.api.routes_book import router as book_router
from app.api.routes_car_events import router as car_events_router
from app.api.routes_catalog import router as catalog_router
from app.api.routes_catalog_online import router as catalog_online_router
from app.api.routes_charge_plan import router as charge_plan_router
from app.api.routes_charges import router as charges_router
from app.api.routes_climate_schedules import router as climate_schedules_router
from app.api.routes_commands import router as commands_router
from app.api.routes_costs import router as costs_router
from app.api.routes_documents import router as documents_router
from app.api.routes_drives import router as drives_router
from app.api.routes_expenses import router as expenses_router
from app.api.routes_geofences import router as geofences_router
from app.api.routes_journeys import router as journeys_router
from app.api.routes_maintenance import router as maintenance_router
from app.api.routes_maintenance_plan import router as maintenance_plan_router
from app.api.routes_members import router as members_router
from app.api.routes_notifications import router as notifications_router
from app.api.routes_parts import router as parts_router
from app.api.routes_passkey_login import router as passkey_login_router
from app.api.routes_passkeys import router as passkeys_router
from app.api.routes_refuels import router as refuels_router
from app.api.routes_stats import router as stats_router
from app.api.routes_telemetry import client_router as telemetry_client_router
from app.api.routes_telemetry import router as telemetry_router
from app.api.routes_timeline import router as timeline_router
from app.api.routes_traccar import router as traccar_router
from app.api.routes_vehicles import router as vehicles_router
from app.api.routes_ws import router as ws_router
from app.security.access import authorize
from app.security.auth import require_auth, verify_origin

api_router = APIRouter(prefix="/api")

# Public: log in (with an origin check against "login CSRF")
api_router.include_router(auth_router, dependencies=[Depends(verify_origin)])
api_router.include_router(passkey_login_router, dependencies=[Depends(verify_origin)])     # /api/auth/passkeys/login/*
# Server to server: another installation reads this server's catalogue or sends a proposal with its key (no session, no cookies)
api_router.include_router(catalog_online_router)
# Server to server: the project's server receives the anonymous daily reports of other installations (only with TELEMETRY_RECEIVER)
api_router.include_router(telemetry_router)

# Protected: valid session + same origin on requests that modify data
protected = APIRouter(dependencies=[Depends(verify_origin), Depends(require_auth), Depends(authorize)])
protected.include_router(account_router)
protected.include_router(notifications_router)
protected.include_router(vehicles_router)
protected.include_router(car_events_router)
protected.include_router(drives_router)
protected.include_router(charges_router)
protected.include_router(charge_plan_router)
protected.include_router(commands_router)
protected.include_router(geofences_router)
protected.include_router(maintenance_router)
protected.include_router(maintenance_plan_router)
protected.include_router(refuels_router)
protected.include_router(timeline_router)
protected.include_router(stats_router)
protected.include_router(climate_schedules_router)
protected.include_router(traccar_router)
protected.include_router(catalog_router)
protected.include_router(account_data_router)
protected.include_router(book_router)
protected.include_router(expenses_router)
protected.include_router(journeys_router)
protected.include_router(members_router)
protected.include_router(parts_router)
protected.include_router(costs_router)
protected.include_router(documents_router)
protected.include_router(passkeys_router)
protected.include_router(telemetry_client_router)


@protected.get("/version", tags=["System"])
async def get_version():
    """Version of EVBro and the commit the image was built from (Ajustes → Sistema)."""
    return {"version": __version__, "commit": build_commit()}


api_router.include_router(protected)

# WebSocket: checks session and origin itself (HTTP dependencies do not apply to WS)
api_router.include_router(ws_router)
