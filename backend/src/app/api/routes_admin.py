"""Administration of the hosted service: invitation tokens and users. Every route requires the admin role."""
from __future__ import annotations

import asyncio
import datetime
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.db.models import AppUser, EbroAccount, Invite, Vehicle
from app.db.session import get_db
from app.security import auth, passwords
from app.services import admin_audit, admin_stats, catalog, catalog_remote, invites, storage, telemetry, tenant
from app.services.parts import PART_CATEGORIES, POSITIONS

STORAGE_MAX_MB = 1_000_000

router = APIRouter(prefix="/admin", tags=["Admin"], dependencies=[Depends(auth.require_admin)])


class InviteCreate(BaseModel):
    note: Optional[str] = Field(default=None, max_length=120)
    days: Optional[int] = Field(default=None, ge=1, le=90)
    vehicle_limit: Optional[int] = Field(default=None, ge=-1, le=1000)      # -1 unlimited, 0 none; None = server default
    member_limit: Optional[int] = Field(default=None, ge=-1, le=1000)
    storage_limit_mb: Optional[int] = Field(default=None, ge=-1, le=STORAGE_MAX_MB)     # MB of files; -1 unlimited, 0 none, None = server default


class ManagerCreate(BaseModel):
    username: str = Field(min_length=3, max_length=40)
    password: str
    vehicle_limit: Optional[int] = Field(default=None, ge=-1, le=1000)
    member_limit: Optional[int] = Field(default=None, ge=-1, le=1000)
    storage_limit_mb: Optional[int] = Field(default=None, ge=-1, le=STORAGE_MAX_MB)     # MB of files; -1 unlimited, 0 none, None = server default


class UserUpdate(BaseModel):
    is_active: Optional[bool] = None
    new_password: Optional[str] = None
    read_only: Optional[bool] = None
    vehicle_limit: Optional[int] = Field(default=None, ge=-1, le=1000)
    member_limit: Optional[int] = Field(default=None, ge=-1, le=1000)
    storage_limit_mb: Optional[int] = Field(default=None, ge=-1, le=STORAGE_MAX_MB)     # MB of files; -1 unlimited, 0 none, None = server default
    expires_at: Optional[datetime.datetime] = None          # null removes the expiry


def _invite_out(i: Invite, users: dict) -> dict:
    return {"id": i.id, "note": i.note, "storage_limit_mb": i.storage_limit_mb, "state": invites.invite_state(i), "created_at": i.created_at.isoformat(), "expires_at": i.expires_at.isoformat(),
            "used_at": i.used_at.isoformat() if i.used_at else None, "used_by": users.get(i.used_by)}


async def _user_out(db: AsyncSession, u: AppUser) -> dict:
    """What the admin may know about an account: counts and health, never the cars themselves (no VIN, plate, position, history)."""
    vehicles = (await db.execute(select(func.count()).select_from(Vehicle).where(Vehicle.owner_id == u.id))).scalar_one()
    members = (await db.execute(select(func.count()).select_from(AppUser).where(AppUser.parent_id == u.id))).scalar_one()
    ebro = (await db.execute(select(EbroAccount.last_login).where(EbroAccount.user_id == u.id, EbroAccount.is_active.is_(True)))).first()
    return {"id": u.id, "username": u.username, "role": u.role, "is_active": u.is_active, "read_only": u.read_only, "invited_by": u.invited_by,
            "vehicle_limit": u.vehicle_limit, "member_limit": u.member_limit, "expires_at": u.expires_at.isoformat() if u.expires_at else None,
            "vehicle_count": vehicles, "member_count": members, "ebro_connected": ebro is not None,
            "storage_limit_mb": u.storage_limit_mb, "storage_limit_effective_mb": storage.effective_limit_mb(u),
            "storage_used_bytes": await storage.storage_usage(db, u.id),
            "ebro_last_login": ebro[0].isoformat() if ebro and ebro[0] else None,
            "created_at": u.created_at.isoformat() if u.created_at else None, "last_login": u.last_login.isoformat() if u.last_login else None}


