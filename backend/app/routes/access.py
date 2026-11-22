"""Access management: roles, IdP group → role mappings, endpoint grants, users, audit log."""
from __future__ import annotations

import csv
import io
import re
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field, field_validator
from sqlalchemy import select
from sqlalchemy.orm import Session

from .. import audit
from ..db import get_db
from ..models import AuditEvent, Endpoint, EndpointGrant, GroupMapping, Role, User
from ..security.rbac import PERMISSIONS, Principal, require, resolve_roles
from .auth import client_ip

router = APIRouter(prefix="/api", tags=["access"])
manage = require("access.manage")


def role_json(r: Role) -> dict:
    return {"id": r.id, "name": r.name, "description": r.description, "permissions": r.permissions or [],
            "builtin": r.builtin,
            "grants": [{"id": g.id, "endpoint_id": g.endpoint_id, "endpoint": g.endpoint.name, "tools": g.tools}
                       for g in r.grants]}


@router.get("/access/permissions")
def permissions(_: Principal = Depends(manage)):
    return PERMISSIONS


@router.get("/access/roles")
def list_roles(db: Session = Depends(get_db), _: Principal = Depends(require("workflows.view"))):
    return [role_json(r) for r in db.execute(select(Role).order_by(Role.name)).scalars()]


class RoleIn(BaseModel):
    name: str
    description: str = ""
    permissions: list[str] = Field(default_factory=list)

    @field_validator("name")
    @classmethod
    def _n(cls, v: str) -> str:
        v = v.strip().lower()
        if not re.match(r"^[a-z][a-z0-9_.-]{1,63}$", v):
            raise ValueError("lowercase letters, digits, '.', '-' or '_'")
        return v

    @field_validator("permissions")
    @classmethod
    def _p(cls, v: list[str]) -> list[str]:
        bad = [x for x in v if x != "*" and x not in PERMISSIONS]
        if bad:
            raise ValueError(f"unknown permissions: {bad}")
        return sorted(set(v))


@router.post("/access/roles", status_code=201)
def create_role(body: RoleIn, request: Request, db: Session = Depends(get_db), p: Principal = Depends(manage)):
    if db.execute(select(Role).where(Role.name == body.name)).scalar_one_or_none():
        raise HTTPException(409, "Role exists")
    r = Role(name=body.name, description=body.description, permissions=body.permissions)
    db.add(r)
    db.flush()
    audit.record(db, p.email, "role.create", r.name, detail={"permissions": r.permissions}, ip=client_ip(request))
    return role_json(r)


@router.put("/access/roles/{role_id}")
def update_role(role_id: int, body: RoleIn, request: Request, db: Session = Depends(get_db),
                p: Principal = Depends(manage)):
    r = db.get(Role, role_id) or _404()
    if r.name == "admin" and "*" not in body.permissions:
        raise HTTPException(400, "The admin role must keep full control")
    before = list(r.permissions or [])
    if not r.builtin:
        r.name = body.name
    r.description, r.permissions = body.description, body.permissions
    audit.record(db, p.email, "role.update", r.name, detail={"before": before, "after": r.permissions},
                 ip=client_ip(request))
    return role_json(r)


@router.delete("/access/roles/{role_id}")
def delete_role(role_id: int, request: Request, db: Session = Depends(get_db), p: Principal = Depends(manage)):
    r = db.get(Role, role_id) or _404()
    if r.builtin:
        raise HTTPException(400, "Built-in roles cannot be deleted")
    audit.record(db, p.email, "role.delete", r.name, ip=client_ip(request))
    db.delete(r)
    return {"ok": True}


# --- group mappings ----------------------------------------------------------------

class MappingIn(BaseModel):
    idp_group: str = Field(min_length=1, max_length=255)
    role_id: int


@router.get("/access/mappings")
def list_mappings(db: Session = Depends(get_db), _: Principal = Depends(manage)):
    rows = db.execute(select(GroupMapping).order_by(GroupMapping.idp_group)).scalars()
    return [{"id": m.id, "idp_group": m.idp_group, "role_id": m.role_id, "role": m.role.name} for m in rows]


@router.post("/access/mappings", status_code=201)
def add_mapping(body: MappingIn, request: Request, db: Session = Depends(get_db), p: Principal = Depends(manage)):
    role = db.get(Role, body.role_id) or _404()
    group = body.idp_group.strip()
    exists = db.execute(select(GroupMapping).where(GroupMapping.idp_group == group,
                                                   GroupMapping.role_id == role.id)).scalar_one_or_none()
    if exists:
        raise HTTPException(409, "Mapping exists")
    m = GroupMapping(idp_group=group, role_id=role.id)
    db.add(m)
    db.flush()
    audit.record(db, p.email, "mapping.create", f"{group} -> {role.name}", ip=client_ip(request))
    return {"id": m.id, "idp_group": m.idp_group, "role_id": role.id, "role": role.name}


