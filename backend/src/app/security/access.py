"""Who may see or do what. One place decides; route handlers never check ownership themselves.

Accounts: `admin` (runs the server from the admin console, on another port; owns nothing and never signs in to the app), `manager` (owns cars and creates member accounts) and
`member` (works on the cars a manager links to it). A car has exactly one owner (`Vehicle.owner_id`); other accounts reach it through
`DeviceAccess` links with a level (`LEVEL_GRANTS`: `view`, `edit`, `view_control`, `control`; the owner has everything). **The admin has no
implicit access to anybody's cars.**

Every route under the protected router goes through `authorize`, which (1) applies the rule of the routes that are not about one car
(`ACCOUNT_RULES`), or (2) resolves the route's identifiers to a vehicle and checks the level the route needs (`VEHICLE_LEVELS`; by default
GET = view, anything else = edit). A route that is in neither place is **denied**: new routes must be classified on purpose
(a test fails until they are).
"""
from __future__ import annotations

from typing import Dict, Optional, Tuple

from fastapi import Depends, HTTPException, Request
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import (
    AppUser,
    ChargingSession,
    ClimateSchedule,
    DeviceAccess,
    Drive,
    Expense,
    Geofence,
    Journey,
    MaintenanceItem,
    MaintenanceLog,
    PasskeyCredential,
    PushSubscription,
    Refuel,
    Vehicle,
    VehicleDocument,
    VehiclePart,
)
from app.db.session import get_db
from app.security import auth

# A level is a SET of permissions, not a ladder: `view_control` (watch the car and send it orders) does not include `edit`.
LEVEL_GRANTS: Dict[str, frozenset] = {
    "view": frozenset({"view"}),
    "edit": frozenset({"view", "edit"}),
    "view_control": frozenset({"view", "control"}),
    "control": frozenset({"view", "edit", "control"}),
    "owner": frozenset({"view", "edit", "control", "owner"}),
}
LEVEL_ORDER: Dict[str, int] = {"view": 1, "view_control": 2, "edit": 2, "control": 3, "owner": 4}     # for sorting and display only
ROOT_ROLES = ("manager",)                 # accounts that own a space (cars, geofences, member accounts). The admin owns nothing.


def allows(level: Optional[str], needed: str) -> bool:
    """Does `level` carry the permission a route needs (`view`, `edit`, `control` or `owner`)?"""
    return needed in LEVEL_GRANTS.get(level or "", frozenset())


def is_root(user: AppUser) -> bool:
    return user.role in ROOT_ROLES


def space_owner_id(user: AppUser) -> Optional[int]:
    """The manager whose space the account lives in (itself, for admin and managers)."""
    return user.id if is_root(user) else user.parent_id


def _capped(level: Optional[str], user: AppUser) -> Optional[str]:
    """A read-only account never writes, whatever the car grants."""
    return "view" if level and user.read_only else level


async def vehicle_level(session: AsyncSession, user: AppUser, vin: str) -> Optional[str]:
    """The level this account has on the car (`owner`, `control`, `edit`, `view`) or None when it cannot even see it."""
    owner_id = (await session.execute(select(Vehicle.owner_id).where(Vehicle.vin == vin))).scalar_one_or_none()
    if owner_id is None:
        return None                                       # missing car, or one nobody owns: fail closed
    if owner_id == user.id:
        return _capped("owner", user)
    link = (await session.execute(select(DeviceAccess.level).where(DeviceAccess.user_id == user.id, DeviceAccess.vin == vin))).scalar_one_or_none()
    return _capped(link, user)


async def accessible_vehicles(session: AsyncSession, user: AppUser) -> Dict[str, str]:
    """{vin: level} of every car the account can see."""
    out: Dict[str, str] = {}
    for link in (await session.execute(select(DeviceAccess.vin, DeviceAccess.level).where(DeviceAccess.user_id == user.id))).all():
        out[link.vin] = _capped(link.level, user)
    for (vin,) in (await session.execute(select(Vehicle.vin).where(Vehicle.owner_id == user.id))).all():
        out[vin] = _capped("owner", user)
    return out


