"""Local stand-ins for a client's systems, so Switchyard can be demoed and tested with no
vendor access at all.

  /erp/mcp          MCP server, no auth, JSON responses        (GL actuals)
  /essbase/mcp      MCP server, static bearer, SSE responses   (planning cube)
  /servicenow/mcp   MCP server, OAuth client-credentials token (ticketing)
  /oauth/token      client-credentials token endpoint for /servicenow
  /idp/...          a tiny OpenID Connect provider with demo users and groups

Run:  uvicorn mock_servers:app --port 9100
Env:  MOCK_PUBLIC_URL   (what the browser uses,  default http://localhost:9100)
      MOCK_INTERNAL_URL (what Switchyard uses,   default = MOCK_PUBLIC_URL)
      MOCK_REDIRECT_PREFIXES comma list of allowed redirect URI prefixes for the IdP
"""
from __future__ import annotations

import base64
import hashlib
import html
import json
import os
import secrets
import time
import uuid
from urllib.parse import urlencode

from fastapi import FastAPI, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, Response
from joserfc import jwt
from joserfc.jwk import KeySet, RSAKey

PUBLIC = os.getenv("MOCK_PUBLIC_URL", "http://localhost:9100").rstrip("/")
INTERNAL = os.getenv("MOCK_INTERNAL_URL", PUBLIC).rstrip("/")
REDIRECT_PREFIXES = [p.strip() for p in os.getenv(
    "MOCK_REDIRECT_PREFIXES", "http://localhost:8000/,http://localhost:5173/,http://testserver/").split(",") if p]

app = FastAPI(title="Switchyard mock vendor systems")

ESSBASE_TOKEN = "demo-essbase-token"
CC_CLIENTS = {"switchyard": "demo-secret"}
_cc_tokens: dict[str, float] = {}
_sessions: set[str] = set()
TICKETS: list[dict] = []

# --------------------------------------------------------------------------------- data
ACCOUNTS = [("4000 Revenue", 1_250_000, 3.1), ("5000 COGS", -610_000, 7.4), ("6100 Payroll", -240_500, -6.2),
            ("6200 Marketing", -88_250, 1.4), ("6300 Travel", -21_400, 12.9), ("7000 Other income", 15_900, 0.6)]

TOOLS = {
    "erp": [
        {"name": "get_actuals", "title": "Get GL actuals",
         "description": "Return general-ledger actuals for a period and entity.",
         "inputSchema": {"type": "object", "properties": {
             "period": {"type": "string", "description": "e.g. Oct-2026"},
             "entity": {"type": "string", "description": "Legal entity code", "default": "US"}},
             "required": ["period"]},
         "annotations": {"readOnlyHint": True}},
        {"name": "get_entities", "title": "List entities", "description": "List legal entities.",
         "inputSchema": {"type": "object", "properties": {}}, "annotations": {"readOnlyHint": True}},
        {"name": "post_journal", "title": "Post a journal",
         "description": "Post a manual journal entry (destructive).",
         "inputSchema": {"type": "object", "properties": {"lines": {"type": "array"}}, "required": ["lines"]},
         "annotations": {"destructiveHint": True}},
    ],
    "essbase": [
        {"name": "get_cubes", "title": "List cubes", "description": "List applications and cubes.",
         "inputSchema": {"type": "object", "properties": {}}, "annotations": {"readOnlyHint": True}},
        {"name": "load_data", "title": "Load data", "description": "Load rows into a cube.",
         "inputSchema": {"type": "object", "properties": {"cube": {"type": "string"}, "rows": {"type": "array"}},
                         "required": ["cube", "rows"]}},
        {"name": "run_calc", "title": "Run calc script", "description": "Run a named calculation script.",
         "inputSchema": {"type": "object", "properties": {"cube": {"type": "string"}, "script": {"type": "string"}},
                         "required": ["cube", "script"]}},
        {"name": "get_variance", "title": "Variance vs forecast",
         "description": "Accounts whose actual-vs-forecast variance exceeds a threshold.",
         "inputSchema": {"type": "object", "properties": {
             "period": {"type": "string"}, "threshold_pct": {"type": "number", "default": 5}},
             "required": ["period"]},
         "annotations": {"readOnlyHint": True}},
        {"name": "clear_data", "title": "Clear data", "description": "Clear a region of the cube (destructive).",
         "inputSchema": {"type": "object", "properties": {"cube": {"type": "string"}, "region": {"type": "string"}},
                         "required": ["cube", "region"]},
         "annotations": {"destructiveHint": True}},
    ],
    "servicenow": [
        {"name": "create_ticket", "title": "Create ticket", "description": "Open an incident ticket.",
         "inputSchema": {"type": "object", "properties": {
             "title": {"type": "string"}, "body": {"type": "string"},
             "priority": {"type": "integer", "minimum": 1, "maximum": 4, "default": 3}},
             "required": ["title"]}},
        {"name": "get_ticket", "title": "Get ticket", "description": "Look up a ticket by number.",
         "inputSchema": {"type": "object", "properties": {"number": {"type": "string"}}, "required": ["number"]},
         "annotations": {"readOnlyHint": True}},
    ],
}