@router.delete("/access/mappings/{mapping_id}")
def delete_mapping(mapping_id: int, request: Request, db: Session = Depends(get_db), p: Principal = Depends(manage)):
    m = db.get(GroupMapping, mapping_id) or _404()
    audit.record(db, p.email, "mapping.delete", f"{m.idp_group} -> {m.role.name}", ip=client_ip(request))
    db.delete(m)
    return {"ok": True}


# --- endpoint grants ---------------------------------------------------------------

class GrantIn(BaseModel):
    role_id: int
    endpoint_id: int
    tools: list[str] = Field(default_factory=lambda: ["*"])


@router.put("/access/grants")
def upsert_grant(body: GrantIn, request: Request, db: Session = Depends(get_db), p: Principal = Depends(manage)):
    role = db.get(Role, body.role_id) or _404()
    ep = db.get(Endpoint, body.endpoint_id) or _404()
    tools = [t.strip() for t in body.tools if t.strip()]
    g = db.execute(select(EndpointGrant).where(EndpointGrant.role_id == role.id,
                                               EndpointGrant.endpoint_id == ep.id)).scalar_one_or_none()
    if not tools:
        if g:
            db.delete(g)
        audit.record(db, p.email, "grant.delete", f"{role.name} @ {ep.name}", ip=client_ip(request))
        return {"ok": True, "deleted": True}
    if g is None:
        g = EndpointGrant(role_id=role.id, endpoint_id=ep.id)
        db.add(g)
    g.tools = tools
    audit.record(db, p.email, "grant.upsert", f"{role.name} @ {ep.name}", detail={"tools": tools},
                 ip=client_ip(request))
    return {"ok": True, "tools": tools}


# --- users -------------------------------------------------------------------------

@router.get("/access/users")
def list_users(db: Session = Depends(get_db), _: Principal = Depends(manage)):
    out = []
    for u in db.execute(select(User).order_by(User.email)).scalars():
        out.append({"id": u.id, "email": u.email, "name": u.name, "groups": u.groups or [], "disabled": u.disabled,
                    "roles": [r.name for r in resolve_roles(db, u)], "last_login_at": u.last_login_at})
    return out


class UserPatch(BaseModel):
    disabled: bool


@router.patch("/access/users/{user_id}")
def patch_user(user_id: int, body: UserPatch, request: Request, db: Session = Depends(get_db),
               p: Principal = Depends(manage)):
    u = db.get(User, user_id) or _404()
    if u.id == p.user_id and body.disabled:
        raise HTTPException(400, "You cannot disable yourself")
    u.disabled = body.disabled
    audit.record(db, p.email, "user.disable" if body.disabled else "user.enable", u.email, ip=client_ip(request))
    return {"ok": True}


# --- audit -------------------------------------------------------------------------

def _audit_query(actor: str | None, action: str | None, outcome: str | None, since: datetime | None):
    q = select(AuditEvent).order_by(AuditEvent.id.desc())
    if actor:
        q = q.where(AuditEvent.actor.ilike(f"%{actor}%"))
    if action:
        q = q.where(AuditEvent.action.ilike(f"{action}%"))
    if outcome:
        q = q.where(AuditEvent.outcome == outcome)
    if since:
        q = q.where(AuditEvent.ts >= since)
    return q


@router.get("/audit")
def list_audit(db: Session = Depends(get_db), _: Principal = Depends(require("audit.view")),
               actor: str | None = None, action: str | None = None, outcome: str | None = None,
               since: datetime | None = None, limit: int = Query(200, le=1000), before_id: int | None = None):
    q = _audit_query(actor, action, outcome, since).limit(limit)
    if before_id:
        q = q.where(AuditEvent.id < before_id)
    return [{"id": e.id, "ts": e.ts, "actor": e.actor, "action": e.action, "target": e.target,
             "outcome": e.outcome, "detail": e.detail, "ip": e.ip} for e in db.execute(q).scalars()]


@router.get("/audit/export.csv")
def export_audit(db: Session = Depends(get_db), p: Principal = Depends(require("audit.view")),
                 actor: str | None = None, action: str | None = None, outcome: str | None = None,
                 since: datetime | None = None):
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(["id", "timestamp_utc", "actor", "action", "target", "outcome", "ip", "detail"])
    for e in db.execute(_audit_query(actor, action, outcome, since).limit(100_000)).scalars():
        w.writerow([e.id, e.ts.isoformat() if e.ts else "", e.actor, e.action, e.target, e.outcome, e.ip, e.detail])
    audit.record(db, p.email, "audit.export")
    return StreamingResponse(iter([buf.getvalue()]), media_type="text/csv",
                             headers={"Content-Disposition": "attachment; filename=switchyard-audit.csv"})


def _404():
    raise HTTPException(404, "Not found")