@router.get("/stats")
async def stats(db: AsyncSession = Depends(get_db)):
    """Aggregated numbers about the server and its use (no car, plate, position or name of anybody's car)."""
    return await admin_stats.collect(db)


@router.get("/invites")
async def list_invites(db: AsyncSession = Depends(get_db)):
    users = {u.id: u.username for u in (await db.execute(select(AppUser))).scalars()}
    rows = (await db.execute(select(Invite).order_by(Invite.id.desc()))).scalars().all()
    return [_invite_out(i, users) for i in rows]


@router.get("/audit")
async def audit_log(limit: int = 200, db: AsyncSession = Depends(get_db)):
    """What the administrators did in the console, newest first."""
    return await admin_audit.recent(db, min(max(limit, 1), 1000))


@router.post("/invites")
async def create_invite(req: InviteCreate, admin: AppUser = Depends(auth.require_admin), db: AsyncSession = Depends(get_db)):
    """Creates an invitation. The token is returned **once**: only its hash is stored."""
    await admin_audit.log(db, admin, "invite.create", req.note or "", request=None, commit=False)
    invite, token = await invites.create_invite(db, admin.id, req.note, req.days or settings.INVITE_DEFAULT_DAYS,
                                                vehicle_limit=req.vehicle_limit, member_limit=req.member_limit, storage_limit_mb=req.storage_limit_mb)
    return {**_invite_out(invite, {}), "token": token}


@router.post("/managers")
async def create_manager(req: ManagerCreate, db: AsyncSession = Depends(get_db)):
    """Creates a manager account directly (the administrator hands over the username and password). The administrator owns no cars."""
    await admin_audit.log(db, None, "manager.create", req.username, commit=False)
    username = req.username.strip().lower()
    problem = passwords.password_problem(req.password, username)
    if problem:
        raise HTTPException(status_code=422, detail=problem)
    if (await db.execute(select(AppUser.id).where(AppUser.username == username, AppUser.is_admin.is_(False)))).first() is not None:
        raise HTTPException(status_code=409, detail="Ese nombre de usuario ya está en uso.")
    manager = AppUser(username=username, password_hash=await asyncio.to_thread(passwords.hash_password, req.password), role="manager",
                      vehicle_limit=req.vehicle_limit if req.vehicle_limit is not None else settings.DEFAULT_VEHICLE_LIMIT,
                      member_limit=req.member_limit if req.member_limit is not None else settings.DEFAULT_MEMBER_LIMIT,
                      storage_limit_mb=req.storage_limit_mb)
    db.add(manager)
    await db.commit()
    await db.refresh(manager)
    return await _user_out(db, manager)


@router.delete("/invites/{invite_id}")
async def revoke_invite(invite_id: int, db: AsyncSession = Depends(get_db)):
    await admin_audit.log(db, None, "invite.revoke", str(invite_id), commit=False)
    invite = await db.get(Invite, invite_id)
    if invite is None:
        raise HTTPException(status_code=404, detail="Invitación no encontrada.")
    if invites.invite_state(invite) != "active":
        raise HTTPException(status_code=409, detail="Solo se pueden anular invitaciones activas.")
    invite.revoked = True
    await db.commit()
    return {"ok": True}


@router.get("/users")
async def list_users(db: AsyncSession = Depends(get_db)):
    """Admin and manager accounts. Member accounts belong to their manager's space and are not shown here."""
    rows = (await db.execute(select(AppUser).where(AppUser.role.in_(("admin", "manager"))).order_by(AppUser.id))).scalars().all()
    return [await _user_out(db, u) for u in rows]


