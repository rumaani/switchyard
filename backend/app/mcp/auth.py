"""Auth adapters: turn an endpoint's stored auth config into request headers.

Vendors implement MCP auth inconsistently, so each endpoint picks one adapter:

  none                      - no credentials (internal / demo servers)
  bearer                    - static bearer token (PAT, long-lived service token)
  api_key                   - static key in a named header (default X-API-Key)
  oauth_client_credentials  - OAuth 2.1 client credentials against the vendor's token endpoint
                              (OCI identity domains, Entra app-only, Okta service apps, ...)

Per-user delegated OAuth (auth code + PKCE per user, on-behalf-of) is on the roadmap; see
docs/ARCHITECTURE.md.
"""
from __future__ import annotations

import time
from typing import Any

import httpx

from . import client as mcp_client

AUTH_TYPES = {
    "none": [],
    "bearer": ["token"],
    "api_key": ["header_name", "api_key"],
    "oauth_client_credentials": ["token_url", "client_id", "client_secret", "scope", "audience", "auth_method"],
}

# (endpoint_id) -> (access_token, expires_at)
_token_cache: dict[int, tuple[str, float]] = {}


def invalidate(endpoint_id: int) -> None:
    _token_cache.pop(endpoint_id, None)


async def auth_headers(endpoint_id: int, auth_type: str, cfg: dict[str, Any], verify: bool = True) -> dict[str, str]:
    if auth_type == "none":
        return {}
    if auth_type == "bearer":
        return {"Authorization": f"Bearer {cfg.get('token', '')}"}
    if auth_type == "api_key":
        return {cfg.get("header_name") or "X-API-Key": cfg.get("api_key", "")}
    if auth_type == "oauth_client_credentials":
        token = await _client_credentials_token(endpoint_id, cfg, verify)
        return {"Authorization": f"Bearer {token}"}
    raise ValueError(f"Unknown auth type: {auth_type}")


async def _client_credentials_token(endpoint_id: int, cfg: dict[str, Any], verify: bool) -> str:
    cached = _token_cache.get(endpoint_id)
    if cached and cached[1] - 60 > time.time():
        return cached[0]
    data = {"grant_type": "client_credentials"}
    if cfg.get("scope"):
        data["scope"] = cfg["scope"]
    if cfg.get("audience"):
        data["resource"] = cfg["audience"]  # RFC 8707 resource indicator
    auth = None
    if (cfg.get("auth_method") or "client_secret_basic") == "client_secret_post":
        data.update(client_id=cfg.get("client_id", ""), client_secret=cfg.get("client_secret", ""))
    else:
        auth = (cfg.get("client_id", ""), cfg.get("client_secret", ""))
    async with httpx.AsyncClient(timeout=30, verify=verify, transport=mcp_client.transport) as http:
        resp = await http.post(cfg["token_url"], data=data, auth=auth, headers={"Accept": "application/json"})
    if resp.status_code != 200:
        raise mcp_client.McpAuthError(f"Token request failed (HTTP {resp.status_code}): {resp.text[:300]}",
                                      http_status=resp.status_code)
    body = resp.json()
    token = body["access_token"]
    _token_cache[endpoint_id] = (token, time.time() + int(body.get("expires_in", 300)))
    return token