def _structured(value) -> dict:
    return {"content": [{"type": "text", "text": json.dumps(value)}], "structuredContent": value}


def _tool_error(text: str) -> dict:
    return {"content": [{"type": "text", "text": text}], "isError": True}


def run_tool(server: str, name: str, args: dict) -> dict:
    if server == "erp" and name == "get_actuals":
        if not args.get("period"):
            return _tool_error("period is required")
        entity = args.get("entity") or "US"
        rows = [{"account": a, "entity": entity, "period": args["period"], "amount": amt} for a, amt, _ in ACCOUNTS]
        return _structured({"rows": rows, "row_count": len(rows), "total": sum(r["amount"] for r in rows)})
    if server == "erp" and name == "get_entities":
        return _structured({"entities": ["US", "UK", "IN"]})
    if server == "erp" and name == "post_journal":
        return _structured({"journal_id": f"JE-{secrets.randbelow(99999):05d}", "lines": len(args.get("lines", []))})
    if server == "essbase" and name == "get_cubes":
        return _structured({"cubes": ["Finance.Plan", "Sample.Basic"]})
    if server == "essbase" and name == "load_data":
        rows = args.get("rows") or []
        if not isinstance(rows, list):
            return _tool_error("rows must be an array")
        return _structured({"cube": args.get("cube"), "loaded": len(rows), "rejected": 0})
    if server == "essbase" and name == "run_calc":
        return _structured({"cube": args.get("cube"), "script": args.get("script"), "status": "completed",
                            "elapsed_s": 4.2})
    if server == "essbase" and name == "get_variance":
        thr = float(args.get("threshold_pct", 5))
        breaches = [{"account": a, "variance_pct": v} for a, _, v in ACCOUNTS if abs(v) > thr]
        return _structured({"period": args.get("period"), "threshold_pct": thr, "breaches": breaches})
    if server == "essbase" and name == "clear_data":
        return _structured({"cleared": args.get("region")})
    if server == "servicenow" and name == "create_ticket":
        t = {"number": f"INC{len(TICKETS) + 1001:07d}", "title": args.get("title"), "body": args.get("body", ""),
             "priority": args.get("priority", 3), "state": "New"}
        TICKETS.append(t)
        return _structured(t)
    if server == "servicenow" and name == "get_ticket":
        t = next((t for t in TICKETS if t["number"] == args.get("number")), None)
        return _structured(t) if t else _tool_error("ticket not found")
    raise KeyError(name)


# --------------------------------------------------------------------------------- MCP
def _check_auth(server: str, request: Request) -> None:
    authz = request.headers.get("authorization", "")
    if server == "essbase" and authz != f"Bearer {ESSBASE_TOKEN}":
        raise HTTPException(401, "invalid token", headers={"WWW-Authenticate": "Bearer"})
    if server == "servicenow":
        tok = authz.removeprefix("Bearer ")
        if _cc_tokens.get(tok, 0) < time.time():
            raise HTTPException(401, "invalid or expired token", headers={"WWW-Authenticate": "Bearer"})