@router.patch("/users/{user_id}")
async def update_user(user_id: int, req: UserUpdate, request: Request, admin: AppUser = Depends(auth.require_admin), db: AsyncSession = Depends(get_db)):
    """Deactivate/reactivate an account or set a new password (the user is signed out everywhere)."""
    await admin_audit.log(db, admin, "user.update", str(user_id), ", ".join(sorted(req.model_fields_set - {"new_password"})) + (" + contraseña" if req.new_password else ""), request=request, commit=False)
    user = await db.get(AppUser, user_id)
    if user is None or user.role == "member":                # members are managed by their manager, not by the admin
        raise HTTPException(status_code=404, detail="Usuario no encontrado.")
    if req.is_active is False:
        if user.id == admin.id:
            raise HTTPException(status_code=409, detail="No puedes desactivar tu propia cuenta.")
        if user.role == "admin":
            admins = (await db.execute(select(func.count()).select_from(AppUser).where(AppUser.role == "admin", AppUser.is_active.is_(True)))).scalar_one()
            if admins <= 1:
                raise HTTPException(status_code=409, detail="Debe quedar al menos un administrador activo.")
    if req.new_password is not None:
        problem = passwords.password_problem(req.new_password, user.username)
        if problem:
            raise HTTPException(status_code=422, detail=problem)
        user.password_hash = await asyncio.to_thread(passwords.hash_password, req.new_password)
    if req.is_active is not None:
        user.is_active = req.is_active
    sent = req.model_fields_set
    if req.read_only is not None:
        user.read_only = req.read_only
    if req.vehicle_limit is not None:
        user.vehicle_limit = req.vehicle_limit
    if req.member_limit is not None:
        user.member_limit = req.member_limit
    if "storage_limit_mb" in sent:                          # null puts the account back on the server default
        user.storage_limit_mb = req.storage_limit_mb
    if "expires_at" in sent:
        user.expires_at = req.expires_at.replace(tzinfo=None) if req.expires_at else None
    await db.commit()
    if req.new_password is not None or req.is_active is False:
        keep = request.cookies.get(auth.COOKIE_NAME) if user.id == admin.id else None
        await auth.revoke_user_sessions(db, user.id, keep_token=keep)
    return await _user_out(db, user)


@router.delete("/users/{user_id}")
async def delete_manager(user_id: int, confirm: str = "", admin: AppUser = Depends(auth.require_admin), db: AsyncSession = Depends(get_db)):
    """Deletes a manager's whole space (cars, history, member accounts, geofences, Ebro account, files). Irreversible: `confirm` must be the username."""
    await admin_audit.log(db, admin, "manager.delete", str(user_id), commit=False)
    user = await db.get(AppUser, user_id)
    if user is None or user.role != "manager":
        raise HTTPException(status_code=404, detail="Gestor no encontrado.")
    if confirm.strip().lower() != user.username:
        raise HTTPException(status_code=409, detail="Para borrar el espacio escribe el nombre de usuario exacto en «confirm».")
    removed = await tenant.delete_manager_space(db, user.id)
    await db.commit()
    return {"ok": True, "removed": removed}


# ───────────────────────── shared catalogue: the approval queue ─────────────────────────
class RejectIn(BaseModel):
    reason: Optional[str] = Field(default=None, max_length=255)


@router.get("/catalog")
async def catalog_queue(status: str = "pending", db: AsyncSession = Depends(get_db)):
    try:
        return await catalog.for_review(db, status)
    except catalog.CatalogError as ex:
        raise HTTPException(status_code=ex.status_code, detail=ex.detail) from ex


@router.post("/catalog/{entry_id}/approve")
async def catalog_approve(entry_id: int, admin: AppUser = Depends(auth.require_admin), db: AsyncSession = Depends(get_db)):
    await admin_audit.log(db, admin, "catalog.approve", str(entry_id), commit=False)
    try:
        return await catalog.serialize_one(db, await catalog.review(db, entry_id, admin.id, approve=True))
    except catalog.CatalogError as ex:
        raise HTTPException(status_code=ex.status_code, detail=ex.detail) from ex


@router.post("/catalog/{entry_id}/reject")
async def catalog_reject(entry_id: int, req: RejectIn, admin: AppUser = Depends(auth.require_admin), db: AsyncSession = Depends(get_db)):
    await admin_audit.log(db, admin, "catalog.reject", str(entry_id), req.reason or "", commit=False)
    try:
        return await catalog.serialize_one(db, await catalog.review(db, entry_id, admin.id, approve=False, reason=req.reason))
    except catalog.CatalogError as ex:
        raise HTTPException(status_code=ex.status_code, detail=ex.detail) from ex


