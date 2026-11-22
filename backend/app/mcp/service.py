"""High-level operations on a registered endpoint: discover tools, call a tool."""
from __future__ import annotations

from typing import Any

from ..config import get_settings
from ..models import Endpoint, utcnow
from ..security.crypto import decrypt_json
from . import auth as mcp_auth
from .client import McpAuthError, McpClient, McpError, normalize_result


async def _open(endpoint_id: int, url: str, auth_type: str, cfg: dict, extra: dict) -> McpClient:
    s = get_settings()
    headers = {**(extra or {}), **await mcp_auth.auth_headers(endpoint_id, auth_type, cfg, s.mcp_verify_tls)}
    return McpClient(url, headers=headers, timeout=s.mcp_timeout_s, verify=s.mcp_verify_tls)


class EndpointRef:
    """Detached snapshot of an endpoint so network calls never hold a DB session open."""

    def __init__(self, ep: Endpoint):
        self.id, self.name, self.url = ep.id, ep.name, ep.url
        self.auth_type, self.extra_headers = ep.auth_type, dict(ep.extra_headers or {})
        self.cfg = decrypt_json(ep.auth_config_enc)
        self.enabled = ep.enabled


async def _with_retry_on_auth(ref: EndpointRef, fn):
    """Run fn(client) in a fresh session; if a cached OAuth token was rejected, refresh once."""
    for attempt in (1, 2):
        client = await _open(ref.id, ref.url, ref.auth_type, ref.cfg, ref.extra_headers)
        try:
            async with client:
                await client.initialize()
                return await fn(client), client
        except McpAuthError:
            if attempt == 1 and ref.auth_type == "oauth_client_credentials":
                mcp_auth.invalidate(ref.id)
                continue
            raise
    raise McpError("unreachable")


async def discover(ref: EndpointRef) -> dict[str, Any]:
    tools, client = await _with_retry_on_auth(ref, lambda c: c.list_tools())
    return {"server_info": client.server_info, "tools": tools, "trace": client.trace}


async def call_tool(ref: EndpointRef, tool: str, arguments: dict[str, Any] | None) -> dict[str, Any]:
    if not ref.enabled:
        raise McpError(f"Endpoint '{ref.name}' is disabled")

    async def _call(c: McpClient):
        return await c.call_tool(tool, arguments)

    raw, client = await _with_retry_on_auth(ref, _call)
    out = normalize_result(raw)
    out["trace"] = client.trace
    return out


def apply_discovery(ep: Endpoint, result: dict[str, Any] | None, error: str | None) -> None:
    ep.last_checked_at = utcnow()
    if error:
        ep.status, ep.status_detail = "error", error[:2000]
        return
    ep.status, ep.status_detail = "ok", ""
    ep.tools_cache = result["tools"]
    ep.server_info = result["server_info"]
