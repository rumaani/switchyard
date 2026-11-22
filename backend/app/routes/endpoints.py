"""Endpoint registry: named vendor MCP servers, their credentials and tool catalogs."""
from __future__ import annotations

import re
import time
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field, field_validator
from sqlalchemy import select
from sqlalchemy.orm import Session

from .. import audit
from ..db import get_db
from ..mcp import service
from ..mcp.auth import AUTH_TYPES
from ..mcp.client import McpError
from ..models import Endpoint
from ..security.crypto import decrypt_json, encrypt_json, mask_config, merge_config
from ..security.rbac import Principal, allowed_tool_patterns, require, tool_allowed
from .auth import client_ip

router = APIRouter(prefix="/api/endpoints", tags=["endpoints"])

NAME_RE = re.compile(r"^[a-z][a-z0-9_-]{1,62}$")


class EndpointIn(BaseModel):
    name: str
    display_name: str = ""
    description: str = ""
    url: str
    auth_type: str = "none"
    auth_config: dict[str, Any] = Field(default_factory=dict)
    extra_headers: dict[str, str] = Field(default_factory=dict)
    enabled: bool = True

    @field_validator("name")
    @classmethod
    def _name(cls, v: str) -> str:
        v = v.strip().lower()
        if not NAME_RE.match(v):
            raise ValueError("2–63 chars: lowercase letters, digits, '-' or '_', starting with a letter")
        return v

    @field_validator("url")
    @classmethod
    def _url(cls, v: str) -> str:
        v = v.strip()
        if not re.match(r"^https?://", v):
            raise ValueError("URL must start with http:// or https://")
        return v

    @field_validator("auth_type")
    @classmethod
    def _auth(cls, v: str) -> str:
        if v not in AUTH_TYPES:
            raise ValueError(f"auth_type must be one of {sorted(AUTH_TYPES)}")
        return v


def _tool_view(t: dict, patterns: list[str]) -> dict:
    ann = t.get("annotations") or {}
    return {
        "name": t.get("name"),
        "title": t.get("title") or ann.get("title") or "",
        "description": t.get("description", ""),
        "inputSchema": t.get("inputSchema") or {"type": "object"},
        "readOnly": bool(ann.get("readOnlyHint")),
        "destructive": bool(ann.get("destructiveHint")) and not ann.get("readOnlyHint"),
        "allowed": tool_allowed(patterns, t.get("name", "")),
    }


def to_json(db: Session, ep: Endpoint, p: Principal, with_tools: bool = False) -> dict:
    patterns = allowed_tool_patterns(db, p, ep)
    cfg = decrypt_json(ep.auth_config_enc) if p.can("endpoints.manage") else {}
    out = {
        "id": ep.id, "name": ep.name, "display_name": ep.display_name or ep.name, "description": ep.description,
        "url": ep.url if p.can("endpoints.manage") else "", "transport": ep.transport,
        "auth_type": ep.auth_type, "auth_config": mask_config(cfg), "extra_headers": ep.extra_headers or {},
        "enabled": ep.enabled, "status": ep.status, "status_detail": ep.status_detail,
        "last_checked_at": ep.last_checked_at, "tool_count": len(ep.tools_cache or []),
        "allowed_tool_count": sum(1 for t in (ep.tools_cache or []) if tool_allowed(patterns, t.get("name", ""))),
        "server": (ep.server_info or {}).get("serverInfo", {}),
    }
    if with_tools:
        out["tools"] = [_tool_view(t, patterns) for t in (ep.tools_cache or [])]
    return out


def _get(db: Session, endpoint_id: int) -> Endpoint:
    ep = db.get(Endpoint, endpoint_id)
    if not ep:
        raise HTTPException(404, "Endpoint not found")
    return ep


async def _refresh(db: Session, ep: Endpoint) -> dict | None:
    ref = service.EndpointRef(ep)
    try:
        result = await service.discover(ref)
        service.apply_discovery(ep, result, None)
        return result
    except (McpError, ValueError, KeyError) as exc:
        service.apply_discovery(ep, None, str(exc))
        return None


@router.get("")
def list_endpoints(db: Session = Depends(get_db), p: Principal = Depends(require("endpoints.view"))):
    eps = db.execute(select(Endpoint).order_by(Endpoint.name)).scalars()
    return [to_json(db, e, p) for e in eps]


@router.get("/auth-types")
def auth_types(_: Principal = Depends(require("endpoints.view"))):
    return AUTH_TYPES


@router.get("/{endpoint_id}")
def get_endpoint(endpoint_id: int, db: Session = Depends(get_db), p: Principal = Depends(require("endpoints.view"))):
    return to_json(db, _get(db, endpoint_id), p, with_tools=True)