class FitmentIn(BaseModel):
    make: Optional[str] = Field(default=None, max_length=50)
    model: str = Field(min_length=1, max_length=80)
    years: Optional[str] = Field(default=None, max_length=20)
    oem_reference: Optional[str] = Field(default=None, max_length=120)     # that make's own part number


class CatalogEdit(BaseModel):
    fitments: Optional[List[FitmentIn]] = Field(default=None, max_length=20)     # every make/model the reference fits; replaces the list
    model: Optional[str] = Field(default=None, max_length=80)
    make: Optional[str] = Field(default=None, max_length=50)
    years: Optional[str] = Field(default=None, max_length=20)
    name: Optional[str] = Field(default=None, max_length=150)
    brand: Optional[str] = Field(default=None, max_length=80)
    reference: Optional[str] = Field(default=None, max_length=120)
    spec: Optional[str] = Field(default=None, max_length=120)
    quantity: Optional[float] = Field(default=None, ge=0, le=100000)
    position: Optional[str] = Field(default=None, max_length=10)
    notes: Optional[str] = Field(default=None, max_length=2000)


class CatalogCreate(BaseModel):
    category: str = Field(min_length=1, max_length=30)
    fitments: List[FitmentIn] = Field(min_length=1, max_length=20)
    name: Optional[str] = Field(default=None, max_length=150)
    brand: Optional[str] = Field(default=None, max_length=80)
    reference: Optional[str] = Field(default=None, max_length=120)
    spec: Optional[str] = Field(default=None, max_length=120)
    quantity: Optional[float] = Field(default=None, ge=0, le=100000)
    position: Optional[str] = Field(default=None, max_length=10)
    notes: Optional[str] = Field(default=None, max_length=2000)


@router.get("/catalog/categories")
async def catalog_categories():
    """Part types the console can add, with the positions and field labels of each one."""
    return [{"key": c["key"], "label": c["label"], "spec_label": c.get("spec_label") or "Especificación", "spec_hint": c.get("spec_hint") or "",
             "qty_label": c.get("qty_label") or "Cantidad", "positions": [{"key": k, "label": POSITIONS[k]} for k in c.get("positions", [])]}
            for c in PART_CATEGORIES]


@router.post("/catalog")
async def catalog_create(req: CatalogCreate, admin: AppUser = Depends(auth.require_admin), db: AsyncSession = Depends(get_db)):
    """Add a part straight to the catalogue, already approved (in online mode it is also sent to that server's queue)."""
    try:
        entry = await catalog.create(db, admin.id, req.model_dump())
    except catalog.CatalogError as ex:
        raise HTTPException(status_code=ex.status_code, detail=ex.detail) from ex
    await admin_audit.log(db, admin, "catalog.create", str(entry.id), f"{entry.category} {entry.brand or ''} {entry.reference or ''}".strip())
    await catalog_remote.maybe_forward(db, entry)
    return await catalog.serialize_one(db, entry)


@router.patch("/catalog/{entry_id}")
async def catalog_edit(entry_id: int, req: CatalogEdit, db: AsyncSession = Depends(get_db)):
    """Complete or correct a proposal before (or after) approving it. Only the fields sent change; "" empties a field."""
    await admin_audit.log(db, None, "catalog.edit", str(entry_id), ", ".join(sorted(req.model_fields_set)), commit=False)
    try:
        return await catalog.serialize_one(db, await catalog.edit(db, entry_id, req.model_dump(exclude_unset=True)))
    except catalog.CatalogError as ex:
        raise HTTPException(status_code=ex.status_code, detail=ex.detail) from ex


@router.get("/catalog/export")
async def catalog_export(db: AsyncSession = Depends(get_db)):
    """Approved entries as versioned JSON: the open catalogue a self-hosted instance can import later (docs/hosted-service.md)."""
    return await catalog.export_approved(db)


