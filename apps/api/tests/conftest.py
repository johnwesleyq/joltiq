"""Test harness: a real PostgreSQL database (joltiq_test) rebuilt from migrations once per session.

The app under test connects as the restricted `joltiq_app` role, so RLS is exercised exactly as in
production. External services are NOT contacted unless a test is marked `live`; tests that use a
local fixture instead of a live service are marked `mocked`.
"""
from __future__ import annotations

import os
import uuid

os.environ.setdefault("JOLTIQ_ENV", "test")
os.environ.setdefault("JOLTIQ_DATABASE_URL", "postgresql+psycopg://joltiq_app:joltiq_app@localhost:5432/joltiq_test")
os.environ.setdefault("JOLTIQ_MIGRATION_DATABASE_URL", "postgresql+psycopg://postgres:postgres@localhost:5432/joltiq_test")
os.environ.setdefault("JOLTIQ_LOCAL_STORAGE_DIR", "/tmp/joltiq-test-storage")
os.environ.setdefault("JOLTIQ_SMTP_PORT", "2525")
os.environ.setdefault("JOLTIQ_RATE_LIMIT_REGISTER_PER_HOUR", "100000")
os.environ.setdefault("JOLTIQ_REDIS_URL", "redis://localhost:6379/15")

import pytest  # noqa: E402
from alembic import command  # noqa: E402
from alembic.config import Config  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402
from sqlalchemy import create_engine, text  # noqa: E402

HERE = os.path.dirname(__file__)


def _rebuild_db() -> None:
    eng = create_engine(os.environ["JOLTIQ_MIGRATION_DATABASE_URL"])
    with eng.begin() as c:
        c.execute(text("DROP SCHEMA public CASCADE; CREATE SCHEMA public; GRANT ALL ON SCHEMA public TO public;"))
        c.execute(text("CREATE EXTENSION IF NOT EXISTS vector"))
    eng.dispose()
    cfg = Config(os.path.join(HERE, "..", "alembic.ini"))
    cfg.set_main_option("script_location", os.path.join(HERE, "..", "migrations"))
    cfg.attributes["url"] = os.environ["JOLTIQ_MIGRATION_DATABASE_URL"]
    command.upgrade(cfg, "head")


@pytest.fixture(scope="session", autouse=True)
def database():
    _rebuild_db()
    try:
        import redis

        redis.Redis.from_url(os.environ["JOLTIQ_REDIS_URL"]).flushdb()
    except Exception:
        pass
    yield


@pytest.fixture(scope="session")
def app(database):
    from joltiq.main import create_app

    return create_app()


class ApiClient:
    """A TestClient with its own cookie jar that automatically sends the CSRF header."""

    def __init__(self, app):
        self.c = TestClient(app, raise_server_exceptions=False)
        self.csrf: str | None = None
        self.me: dict | None = None

    def _h(self, headers):
        h = dict(headers or {})
        if self.csrf:
            h.setdefault("x-csrf-token", self.csrf)
        return h

    def _capture(self, r):
        if r.headers.get("content-type", "").startswith("application/json"):
            try:
                body = r.json()
            except Exception:
                return r
            if isinstance(body, dict) and "csrf_token" in body:
                self.csrf = body["csrf_token"]
                self.me = body
        return r

    def get(self, url, **kw):
        return self.c.get(url, headers=self._h(kw.pop("headers", None)), **kw)

    def post(self, url, **kw):
        return self._capture(self.c.post(url, headers=self._h(kw.pop("headers", None)), **kw))

    def patch(self, url, **kw):
        return self.c.patch(url, headers=self._h(kw.pop("headers", None)), **kw)

    def put(self, url, **kw):
        return self.c.put(url, headers=self._h(kw.pop("headers", None)), **kw)

    def delete(self, url, **kw):
        return self.c.delete(url, headers=self._h(kw.pop("headers", None)), **kw)

    @property
    def org_id(self) -> str:
        return self.me["active_organization"]["id"]


PASSWORD = "correct-horse-battery-staple"


@pytest.fixture
def new_client(app):
    def make() -> ApiClient:
        return ApiClient(app)

    return make


@pytest.fixture
def owner(new_client):
    """A freshly registered organization owner."""
    return register(new_client(), f"Org {uuid.uuid4().hex[:6]}")


def register(c: ApiClient, org_name: str, email: str | None = None) -> ApiClient:
    email = email or f"owner-{uuid.uuid4().hex[:10]}@example.com"
    r = c.post("/api/v1/auth/register", json={"email": email, "password": PASSWORD, "name": "Owner", "organization_name": org_name})
    assert r.status_code == 201, r.text
    c.email = email
    return c


def invite_and_join(app, admin: ApiClient, role: str, customer_id: str | None = None) -> ApiClient:
    email = f"{role}-{uuid.uuid4().hex[:8]}@example.com"
    r = admin.post("/api/v1/users/invitations", json={"email": email, "role": role, "customer_id": customer_id})
    assert r.status_code == 201, r.text
    token = r.json()["dev_accept_link"].split("token=")[1]
    c = ApiClient(app)
    r = c.post("/api/v1/auth/invitations/accept", json={"token": token, "name": role.title(), "password": PASSWORD})
    assert r.status_code == 200, r.text
    c.email = email
    return c


@pytest.fixture
def org_team(app, owner):
    """Owner + technician + customer (linked to a customer account) in one org."""
    cust = owner.post("/api/v1/customers", json={"name": "Acme Plumbing"}).json()
    tech = invite_and_join(app, owner, "technician")
    customer = invite_and_join(app, owner, "customer", cust["id"])
    return {"owner": owner, "tech": tech, "customer": customer, "customer_account": cust}
