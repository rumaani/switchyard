"""Role-based access control.

A signed-in user's roles = roles mapped from their IdP groups (+ admin if their email is
listed in ADMIN_EMAILS). Roles carry permissions (what screens/actions are allowed) and
endpoint grants (which tools may be called on which endpoint).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from fnmatch import fnmatchcase

from fastapi import Depends, HTTPException, Request
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..config import get_settings
from ..db import get_db
from ..models import Endpoint, EndpointGrant, GroupMapping, Role, User

# Permission catalogue. "*" (admin) implies everything.
PERMISSIONS: dict[str, str] = {
    "endpoints.view": "See registered endpoints and their tools",
    "endpoints.manage": "Add, edit, remove endpoints and their credentials",
    "tools.call": "Call tools directly from the endpoint screen (still limited by endpoint grants)",
    "workflows.view": "See workflows",
    "workflows.run": "Start workflow runs",
    "workflows.manage": "Create, edit, delete workflows",
    "runs.view_all": "See runs started by anyone (otherwise only your own)",
    "runs.approve": "Approve or reject approval steps",
    "access.manage": "Manage roles, group mappings and endpoint grants",
    "audit.view": "Read the audit log",
}

BUILTIN_ROLES: dict[str, tuple[str, list[str]]] = {
    "admin": ("Full control of this deployment", ["*"]),
    "builder": ("Designs endpoints and workflows", [
        "endpoints.view", "endpoints.manage", "tools.call", "workflows.view", "workflows.run",
        "workflows.manage", "runs.view_all"]),
    "operator": ("Runs workflows and approves steps", [
        "endpoints.view", "workflows.view", "workflows.run", "runs.view_all", "runs.approve"]),
    "viewer": ("Read-only", ["endpoints.view", "workflows.view", "runs.view_all"]),
    "auditor": ("Reads the audit log and run history", ["workflows.view", "runs.view_all", "audit.view"]),
}


@dataclass
class Principal:
    user_id: int
    email: str
    name: str
    groups: list[str]
    roles: list[str] = field(default_factory=list)
    role_ids: list[int] = field(default_factory=list)
    permissions: set[str] = field(default_factory=set)

    @property
    def is_admin(self) -> bool:
        return "*" in self.permissions

    def can(self, perm: str) -> bool:
        return self.is_admin or perm in self.permissions


def resolve_roles(db: Session, user: User) -> list[Role]:
    groups = [str(g) for g in (user.groups or [])]
    roles: dict[int, Role] = {}
    if groups:
        rows = db.execute(
            select(Role).join(GroupMapping, GroupMapping.role_id == Role.id).where(GroupMapping.idp_group.in_(groups))
        ).scalars()
        roles.update({r.id: r for r in rows})
    if user.email.lower() in get_settings().admin_email_set:
        admin = db.execute(select(Role).where(Role.name == "admin")).scalar_one_or_none()
        if admin:
            roles[admin.id] = admin
    return sorted(roles.values(), key=lambda r: r.name)


def build_principal(db: Session, user: User) -> Principal:
    roles = resolve_roles(db, user)
    perms: set[str] = set()
    for r in roles:
        perms.update(r.permissions or [])
    return Principal(user_id=user.id, email=user.email, name=user.name, groups=list(user.groups or []),
                     roles=[r.name for r in roles], role_ids=[r.id for r in roles], permissions=perms)


def allowed_tool_patterns(db: Session, p: Principal, endpoint: Endpoint) -> list[str]:
    if p.is_admin:
        return ["*"]
    if not p.role_ids:
        return []
    grants = db.execute(
        select(EndpointGrant).where(EndpointGrant.endpoint_id == endpoint.id, EndpointGrant.role_id.in_(p.role_ids))
    ).scalars()
    patterns: list[str] = []
    for g in grants:
        patterns.extend(g.tools or [])
    return patterns


def tool_allowed(patterns: list[str], tool: str) -> bool:
    return any(fnmatchcase(tool, pat) for pat in patterns)


def can_call_tool(db: Session, p: Principal, endpoint: Endpoint, tool: str) -> bool:
    return tool_allowed(allowed_tool_patterns(db, p, endpoint), tool)


# --- FastAPI dependencies -------------------------------------------------------

def current_principal(request: Request, db: Session = Depends(get_db)) -> Principal:
    uid = request.session.get("uid")
    if not uid:
        raise HTTPException(401, "Not signed in")
    user = db.get(User, uid)
    if not user or user.disabled:
        request.session.clear()
        raise HTTPException(401, "Not signed in")
    return build_principal(db, user)


def require(perm: str):
    def dep(p: Principal = Depends(current_principal)) -> Principal:
        if not p.can(perm):
            raise HTTPException(403, f"Missing permission: {perm}")
        return p

    return dep


def ensure_builtin_roles(db: Session) -> None:
    existing = {r.name for r in db.execute(select(Role)).scalars()}
    for name, (desc, perms) in BUILTIN_ROLES.items():
        if name not in existing:
            db.add(Role(name=name, description=desc, permissions=perms, builtin=True))
    db.flush()