# ───────────────────────── online catalogue: keys this server issues, and where this server takes its catalogue from ─────────────────────────
class CatalogKeyCreate(BaseModel):
    note: Optional[str] = Field(default=None, max_length=120)
    days: Optional[int] = Field(default=None, ge=1, le=3650)     # None = the key does not expire


class CatalogPublicUrl(BaseModel):
    public_url: Optional[str] = Field(default=None, max_length=255)


class CatalogSourceIn(BaseModel):
    source: str = Field(pattern="^(own|remote)$")
    link: Optional[str] = Field(default=None, max_length=600)    # None = keep the stored link; "" = remove it


def _remote_http(ex: catalog_remote.RemoteError) -> HTTPException:
    return HTTPException(status_code=ex.status_code, detail=ex.detail)


@router.get("/catalog/keys")
async def catalog_keys(db: AsyncSession = Depends(get_db)):
    return {"public_url": await catalog_remote.get_public_url(db), "keys": await catalog_remote.list_keys(db)}


@router.put("/catalog/keys/public-url")
async def catalog_public_url(req: CatalogPublicUrl, admin: AppUser = Depends(auth.require_admin), db: AsyncSession = Depends(get_db)):
    """The public address of this server, used to compose the links handed to other installations."""
    try:
        url = await catalog_remote.set_public_url(db, req.public_url)
    except catalog_remote.RemoteError as ex:
        raise _remote_http(ex) from ex
    await admin_audit.log(db, admin, "catalog.public_url", url or "")
    return {"public_url": url}


@router.post("/catalog/keys")
async def catalog_key_create(req: CatalogKeyCreate, admin: AppUser = Depends(auth.require_admin), db: AsyncSession = Depends(get_db)):
    """A new key for one installation; the plain key and its link come back once."""
    row, key, link = await catalog_remote.create_key(db, admin.id, req.note, req.days)
    await admin_audit.log(db, admin, "catalog.key.create", str(row.id), req.note or "")
    public = await catalog_remote.get_public_url(db)
    # This server is the default catalogue of every installation: the key alone is enough on the other side
    is_default = bool(public) and public.rstrip("/") == catalog_remote.default_server()
    return {**catalog_remote.serialize_key(row), "key": key, "link": link, "key_alone": is_default}


@router.delete("/catalog/keys/{key_id}")
async def catalog_key_revoke(key_id: int, admin: AppUser = Depends(auth.require_admin), db: AsyncSession = Depends(get_db)):
    if not await catalog_remote.revoke_key(db, key_id):
        raise HTTPException(status_code=404, detail="Clave no encontrada.")
    await admin_audit.log(db, admin, "catalog.key.revoke", str(key_id))
    return {"ok": True}


@router.get("/catalog/source")
async def catalog_source(db: AsyncSession = Depends(get_db)):
    return await catalog_remote.config_state(db)


@router.post("/catalog/source/connect")
async def catalog_source_connect(admin: AppUser = Depends(auth.require_admin), db: AsyncSession = Depends(get_db)):
    """One click: sign up to the project's catalogue and switch to online (services/catalog_remote.connect_default)."""
    try:
        await catalog_remote.connect_default(db)
    except catalog_remote.RemoteError as ex:
        raise _remote_http(ex) from ex
    await admin_audit.log(db, admin, "catalog.connect", catalog_remote.default_server() or "")
    return await catalog_remote.config_state(db)


@router.post("/catalog/source/keep-own")
async def catalog_source_keep_own(admin: AppUser = Depends(auth.require_admin), db: AsyncSession = Depends(get_db)):
    """The administrator answered «only this server's catalogue»: stop asking."""
    await catalog_remote.mark_decided(db)
    await admin_audit.log(db, admin, "catalog.source", "own")
    return await catalog_remote.config_state(db)


