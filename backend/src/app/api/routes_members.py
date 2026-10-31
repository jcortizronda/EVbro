"""Member accounts: the people a manager (or the admin, in their own space) lets work on their cars, and which cars with which level.

A member can never add cars: only the manager does. A member sees and does what the links `view` < `edit` < `control` allow.
"""
from __future__ import annotations

import asyncio
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import AppUser, DeviceAccess, Vehicle
from app.db.session import get_db
from app.i18n import _
from app.security import auth, passwords
from app.security.access import LEVEL_ORDER
from app.services import tenant

router = APIRouter(tags=["Members"])
LINK_LEVELS = ("view", "edit", "view_control", "control")       # see security/access.py LEVEL_GRANTS


class MemberCreate(BaseModel):
    username: str = Field(min_length=3, max_length=40)
    password: str = Field(max_length=passwords.MAX_LENGTH)
    read_only: bool = False


class MemberUpdate(BaseModel):
    is_active: Optional[bool] = None
    read_only: Optional[bool] = None
    new_password: Optional[str] = Field(default=None, max_length=passwords.MAX_LENGTH)


class LinkIn(BaseModel):
    level: str = Field(max_length=16)


def _member_out(u: AppUser, links: list) -> dict:
    return {"id": u.id, "username": u.username, "is_active": u.is_active, "read_only": u.read_only,
            "created_at": u.created_at.isoformat() if u.created_at else None, "last_login": u.last_login.isoformat() if u.last_login else None,
            "access": links}


async def _links_of(session: AsyncSession, member_ids: list) -> dict:
    out: dict = {m: [] for m in member_ids}
    if not member_ids:
        return out
    rows = (await session.execute(
        select(DeviceAccess.user_id, DeviceAccess.vin, DeviceAccess.level, Vehicle.name)
        .join(Vehicle, Vehicle.vin == DeviceAccess.vin).where(DeviceAccess.user_id.in_(member_ids)))).all()
    for uid, vin, level, name in rows:
        out[uid].append({"vin": vin, "name": name, "level": level})
    return out


@router.get("/members")
async def list_members(user: AppUser = Depends(auth.require_auth), db: AsyncSession = Depends(get_db)):
    members = (await db.execute(select(AppUser).where(AppUser.parent_id == user.id).order_by(AppUser.id))).scalars().all()
    links = await _links_of(db, [m.id for m in members])
    return [_member_out(m, links[m.id]) for m in members]


@router.post("/members")
async def create_member(req: MemberCreate, user: AppUser = Depends(auth.require_auth), db: AsyncSession = Depends(get_db)):
    username = (req.username or "").strip().lower()
    if not 3 <= len(username) <= 40:
        raise HTTPException(status_code=422, detail="El usuario debe tener entre 3 y 40 caracteres.")
    problem = passwords.password_problem(req.password, username)
    if problem:
        raise HTTPException(status_code=422, detail=problem)
    if user.member_limit >= 0:
        count = (await db.execute(select(func.count()).select_from(AppUser).where(AppUser.parent_id == user.id))).scalar_one()
        if count >= user.member_limit:
            raise HTTPException(status_code=409, detail="Has alcanzado el límite de cuentas asociadas.")
    if (await db.execute(select(AppUser.id).where(AppUser.username == username, AppUser.is_admin.is_(False)))).first() is not None:
        raise HTTPException(status_code=409, detail="Ese nombre de usuario ya está en uso.")
    member = AppUser(username=username, password_hash=await asyncio.to_thread(passwords.hash_password, req.password), role="member",
                     parent_id=user.id, read_only=req.read_only, expires_at=user.expires_at)
    db.add(member)
    await db.commit()
    await db.refresh(member)
    return _member_out(member, [])


@router.patch("/members/{member_id}")
async def update_member(member_id: int, req: MemberUpdate, user: AppUser = Depends(auth.require_auth), db: AsyncSession = Depends(get_db)):
    member = await db.get(AppUser, member_id)               # the central rule already checked it is one of the caller's members
    if member is None:
        raise HTTPException(status_code=404, detail="No encontrado.")
    if req.new_password is not None:
        problem = passwords.password_problem(req.new_password, member.username)
        if problem:
            raise HTTPException(status_code=422, detail=problem)
        member.password_hash = await asyncio.to_thread(passwords.hash_password, req.new_password)
    if req.is_active is not None:
        member.is_active = req.is_active
    if req.read_only is not None:
        member.read_only = req.read_only
    await db.commit()
    if req.new_password is not None or req.is_active is False:
        await auth.revoke_user_sessions(db, member.id)
    return _member_out(member, (await _links_of(db, [member.id]))[member.id])


@router.delete("/members/{member_id}")
async def delete_member(member_id: int, db: AsyncSession = Depends(get_db)):
    await tenant.delete_member(db, member_id)
    await db.commit()
    return {"ok": True}


# ───────────────────────── links member <-> car (owner only) ─────────────────────────
@router.get("/vehicles/{vin}/access")
async def list_access(vin: str, db: AsyncSession = Depends(get_db)):
    rows = (await db.execute(select(DeviceAccess.user_id, AppUser.username, DeviceAccess.level)
                             .join(AppUser, AppUser.id == DeviceAccess.user_id).where(DeviceAccess.vin == vin).order_by(AppUser.username))).all()
    return [{"user_id": uid, "username": name, "level": level} for uid, name, level in rows]


async def _target_member(db: AsyncSession, vin: str, user_id: int) -> AppUser:
    """The linked account must be a member of the car's owner: access never crosses manager spaces."""
    owner_id = (await db.execute(select(Vehicle.owner_id).where(Vehicle.vin == vin))).scalar_one_or_none()
    member = await db.get(AppUser, user_id)
    if member is None or member.role != "member" or owner_id is None or member.parent_id != owner_id:
        raise HTTPException(status_code=404, detail="Cuenta asociada no encontrada.")
    return member


@router.put("/vehicles/{vin}/access/{user_id}")
async def set_access(vin: str, user_id: int, req: LinkIn, request: Request, user: AppUser = Depends(auth.require_auth), db: AsyncSession = Depends(get_db)):
    if req.level not in LINK_LEVELS:
        raise HTTPException(status_code=422, detail=_("El nivel debe ser uno de: {p0}.", p0=', '.join(LINK_LEVELS)))
    member = await _target_member(db, vin, user_id)
    link = (await db.execute(select(DeviceAccess).where(DeviceAccess.user_id == member.id, DeviceAccess.vin == vin))).scalar_one_or_none()
    if link is None:
        db.add(DeviceAccess(user_id=member.id, vin=vin, level=req.level, granted_by=user.id))
    else:
        link.level = req.level
    await db.commit()
    return {"user_id": member.id, "vin": vin, "level": req.level, "rank": LEVEL_ORDER[req.level]}


@router.delete("/vehicles/{vin}/access/{user_id}")
async def remove_access(vin: str, user_id: int, db: AsyncSession = Depends(get_db)):
    member = await _target_member(db, vin, user_id)
    link = (await db.execute(select(DeviceAccess).where(DeviceAccess.user_id == member.id, DeviceAccess.vin == vin))).scalar_one_or_none()
    if link is not None:
        await db.delete(link)
        await db.commit()
    return {"ok": True}