# ───────────────────────── rules ─────────────────────────
# Routes that are not about a single car. "any" = any signed-in account; "root" = a manager. (Admin routes are not here: they live in the
# admin console, a separate app on another port.)
ACCOUNT_RULES: Dict[Tuple[str, str], str] = {
    ("POST", "/api/account/login"): "root", ("POST", "/api/account/logout"): "root", ("POST", "/api/account/pin"): "root",
    ("PUT", "/api/account/pin-policy"): "root",     # when orders ask for the PIN: the manager's choice for its cars
    ("GET", "/api/account/mqtt"): "root", ("PUT", "/api/account/mqtt"): "root", ("DELETE", "/api/account/mqtt"): "root",
    ("POST", "/api/account/mqtt/test"): "root",     # the manager's own MQTT broker (Home Assistant)
    ("GET", "/api/account/status"): "root",
    ("GET", "/api/account/storage"): "any",         # space of the caller's manager (a member sees its manager's numbers)
    ("GET", "/api/account/home-layout"): "any", ("PUT", "/api/account/home-layout"): "any", ("DELETE", "/api/account/home-layout"): "any",   # each account's own Inicio
    ("PUT", "/api/account/language"): "any",        # the language of the texts is a personal choice of every account (app.i18n)
    ("GET", "/api/account/export"): "root", ("POST", "/api/account/import"): "root",    # the manager's whole space, files included
    ("POST", "/api/car-events/reconnect"): "root", ("GET", "/api/car-events/status"): "root",
    ("GET", "/api/commands/catalog"): "any", ("GET", "/api/version"): "any",
    ("GET", "/api/search"): "any",                  # searches only the cars the caller can see (accessible_vehicles)
    ("GET", "/api/expense-categories"): "any", ("GET", "/api/part-categories"): "any",
    ("GET", "/api/catalog"): "any", ("GET", "/api/catalog/mine"): "any",          # approved shared catalogue and my own proposals
    ("GET", "/api/geofences"): "any", ("GET", "/api/geofences/pvpc/current"): "any", ("GET", "/api/geofences/reverse"): "any",
    ("GET", "/api/geofences/search-address"): "any", ("POST", "/api/geofences"): "root",
    ("PUT", "/api/geofences/{geofence_id}"): "root", ("DELETE", "/api/geofences/{geofence_id}"): "root",
    # Notifications are per account: every account manages its own configuration, channels, devices and history.
    ("GET", "/api/notifications/config"): "any", ("PUT", "/api/notifications/config"): "any",
    ("GET", "/api/notifications/logs"): "any", ("DELETE", "/api/notifications/logs"): "any",
    ("GET", "/api/notifications/push/devices"): "any", ("DELETE", "/api/notifications/push/devices/{device_id}"): "any",
    ("GET", "/api/notifications/push/public-key"): "any", ("POST", "/api/notifications/push/status"): "any",
    ("POST", "/api/notifications/push/subscribe"): "any", ("POST", "/api/notifications/push/unsubscribe"): "any",
    ("POST", "/api/notifications/test"): "any",
    ("GET", "/api/vehicles"): "any", ("POST", "/api/vehicles"): "root", ("POST", "/api/vehicles/discover"): "root",
    ("GET", "/api/members"): "root", ("POST", "/api/members"): "root",
    ("PATCH", "/api/members/{member_id}"): "root", ("DELETE", "/api/members/{member_id}"): "root",
    # Passkeys (sign-in with the fingerprint) are per account: each one manages only its own.
    ("GET", "/api/passkeys"): "any", ("DELETE", "/api/passkeys/{passkey_id}"): "any",
    ("POST", "/api/passkeys/register/options"): "any", ("POST", "/api/passkeys/register/verify"): "any",
}