@app.post("/{server}/mcp")
async def mcp(server: str, request: Request):
    if server not in TOOLS:
        raise HTTPException(404)
    _check_auth(server, request)
    msg = await request.json()
    method, mid = msg.get("method"), msg.get("id")
    headers = {}
    if method == "initialize":
        sid = uuid.uuid4().hex
        _sessions.add(sid)
        headers["Mcp-Session-Id"] = sid
        result = {"protocolVersion": "2025-06-18", "capabilities": {"tools": {"listChanged": False}},
                  "serverInfo": {"name": f"mock-{server}", "version": "1.0.0"},
                  "instructions": f"Mock {server} server for Switchyard demos."}
    else:
        if request.headers.get("mcp-session-id") not in _sessions:
            return JSONResponse({"jsonrpc": "2.0", "id": mid, "error": {"code": -32000, "message": "Bad session"}},
                                status_code=400)
        if mid is None:  # notification
            return Response(status_code=202)
        if method == "tools/list":
            result = {"tools": TOOLS[server]}
        elif method == "tools/call":
            p = msg.get("params") or {}
            try:
                result = run_tool(server, p.get("name"), p.get("arguments") or {})
            except KeyError:
                return _reply(server, {"jsonrpc": "2.0", "id": mid,
                                       "error": {"code": -32602, "message": f"Unknown tool: {p.get('name')}"}})
        elif method == "ping":
            result = {}
        else:
            return _reply(server, {"jsonrpc": "2.0", "id": mid,
                                   "error": {"code": -32601, "message": f"Method not found: {method}"}})
    return _reply(server, {"jsonrpc": "2.0", "id": mid, "result": result}, headers)


def _reply(server: str, payload: dict, headers: dict | None = None) -> Response:
    if server == "essbase":  # exercise the SSE code path: a progress notification, then the answer
        note = {"jsonrpc": "2.0", "method": "notifications/message", "params": {"level": "info", "data": "working"}}
        body = f"event: message\ndata: {json.dumps(note)}\n\nevent: message\ndata: {json.dumps(payload)}\n\n"
        return Response(body, media_type="text/event-stream", headers=headers)
    return JSONResponse(payload, headers=headers)


@app.delete("/{server}/mcp")
async def end_session(server: str, request: Request):
    _sessions.discard(request.headers.get("mcp-session-id", ""))
    return Response(status_code=204)


@app.post("/oauth/token")
async def cc_token(request: Request):
    form = dict(await request.form())
    cid, secret = form.get("client_id"), form.get("client_secret")
    if authz := request.headers.get("authorization"):
        cid, _, secret = base64.b64decode(authz.removeprefix("Basic ")).decode().partition(":")
    if form.get("grant_type") != "client_credentials" or CC_CLIENTS.get(cid) != secret:
        return JSONResponse({"error": "invalid_client"}, status_code=401)
    tok = secrets.token_urlsafe(24)
    _cc_tokens[tok] = time.time() + 600
    return {"access_token": tok, "token_type": "Bearer", "expires_in": 600, "scope": form.get("scope", "")}


# --------------------------------------------------------------------------------- OIDC IdP
KEY = RSAKey.generate_key(2048, parameters={"kid": "mock-1", "use": "sig", "alg": "RS256"})
OIDC_CLIENTS = {"switchyard": "demo-oidc-secret"}
USERS = {
    "alice": {"name": "Alice Admin", "email": "alice@client.example", "groups": ["sy-admins"]},
    "ben": {"name": "Ben Builder", "email": "ben@client.example", "groups": ["sy-builders"]},
    "olivia": {"name": "Olivia Operator", "email": "olivia@client.example", "groups": ["sy-operators"]},
    "oscar": {"name": "Oscar Operator", "email": "oscar@client.example", "groups": ["sy-operators"]},
    "vera": {"name": "Vera Viewer", "email": "vera@client.example", "groups": ["sy-viewers"]},
    "adam": {"name": "Adam Auditor", "email": "adam@client.example", "groups": ["sy-auditors"]},
}
_codes: dict[str, dict] = {}


@app.get("/idp/.well-known/openid-configuration")
def discovery():
    return {
        "issuer": f"{PUBLIC}/idp",
        "authorization_endpoint": f"{PUBLIC}/idp/authorize",
        "token_endpoint": f"{INTERNAL}/idp/token",
        "jwks_uri": f"{INTERNAL}/idp/jwks",
        "userinfo_endpoint": f"{INTERNAL}/idp/userinfo",
        "end_session_endpoint": f"{PUBLIC}/idp/logout",
        "response_types_supported": ["code"],
        "subject_types_supported": ["public"],
        "id_token_signing_alg_values_supported": ["RS256"],
        "code_challenge_methods_supported": ["S256"],
        "token_endpoint_auth_methods_supported": ["client_secret_basic", "client_secret_post"],
        "scopes_supported": ["openid", "email", "profile"],
    }


@app.get("/idp/jwks")
def jwks():
    return KeySet([KEY]).as_dict(private=False)


def _valid_redirect(uri: str) -> bool:
    return any(uri.startswith(p) for p in REDIRECT_PREFIXES)