@router.put("/catalog/source")
async def catalog_source_set(req: CatalogSourceIn, admin: AppUser = Depends(auth.require_admin), db: AsyncSession = Depends(get_db)):
    """Own catalogue, or the online one behind a link (`https://servidor#clave`, stored encrypted and never returned)."""
    try:
        await catalog_remote.set_config(db, req.source, req.link)
    except catalog_remote.RemoteError as ex:
        raise _remote_http(ex) from ex
    await admin_audit.log(db, admin, "catalog.source", req.source)
    return await catalog_remote.config_state(db)


@router.post("/catalog/source/sync")
async def catalog_source_sync(admin: AppUser = Depends(auth.require_admin), db: AsyncSession = Depends(get_db)):
    """Downloads the online catalogue now and reports how many entries the mirror holds."""
    try:
        result = await catalog_remote.sync(db)
    except catalog_remote.RemoteError as ex:
        raise _remote_http(ex) from ex
    await admin_audit.log(db, admin, "catalog.sync", str(result["entries"]))
    return {**result, **catalog_remote.serialize_config(await catalog_remote.get_config(db))}


# ───────────────────────── anonymous reports (services/telemetry.py, docs/telemetry.md) ─────────────────────────
class TelemetryIn(BaseModel):
    enabled: bool


@router.get("/telemetry")
async def telemetry_state(db: AsyncSession = Depends(get_db)):
    """Whether this server sends its anonymous reports, where to, and how the last one went."""
    return await telemetry.state(db)


@router.put("/telemetry")
async def telemetry_set(req: TelemetryIn, admin: AppUser = Depends(auth.require_admin), db: AsyncSession = Depends(get_db)):
    if req.enabled and not (settings.TELEMETRY_URL or "").strip():
        raise HTTPException(status_code=409, detail="Este servidor no tiene dirección para los informes (TELEMETRY_URL vacía).")
    await telemetry.set_enabled(db, req.enabled)
    await admin_audit.log(db, admin, "telemetry.enabled" if req.enabled else "telemetry.disabled")
    return await telemetry.state(db)


@router.get("/telemetry/preview")
async def telemetry_preview(db: AsyncSession = Depends(get_db)):
    """Exactly the report that would be sent now (also while the reports are off, to decide)."""
    return await telemetry.build_report(db)


@router.post("/telemetry/send")
async def telemetry_send(admin: AppUser = Depends(auth.require_admin), db: AsyncSession = Depends(get_db)):
    if not await telemetry.load_enabled(db):
        raise HTTPException(status_code=409, detail="Activa los informes antes de enviar uno.")
    result = await telemetry.send(db)
    await admin_audit.log(db, admin, "telemetry.send", "ok" if result["ok"] else (result.get("error") or "error"))
    return {**result, "state": await telemetry.state(db)}


@router.post("/telemetry/new-id")
async def telemetry_new_id(admin: AppUser = Depends(auth.require_admin), db: AsyncSession = Depends(get_db)):
    """A new random installation id: the reports from now on cannot be linked to the earlier ones."""
    await telemetry.reset_install_id(db)
    await admin_audit.log(db, admin, "telemetry.new_id")
    return await telemetry.state(db)


@router.get("/telemetry/received")
async def telemetry_received(db: AsyncSession = Depends(get_db)):
    """The project's server only: what the installations have reported."""
    if not settings.TELEMETRY_RECEIVER:
        raise HTTPException(status_code=404, detail="Este servidor no recibe informes.")
    return await telemetry.summary(db)


class ResolveIn(BaseModel):
    resolved: bool = True


@router.post("/telemetry/errors/{group_id}/resolve")
async def telemetry_resolve(group_id: int, req: ResolveIn, admin: AppUser = Depends(auth.require_admin), db: AsyncSession = Depends(get_db)):
    """Marks an error as fixed; it reopens by itself if a newer version reports it again."""
    if not settings.TELEMETRY_RECEIVER:
        raise HTTPException(status_code=404, detail="Este servidor no recibe informes.")
    if not await telemetry.resolve_group(db, group_id, req.resolved):
        raise HTTPException(status_code=404, detail="Error no encontrado.")
    await admin_audit.log(db, admin, "telemetry.resolve" if req.resolved else "telemetry.reopen", str(group_id))
    return {"ok": True}