# Exceptions to "GET = view, anything else = edit" for routes about one car.
VEHICLE_LEVELS: Dict[Tuple[str, str], str] = {
    ("PUT", "/api/vehicles/{vin}"): "owner",                       # the car sheet
    ("DELETE", "/api/vehicles/{vin}"): "owner",
    ("POST", "/api/vehicles/{vin}/photo"): "owner", ("DELETE", "/api/vehicles/{vin}/photo"): "owner",
    ("GET", "/api/vehicles/{vin}/access"): "owner", ("PUT", "/api/vehicles/{vin}/access/{user_id}"): "owner",
    ("DELETE", "/api/vehicles/{vin}/access/{user_id}"): "owner",
    ("POST", "/api/vehicles/{vin}/commands"): "control",
    ("PUT", "/api/vehicles/{vin}/charge/schedule"): "control", ("DELETE", "/api/vehicles/{vin}/charge/schedule"): "control",
    ("POST", "/api/vehicles/{vin}/charge/start"): "control", ("POST", "/api/vehicles/{vin}/charge/stop"): "control",
    ("PUT", "/api/vehicles/{vin}/charge/target"): "control",
    ("POST", "/api/vehicles/{vin}/documents"): "owner", ("DELETE", "/api/documents/{document_id}"): "owner",    # invoice, policy...: the owner's papers
    # Timetabled pre-conditioning sends orders to the car: same level as sending one by hand
    ("POST", "/api/vehicles/{vin}/climate-schedules"): "control",
    # The Traccar link holds a server token and decides how drives are built: the owner's business
    ("GET", "/api/vehicles/{vin}/traccar"): "owner", ("PUT", "/api/vehicles/{vin}/traccar"): "owner",
    ("DELETE", "/api/vehicles/{vin}/traccar"): "owner", ("POST", "/api/vehicles/{vin}/traccar/test"): "owner",
    ("PUT", "/api/climate-schedules/{schedule_id}"): "control", ("DELETE", "/api/climate-schedules/{schedule_id}"): "control",
}

# Routes that talk to the Ebro cloud or the car: a manual car (no Ebro account) answers 409 instead of failing deeper in.
CONNECTED_ONLY = {
    ("POST", "/api/vehicles/{vin}/commands"), ("GET", "/api/vehicles/{vin}/alarm"), ("POST", "/api/vehicles/{vin}/poll"),
    ("GET", "/api/vehicles/{vin}/charge/plan"), ("PUT", "/api/vehicles/{vin}/charge/schedule"), ("DELETE", "/api/vehicles/{vin}/charge/schedule"),
    ("POST", "/api/vehicles/{vin}/charge/start"), ("POST", "/api/vehicles/{vin}/charge/stop"), ("PUT", "/api/vehicles/{vin}/charge/target"),
    ("POST", "/api/vehicles/{vin}/climate-schedules"),
}


# Path parameters that point to something that belongs to a car, and how to find the car.
async def _vin_of(session: AsyncSession, param: str, value: str) -> Optional[str]:
    if param == "vin":
        return value
    try:
        number = int(value)
    except ValueError:
        return None
    if param == "log_id":
        stmt = select(MaintenanceItem.vin).join(MaintenanceLog, MaintenanceLog.item_id == MaintenanceItem.id).where(MaintenanceLog.id == number)
    else:
        model = {"drive_id": Drive, "charge_id": ChargingSession, "refuel_id": Refuel, "expense_id": Expense, "part_id": VehiclePart,
                 "item_id": MaintenanceItem, "document_id": VehicleDocument, "schedule_id": ClimateSchedule, "journey_id": Journey}[param]
        stmt = select(model.vin).where(model.id == number)
    return (await session.execute(stmt)).scalar_one_or_none()


VEHICLE_RESOURCE_PARAMS = ("vin", "drive_id", "charge_id", "refuel_id", "expense_id", "part_id", "item_id", "log_id", "document_id", "schedule_id", "journey_id")
OTHER_RESOURCE_PARAMS = ("invite_id", "user_id", "member_id", "geofence_id", "device_id", "passkey_id")      # handled by their own rules


def required_level(method: str, template: str) -> str:
    return VEHICLE_LEVELS.get((method, template)) or ("view" if method in ("GET", "HEAD") else "edit")


def _deny_not_found() -> HTTPException:
    return HTTPException(status_code=404, detail="No encontrado.")


