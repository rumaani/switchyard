"""Sign-in: enterprise OIDC SSO (Entra ID, Okta, Ping, Keycloak, ...) or a dev-only picker.

SAML-only identity providers are supported by putting Keycloak (or Entra/Okta) in front
as an OIDC broker; see docs/SSO_SETUP.md.
"""
from __future__ import annotations

import logging

from authlib.integrations.starlette_client import OAuth, OAuthError
from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import RedirectResponse
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from .. import audit
from ..config import get_settings
from ..db import get_db
from ..models import User, utcnow
from ..security.rbac import PERMISSIONS, Principal, build_principal, current_principal

log = logging.getLogger("switchyard.auth")
router = APIRouter()

_oauth: OAuth | None = None
oidc_transport = None  # tests route IdP back-channel calls through an in-process app


def oauth() -> OAuth:
    global _oauth
    if _oauth is None:
        s = get_settings()
        if not (s.oidc_discovery_url and s.oidc_client_id):
            raise HTTPException(500, "OIDC is not configured (OIDC_DISCOVERY_URL / OIDC_CLIENT_ID)")
        o = OAuth()
        kwargs = {"scope": s.oidc_scopes, "code_challenge_method": "S256"}
        if oidc_transport is not None:
            kwargs["transport"] = oidc_transport
        o.register(
            name="sso",
            server_metadata_url=s.oidc_discovery_url,
            client_id=s.oidc_client_id,
            client_secret=s.oidc_client_secret or None,
            client_kwargs=kwargs,
        )
        _oauth = o
    return _oauth


def client_ip(request: Request) -> str:
    fwd = request.headers.get("x-forwarded-for")
    return fwd.split(",")[0].strip() if fwd else (request.client.host if request.client else "")


def upsert_user(db: Session, subject: str, email: str, name: str, groups: list[str]) -> User:
    user = db.execute(select(User).where(User.subject == subject)).scalar_one_or_none()
    if user is None:
        user = User(subject=subject, email=email, name=name)
        db.add(user)
    user.email, user.name, user.groups, user.last_login_at = email, name or email, groups, utcnow()
    db.flush()
    return user


def principal_json(p: Principal) -> dict:
    perms = sorted(PERMISSIONS) if p.is_admin else sorted(p.permissions)
    return {"id": p.user_id, "email": p.email, "name": p.name, "groups": p.groups, "roles": p.roles,
            "permissions": perms, "is_admin": p.is_admin}


@router.get("/api/auth/config")
def auth_config():
    s = get_settings()
    return {"mode": s.auth_mode, "provider_name": s.oidc_provider_name, "app_name": s.app_name,
            "environment": s.environment}


@router.get("/api/auth/me")
def me(p: Principal = Depends(current_principal)):
    return principal_json(p)


class DevLogin(BaseModel):
    email: str = Field(min_length=3, max_length=320)
    name: str = ""
    groups: list[str] = []


@router.post("/api/auth/dev-login")
def dev_login(body: DevLogin, request: Request, db: Session = Depends(get_db)):
    s = get_settings()
    if s.auth_mode != "dev" or s.is_production:
        raise HTTPException(404, "Not available")
    email = body.email.strip().lower()
    user = upsert_user(db, f"dev:{email}", email, body.name.strip() or email.split("@")[0], body.groups)
    request.session.clear()
    request.session["uid"] = user.id
    audit.record(db, email, "auth.login", "dev", detail={"groups": body.groups}, ip=client_ip(request))
    return principal_json(build_principal(db, user))


@router.get("/auth/login")
async def sso_login(request: Request):
    s = get_settings()
    if s.auth_mode != "oidc":
        return RedirectResponse("/")
    redirect_uri = s.public_url.rstrip("/") + "/auth/callback"
    return await oauth().sso.authorize_redirect(request, redirect_uri)


@router.get("/auth/callback")
async def sso_callback(request: Request, db: Session = Depends(get_db)):
    s = get_settings()
    try:
        token = await oauth().sso.authorize_access_token(request)  # validates id_token (sig, iss, aud, nonce)
    except OAuthError as exc:
        audit.record(db, "anonymous", "auth.login", "oidc", outcome="error",
                     detail={"error": exc.error, "description": exc.description}, ip=client_ip(request))
        return RedirectResponse(f"/?login_error={exc.error}")
    claims = dict(token.get("userinfo") or {})
    if s.oidc_groups_claim not in claims and token.get("access_token"):
        try:  # some IdPs only release groups from the userinfo endpoint
            claims.update(dict(await oauth().sso.userinfo(token=token)))
        except Exception:  # noqa: BLE001 - optional enrichment
            log.warning("userinfo lookup failed", exc_info=True)
    raw_groups = claims.get(s.oidc_groups_claim) or []
    groups = [str(g) for g in (raw_groups if isinstance(raw_groups, list) else [raw_groups])]
    email = (claims.get("email") or claims.get("preferred_username") or claims.get("upn") or claims["sub"]).lower()
    user = upsert_user(db, f"oidc:{claims['iss']}|{claims['sub']}", email, claims.get("name", ""), groups)
    if user.disabled:
        audit.record(db, email, "auth.login", "oidc", outcome="denied", detail={"reason": "disabled"})
        return RedirectResponse("/?login_error=disabled")
    request.session.clear()
    request.session["uid"] = user.id
    request.session["id_token"] = token.get("id_token", "")[:4096]
    audit.record(db, email, "auth.login", "oidc", detail={"groups": groups}, ip=client_ip(request))
    return RedirectResponse("/")


@router.post("/api/auth/logout")
async def logout(request: Request, db: Session = Depends(get_db)):
    s = get_settings()
    uid = request.session.get("uid")
    id_token = request.session.get("id_token", "")
    request.session.clear()
    if uid and (u := db.get(User, uid)):
        audit.record(db, u.email, "auth.logout", ip=client_ip(request))
    logout_url = None
    if s.auth_mode == "oidc":
        try:
            meta = await oauth().sso.load_server_metadata()
            end = meta.get("end_session_endpoint")
            if end:
                from urllib.parse import urlencode

                params = {"id_token_hint": id_token, "post_logout_redirect_uri": s.public_url}
                logout_url = end + "?" + urlencode(params)
        except Exception:  # noqa: BLE001
            log.warning("could not build IdP logout URL", exc_info=True)
    return {"ok": True, "logout_url": logout_url}
