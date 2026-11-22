import os
import sys
import tempfile
from pathlib import Path

import httpx
import pytest

ROOT = Path(__file__).resolve().parents[2]
_tmp = tempfile.mkdtemp()
os.environ.update({
    "DATABASE_URL": f"sqlite:///{_tmp}/test.db",
    "RUN_MODE": "inline",
    "AUTH_MODE": "dev",
    "SEED_DEMO": "true",
    "DEMO_MCP_URL": "http://mock",
    "MOCK_PUBLIC_URL": "http://mock",
    "SECRET_KEY": "test-secret-test-secret-test-secret-123",
})
sys.path.insert(0, str(ROOT / "backend"))
sys.path.insert(0, str(ROOT / "mock"))

import mock_servers  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from app.mcp import client as mcp_client  # noqa: E402

mcp_client.transport = httpx.ASGITransport(app=mock_servers.app)

from app.main import app  # noqa: E402

HEADERS = {"X-Requested-With": "switchyard"}


@pytest.fixture(scope="session")
def base_app():
    with TestClient(app):  # runs lifespan: create tables + seed
        yield app


def make_client(base_app) -> TestClient:
    c = TestClient(base_app)
    c.headers.update(HEADERS)
    return c


def login(base_app, who: str, groups: list[str]) -> TestClient:
    c = make_client(base_app)
    r = c.post("/api/auth/dev-login", json={"email": f"{who}@client.example", "groups": groups})
    assert r.status_code == 200, r.text
    return c


@pytest.fixture
def admin(base_app):
    return login(base_app, "alice", ["sy-admins"])


@pytest.fixture
def builder(base_app):
    return login(base_app, "ben", ["sy-builders"])


@pytest.fixture
def operator(base_app):
    return login(base_app, "olivia", ["sy-operators"])


@pytest.fixture
def operator2(base_app):
    return login(base_app, "oscar", ["sy-operators"])


@pytest.fixture
def viewer(base_app):
    return login(base_app, "vera", ["sy-viewers"])


@pytest.fixture
def anon(base_app):
    return make_client(base_app)