async def authorize(request: Request, user: AppUser = Depends(auth.require_auth), session: AsyncSession = Depends(get_db)) -> None:
    """Central authorization for every protected route (see the module docstring)."""
    route = request.scope.get("route")
    template = getattr(route, "path", request.url.path)
    if not template.startswith("/api"):                   # the matched route reports its path without the router prefix
        template = "/api" + template
    method = request.method.upper()
    params = request.path_params
    rule = ACCOUNT_RULES.get((method, template))

    if rule is not None:
        if rule == "root" and not is_root(user):
            raise HTTPException(status_code=403, detail="Solo el gestor de la cuenta puede hacer esto.")
        await _check_space_resources(session, user, params, method)
        return

    vehicle_params = [p for p in VEHICLE_RESOURCE_PARAMS if p in params]
    if not vehicle_params:
        raise HTTPException(status_code=403, detail="Ruta sin clasificar: se deniega por seguridad.")     # fail closed

    needed = required_level(method, template)
    for param in vehicle_params:
        vin = await _vin_of(session, param, params[param])
        level = await vehicle_level(session, user, vin) if vin else None
        if level is None:
            raise _deny_not_found()                       # not yours and not shared with you: it does not exist for you
        if not allows(level, needed):
            raise HTTPException(status_code=403, detail="No tienes permiso para hacer esto con este coche.")
        if (method, template) in CONNECTED_ONLY and (await session.execute(select(Vehicle.is_manual).where(Vehicle.vin == vin))).scalar_one():
            raise HTTPException(status_code=409, detail="Este coche no tiene una cuenta de Ebro conectada: no admite órdenes ni datos en directo.")


async def _check_space_resources(session: AsyncSession, user: AppUser, params: dict, method: str) -> None:
    """Things that live in a manager's space (geofences, member accounts) or belong to one account (push devices, passkeys) must be in the caller's own space."""
    if "geofence_id" in params:
        owner = (await session.execute(select(Geofence.owner_id).where(Geofence.id == _as_int(params["geofence_id"])))).scalar_one_or_none()
        if owner != space_owner_id(user):
            raise _deny_not_found()
    if "member_id" in params:
        parent = (await session.execute(select(AppUser.parent_id).where(AppUser.id == _as_int(params["member_id"])))).scalar_one_or_none()
        if parent != user.id:
            raise _deny_not_found()
    if "device_id" in params:
        owner = (await session.execute(select(PushSubscription.user_id).where(PushSubscription.id == _as_int(params["device_id"])))).scalar_one_or_none()
        if owner != user.id:
            raise _deny_not_found()
    if "passkey_id" in params:
        owner = (await session.execute(select(PasskeyCredential.user_id).where(PasskeyCredential.id == _as_int(params["passkey_id"])))).scalar_one_or_none()
        if owner != user.id:
            raise _deny_not_found()


def _as_int(value: str) -> int:
    try:
        return int(value)
    except ValueError:
        return -1


# ───────────────────────── ownership helpers ─────────────────────────
async def first_manager_id(session: AsyncSession) -> Optional[int]:
    """The oldest manager: the owner of whatever the server's own environment configures (EBRO_PHONE, EBRO_PIN) and of pre-ownership data."""
    return (await session.execute(select(func.min(AppUser.id)).where(AppUser.role == "manager"))).scalar_one_or_none()


async def foreign_vins(session_or_owner, owner_id: Optional[int] = None) -> frozenset:
    """VINs that belong to a manager other than `owner_id`. Usable as `foreign_vins(owner_id)` (opens its own session)."""
    if owner_id is None and not hasattr(session_or_owner, "execute"):
        owner_id = session_or_owner
        from app.db.session import async_session_factory
        async with async_session_factory() as session:
            return await foreign_vins(session, owner_id)
    rows = (await session_or_owner.execute(select(Vehicle.vin).where(Vehicle.owner_id.is_not(None), Vehicle.owner_id != owner_id))).all()
    return frozenset(v for (v,) in rows)


async def within_vehicle_limit(session: AsyncSession, owner_id: int) -> bool:
    """May this manager own one more car? (-1 unlimited, 0 none, N cap)."""
    owner = await session.get(AppUser, owner_id)
    if owner is None or not is_root(owner) or not owner.is_active:
        return False
    if owner.vehicle_limit < 0:
        return True
    owned = (await session.execute(select(func.count()).select_from(Vehicle).where(Vehicle.owner_id == owner_id))).scalar_one()
    return owned < owner.vehicle_limit