@app.get("/idp/authorize", response_class=HTMLResponse)
def authorize(request: Request):
    q = dict(request.query_params)
    if q.get("client_id") not in OIDC_CLIENTS or not _valid_redirect(q.get("redirect_uri", "")):
        return HTMLResponse("<h1>invalid client or redirect_uri</h1>", status_code=400)
    hidden = "".join(f'<input type="hidden" name="{html.escape(k)}" value="{html.escape(v)}">' for k, v in q.items())
    buttons = "".join(
        f'<button name="user" value="{u}"><b>{html.escape(d["name"])}</b><span>{html.escape(d["email"])}'
        f' · {", ".join(d["groups"])}</span></button>' for u, d in USERS.items())
    return f"""<!doctype html><html><head><title>Demo IdP – sign in</title><style>
body{{font-family:system-ui,sans-serif;background:#f3f4f6;display:grid;place-items:center;min-height:100vh;margin:0}}
form{{background:#fff;padding:28px;border-radius:12px;box-shadow:0 2px 12px #0001;width:380px}}
h1{{font-size:18px;margin:0 0 4px}} p{{color:#6b7280;font-size:13px;margin:0 0 16px}}
button{{display:flex;flex-direction:column;align-items:flex-start;width:100%;padding:10px 12px;margin:6px 0;
border:1px solid #e5e7eb;border-radius:8px;background:#fff;cursor:pointer;text-align:left}}
button:hover{{border-color:#2563eb;background:#eff6ff}} span{{color:#6b7280;font-size:12px}}</style></head>
<body><form method="post" action="{PUBLIC}/idp/approve"><h1>Demo identity provider</h1>
<p>Stands in for Entra ID / Okta. Pick a user; their groups map to Switchyard roles.</p>{hidden}{buttons}
</form></body></html>"""


@app.post("/idp/approve")
async def approve(request: Request, user: str = Form(...)):
    q = {k: v for k, v in (await request.form()).items() if k != "user"}
    if user not in USERS or not _valid_redirect(q.get("redirect_uri", "")):
        raise HTTPException(400)
    code = secrets.token_urlsafe(24)
    _codes[code] = {"user": user, **q, "exp": time.time() + 120}
    return RedirectResponse(f"{q['redirect_uri']}?{urlencode({'code': code, 'state': q.get('state', '')})}",
                            status_code=303)


@app.post("/idp/token")
async def token(request: Request):
    form = dict(await request.form())
    cid, secret = form.get("client_id"), form.get("client_secret")
    if authz := request.headers.get("authorization"):
        cid, _, secret = base64.b64decode(authz.removeprefix("Basic ")).decode().partition(":")
    if OIDC_CLIENTS.get(cid) != secret:
        return JSONResponse({"error": "invalid_client"}, status_code=401)
    c = _codes.pop(form.get("code", ""), None)
    if not c or c["exp"] < time.time() or c.get("redirect_uri") != form.get("redirect_uri"):
        return JSONResponse({"error": "invalid_grant"}, status_code=400)
    if c.get("code_challenge"):
        digest = hashlib.sha256(form.get("code_verifier", "").encode()).digest()
        if base64.urlsafe_b64encode(digest).rstrip(b"=").decode() != c["code_challenge"]:
            return JSONResponse({"error": "invalid_grant", "error_description": "PKCE failed"}, status_code=400)
    u = USERS[c["user"]]
    now = int(time.time())
    claims = {"iss": f"{PUBLIC}/idp", "sub": c["user"], "aud": cid, "iat": now, "exp": now + 3600,
              "email": u["email"], "name": u["name"], "groups": u["groups"]}
    if c.get("nonce"):
        claims["nonce"] = c["nonce"]
    id_token = jwt.encode({"alg": "RS256", "kid": "mock-1"}, claims, KEY)
    return {"access_token": secrets.token_urlsafe(24), "token_type": "Bearer", "expires_in": 3600,
            "id_token": id_token, "scope": c.get("scope", "openid")}


@app.get("/idp/userinfo")
def userinfo():
    raise HTTPException(401)


@app.get("/idp/logout")
def idp_logout(post_logout_redirect_uri: str = ""):
    if post_logout_redirect_uri and _valid_redirect(post_logout_redirect_uri.rstrip("/") + "/"):
        return RedirectResponse(post_logout_redirect_uri)
    return HTMLResponse("<p>Signed out of the demo IdP.</p>")


@app.get("/healthz")
def healthz():
    return {"status": "ok"}
