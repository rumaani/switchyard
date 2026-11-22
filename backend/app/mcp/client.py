"""Minimal, dependency-light MCP client for the Streamable HTTP transport.

Speaks JSON-RPC 2.0 over HTTP POST and accepts either a plain JSON response or a
Server-Sent-Events stream (both are allowed by the spec). Handles the Mcp-Session-Id
header, protocol-version negotiation and paginated tools/list.
"""
from __future__ import annotations

import itertools
import json
import time
from typing import Any

import httpx

PROTOCOL_VERSION = "2025-06-18"
CLIENT_INFO = {"name": "switchyard", "version": "0.1.0"}

# Tests (and the local demo) can route all outbound MCP traffic through an in-process app.
transport: httpx.AsyncBaseTransport | None = None


class McpError(Exception):
    def __init__(self, message: str, code: int | None = None, data: Any = None, http_status: int | None = None):
        super().__init__(message)
        self.code, self.data, self.http_status = code, data, http_status


class McpAuthError(McpError):
    """The endpoint rejected our credentials (HTTP 401/403)."""


class McpClient:
    def __init__(self, url: str, headers: dict[str, str] | None = None, timeout: float = 60.0, verify: bool = True):
        self.url = url
        self.headers = dict(headers or {})
        self.timeout = timeout
        self.verify = verify
        self.session_id: str | None = None
        self.protocol_version: str | None = None
        self.server_info: dict[str, Any] = {}
        self.trace: list[dict[str, Any]] = []  # lightweight execution path for the UI
        self._ids = itertools.count(1)
        self._http: httpx.AsyncClient | None = None

    async def __aenter__(self) -> McpClient:
        self._http = httpx.AsyncClient(timeout=self.timeout, verify=self.verify, transport=transport,
                                       follow_redirects=False)
        return self

    async def __aexit__(self, *exc) -> None:
        if self._http is not None:
            if self.session_id:
                try:  # politely end the session; servers may answer 405 (not supported)
                    await self._http.delete(self.url, headers=self._headers())
                except httpx.HTTPError:
                    pass
            await self._http.aclose()
            self._http = None

    # -- protocol -------------------------------------------------------------
    async def initialize(self) -> dict[str, Any]:
        result = await self._rpc("initialize", {
            "protocolVersion": PROTOCOL_VERSION,
            "capabilities": {},
            "clientInfo": CLIENT_INFO,
        })
        self.protocol_version = result.get("protocolVersion") or PROTOCOL_VERSION
        self.server_info = {
            "serverInfo": result.get("serverInfo", {}),
            "protocolVersion": self.protocol_version,
            "capabilities": result.get("capabilities", {}),
            "instructions": result.get("instructions", ""),
        }
        await self._rpc("notifications/initialized", None, notify=True)
        return self.server_info

    async def list_tools(self) -> list[dict[str, Any]]:
        tools: list[dict[str, Any]] = []
        cursor = None
        for _ in range(50):  # hard stop against a server that never ends pagination
            params = {"cursor": cursor} if cursor else {}
            result = await self._rpc("tools/list", params)
            tools.extend(result.get("tools", []))
            cursor = result.get("nextCursor")
            if not cursor:
                break
        return tools

    async def call_tool(self, name: str, arguments: dict[str, Any] | None) -> dict[str, Any]:
        return await self._rpc("tools/call", {"name": name, "arguments": arguments or {}})

    # -- transport ------------------------------------------------------------
    def _headers(self) -> dict[str, str]:
        h = {"Accept": "application/json, text/event-stream", "Content-Type": "application/json", **self.headers}
        if self.session_id:
            h["Mcp-Session-Id"] = self.session_id
        if self.protocol_version:
            h["MCP-Protocol-Version"] = self.protocol_version
        return h

    async def _rpc(self, method: str, params: dict[str, Any] | None, notify: bool = False) -> dict[str, Any]:
        assert self._http is not None, "use 'async with McpClient(...)'"
        msg: dict[str, Any] = {"jsonrpc": "2.0", "method": method}
        if params is not None:
            msg["params"] = params
        req_id = None
        if not notify:
            req_id = next(self._ids)
            msg["id"] = req_id
        t0 = time.perf_counter()
        status = None
        try:
            async with self._http.stream("POST", self.url, headers=self._headers(), json=msg) as resp:
                status = resp.status_code
                if sid := resp.headers.get("mcp-session-id"):
                    self.session_id = sid
                if resp.status_code in (401, 403):
                    body = (await resp.aread()).decode(errors="replace")[:500]
                    raise McpAuthError(f"Endpoint rejected credentials (HTTP {resp.status_code})",
                                       http_status=resp.status_code, data=body)
                if resp.status_code >= 400:
                    body = (await resp.aread()).decode(errors="replace")[:500]
                    raise McpError(f"HTTP {resp.status_code} from endpoint: {body}", http_status=resp.status_code)
                if notify:
                    return {}
                ctype = resp.headers.get("content-type", "")
                if "text/event-stream" in ctype:
                    message = await _read_sse_response(resp, req_id)
                else:
                    message = json.loads(await resp.aread())
        except httpx.TimeoutException as exc:
            raise McpError(f"Timed out calling {method}") from exc
        except httpx.HTTPError as exc:
            raise McpError(f"Could not reach endpoint: {exc}") from exc
        finally:
            self.trace.append({"method": method, "status": status, "ms": round((time.perf_counter() - t0) * 1000)})

        if isinstance(message, list):  # tolerate batch-shaped replies
            message = next((m for m in message if m.get("id") == req_id), {})
        if "error" in message:
            err = message["error"] or {}
            raise McpError(err.get("message", "MCP error"), code=err.get("code"), data=err.get("data"))
        return message.get("result") or {}


async def _read_sse_response(resp: httpx.Response, req_id: int) -> dict[str, Any]:
    """Read SSE events until the JSON-RPC response with our id arrives."""
    data_lines: list[str] = []
    async for line in resp.aiter_lines():
        if line == "":
            if data_lines:
                payload = "\n".join(data_lines)
                data_lines = []
                try:
                    msg = json.loads(payload)
                except json.JSONDecodeError:
                    continue
                if isinstance(msg, dict) and msg.get("id") == req_id and ("result" in msg or "error" in msg):
                    return msg
            continue
        if line.startswith("data:"):
            data_lines.append(line[5:].lstrip())
    if data_lines:  # stream ended without a trailing blank line
        msg = json.loads("\n".join(data_lines))
        if msg.get("id") == req_id:
            return msg
    raise McpError("Stream ended without a response")


def normalize_result(result: dict[str, Any]) -> dict[str, Any]:
    """Turn a tools/call result into {value, is_error, content}.

    value = structuredContent if present, else the text content (parsed as JSON when it is JSON).
    """
    content = result.get("content") or []
    if result.get("structuredContent") is not None:
        value: Any = result["structuredContent"]
    else:
        texts = [c.get("text", "") for c in content if c.get("type") == "text"]
        if len(texts) == 1:
            try:
                value = json.loads(texts[0])
            except (json.JSONDecodeError, TypeError):
                value = texts[0]
        elif texts:
            value = "\n".join(texts)
        else:
            value = content
    return {"value": value, "is_error": bool(result.get("isError")), "content": content}