@router.post("/test")
async def test_connection(body: EndpointIn, p: Principal = Depends(require("endpoints.manage"))):
    """Try a configuration without saving it."""
    fake = Endpoint(id=0, name=body.name, url=body.url, auth_type=body.auth_type,
                    auth_config_enc=encrypt_json(body.auth_config), extra_headers=body.extra_headers, enabled=True)
    t0 = time.perf_counter()
    try:
        result = await service.discover(service.EndpointRef(fake))
        return {"ok": True, "ms": round((time.perf_counter() - t0) * 1000),
                "server": result["server_info"].get("serverInfo", {}), "tool_count": len(result["tools"]),
                "trace": result["trace"]}
    except (McpError, ValueError, KeyError) as exc:
        return {"ok": False, "error": str(exc)}
    finally:
        service.mcp_auth.invalidate(0)


@router.post("", status_code=201)
async def create_endpoint(body: EndpointIn, request: Request, db: Session = Depends(get_db),
                          p: Principal = Depends(require("endpoints.manage"))):
    if db.execute(select(Endpoint).where(Endpoint.name == body.name)).scalar_one_or_none():
        raise HTTPException(409, f"An endpoint named '{body.name}' already exists")
    ep = Endpoint(name=body.name, display_name=body.display_name, description=body.description, url=body.url,
                  auth_type=body.auth_type, auth_config_enc=encrypt_json(body.auth_config),
                  extra_headers=body.extra_headers, enabled=body.enabled)
    db.add(ep)
    db.flush()
    await _refresh(db, ep)
    audit.record(db, p.email, "endpoint.create", ep.name, detail={"url": ep.url, "auth_type": ep.auth_type},
                 ip=client_ip(request))
    return to_json(db, ep, p, with_tools=True)


@router.put("/{endpoint_id}")
async def update_endpoint(endpoint_id: int, body: EndpointIn, request: Request, db: Session = Depends(get_db),
                          p: Principal = Depends(require("endpoints.manage"))):
    ep = _get(db, endpoint_id)
    clash = db.execute(select(Endpoint).where(Endpoint.name == body.name, Endpoint.id != ep.id)).scalar_one_or_none()
    if clash:
        raise HTTPException(409, f"An endpoint named '{body.name}' already exists")
    old_cfg = decrypt_json(ep.auth_config_enc)
    ep.name, ep.display_name, ep.description, ep.url = body.name, body.display_name, body.description, body.url
    ep.auth_type, ep.extra_headers, ep.enabled = body.auth_type, body.extra_headers, body.enabled
    ep.auth_config_enc = encrypt_json(merge_config(old_cfg, body.auth_config))
    service.mcp_auth.invalidate(ep.id)
    db.flush()
    await _refresh(db, ep)
    audit.record(db, p.email, "endpoint.update", ep.name, detail={"url": ep.url, "auth_type": ep.auth_type,
                 "enabled": ep.enabled}, ip=client_ip(request))
    return to_json(db, ep, p, with_tools=True)


@router.delete("/{endpoint_id}")
def delete_endpoint(endpoint_id: int, request: Request, db: Session = Depends(get_db),
                    p: Principal = Depends(require("endpoints.manage"))):
    ep = _get(db, endpoint_id)
    name = ep.name
    db.delete(ep)
    service.mcp_auth.invalidate(endpoint_id)
    audit.record(db, p.email, "endpoint.delete", name, ip=client_ip(request))
    return {"ok": True}


@router.post("/{endpoint_id}/refresh")
async def refresh(endpoint_id: int, db: Session = Depends(get_db), p: Principal = Depends(require("endpoints.view"))):
    ep = _get(db, endpoint_id)
    await _refresh(db, ep)
    return to_json(db, ep, p, with_tools=True)


class ToolCall(BaseModel):
    arguments: dict[str, Any] = Field(default_factory=dict)


@router.post("/{endpoint_id}/tools/{tool}/call")
async def call_tool(endpoint_id: int, tool: str, body: ToolCall, request: Request, db: Session = Depends(get_db),
                    p: Principal = Depends(require("tools.call"))):
    ep = _get(db, endpoint_id)
    if not tool_allowed(allowed_tool_patterns(db, p, ep), tool):
        audit.record(db, p.email, "tool.call", f"{ep.name}/{tool}", outcome="denied", ip=client_ip(request))
        db.commit()
        raise HTTPException(403, f"Your roles do not grant '{tool}' on '{ep.name}'")
    ref = service.EndpointRef(ep)
    t0 = time.perf_counter()
    try:
        out = await service.call_tool(ref, tool, body.arguments)
    except McpError as exc:
        audit.record(db, p.email, "tool.call", f"{ep.name}/{tool}", outcome="error",
                     detail={"arguments": body.arguments, "error": str(exc)}, ip=client_ip(request))
        return {"ok": False, "error": str(exc), "ms": round((time.perf_counter() - t0) * 1000)}
    audit.record(db, p.email, "tool.call", f"{ep.name}/{tool}", outcome="error" if out["is_error"] else "ok",
                 detail={"arguments": body.arguments}, ip=client_ip(request))
    return {"ok": not out["is_error"], "ms": round((time.perf_counter() - t0) * 1000), **out}
