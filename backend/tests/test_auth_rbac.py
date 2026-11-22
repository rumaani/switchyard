from urllib.parse import parse_qs, urlparse

import mock_servers
from conftest import HEADERS
from fastapi.testclient import TestClient

from app.config import get_settings
from app.routes import auth as auth_routes


def test_requires_sign_in(anon):
    assert anon.get("/api/endpoints").status_code == 401
    assert anon.get("/api/auth/me").status_code == 401


def test_csrf_header_required(base_app):
    c = TestClient(base_app)
    r = c.post("/api/auth/dev-login", json={"email": "x@y.z"})
    assert r.status_code == 403


def test_group_mapping_resolves_roles(operator, admin, viewer):
    me = operator.get("/api/auth/me").json()
    assert me["roles"] == ["operator"]
    assert "runs.approve" in me["permissions"] and "endpoints.manage" not in me["permissions"]
    assert admin.get("/api/auth/me").json()["is_admin"] is True
    assert viewer.get("/api/auth/me").json()["roles"] == ["viewer"]


def test_unmapped_user_has_no_access(base_app):
    from conftest import login

    c = login(base_app, "nobody", ["some-other-group"])
    assert c.get("/api/auth/me").json()["roles"] == []
    assert c.get("/api/endpoints").status_code == 403


def test_permission_enforced(viewer, operator):
    assert viewer.post("/api/access/roles", json={"name": "x"}).status_code == 403
    assert operator.get("/api/audit").status_code == 403


def test_admin_manages_roles_and_mappings(admin, base_app):
    r = admin.post("/api/access/roles", json={"name": "fpa-analyst", "permissions": ["workflows.view",
                                                                                    "workflows.run"]})
    assert r.status_code == 201, r.text
    role_id = r.json()["id"]
    assert admin.post("/api/access/mappings", json={"idp_group": "FPA-Team", "role_id": role_id}).status_code == 201
    from conftest import login

    c = login(base_app, "fiona", ["FPA-Team"])
    assert c.get("/api/auth/me").json()["roles"] == ["fpa-analyst"]
    # built-in admin role cannot lose full control
    admin_role = next(x for x in admin.get("/api/access/roles").json() if x["name"] == "admin")
    r = admin.put(f"/api/access/roles/{admin_role['id']}", json={"name": "admin", "permissions": []})
    assert r.status_code == 400
    acts = [e["action"] for e in admin.get("/api/audit").json()]
    assert "role.create" in acts and "mapping.create" in acts


def test_oidc_sign_in_flow(base_app):
    """Full authorization-code + PKCE flow against the mock IdP, incl. id_token validation."""
    s = get_settings()
    saved = (s.auth_mode, s.oidc_discovery_url, s.oidc_client_id, s.oidc_client_secret, s.public_url)
    s.auth_mode, s.public_url = "oidc", "http://testserver"
    s.oidc_discovery_url = "http://mock/idp/.well-known/openid-configuration"
    s.oidc_client_id, s.oidc_client_secret = "switchyard", "demo-oidc-secret"
    auth_routes._oauth = None
    auth_routes.oidc_transport = _asgi_transport_for_authlib()
    try:
        c = TestClient(base_app, follow_redirects=False)
        c.headers.update(HEADERS)
        r = c.get("/auth/login")
        assert r.status_code == 302
        loc = urlparse(r.headers["location"])
        assert loc.path == "/idp/authorize"
        q = {k: v[0] for k, v in parse_qs(loc.query).items()}
        assert q["code_challenge_method"] == "S256" and q["nonce"]

        idp = TestClient(mock_servers.app, base_url="http://mock", follow_redirects=False)
        r = idp.post("/idp/approve", data={**q, "user": "olivia"})
        assert r.status_code == 303
        cb = urlparse(r.headers["location"])
        r = c.get(f"{cb.path}?{cb.query}")
        assert r.status_code in (302, 307) and r.headers["location"] == "/", r.text
        me = c.get("/api/auth/me").json()
        assert me["email"] == "olivia@client.example" and me["roles"] == ["operator"]

        out = c.post("/api/auth/logout").json()
        assert out["logout_url"].startswith("http://mock/idp/logout")
        assert c.get("/api/auth/me").status_code == 401
    finally:
        s.auth_mode, s.oidc_discovery_url, s.oidc_client_id, s.oidc_client_secret, s.public_url = saved
        auth_routes._oauth = None
        auth_routes.oidc_transport = None


def _asgi_transport_for_authlib():
    """Authlib's httpx integration may be built on `httpx` or on the `httpx2` fork depending on
    the installed version; hand it a transport from whichever one it uses."""
    from authlib.integrations import httpx_client

    lib = next(c.__module__.split(".")[0] for c in httpx_client.AsyncOAuth2Client.__mro__
               if c.__module__.split(".")[0] in ("httpx", "httpx2"))
    mod = __import__(lib)
    return mod.ASGITransport(app=mock_servers.app)
