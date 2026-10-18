"""Database-level tenant isolation: proves RLS blocks cross-tenant reads/writes even if application code forgets
a filter. These tests talk to Postgres directly as the restricted `joltiq_app` role."""
import uuid

import pytest
from conftest import register
from sqlalchemy import select, text
from sqlalchemy.exc import DBAPIError, ProgrammingError


def _org_ids(new_client):
    a = register(new_client(), "Tenant A")
    b = register(new_client(), "Tenant B")
    a.post("/api/v1/tickets", json={"subject": "A secret ticket", "description": "A only"})
    b.post("/api/v1/tickets", json={"subject": "B secret ticket", "description": "B only"})
    return uuid.UUID(a.org_id), uuid.UUID(b.org_id)


def test_rls_filters_unfiltered_queries(app, new_client):
    from joltiq.db import session_scope
    from joltiq.models import Ticket

    a, b = _org_ids(new_client)
    with session_scope(a) as db:
        subjects = [t.subject for t in db.execute(select(Ticket)).scalars()]  # deliberately no org filter
    assert "A secret ticket" in subjects and "B secret ticket" not in subjects
    with session_scope(None) as db:
        assert db.execute(select(Ticket)).scalars().all() == []  # no tenant context => nothing visible


def test_rls_blocks_cross_tenant_insert(app, new_client):
    from joltiq.db import session_scope
    from joltiq.models import Customer

    a, b = _org_ids(new_client)
    with pytest.raises((ProgrammingError, DBAPIError)):
        with session_scope(a) as db:
            db.add(Customer(org_id=b, name="smuggled"))
            db.flush()


def test_rls_blocks_cross_tenant_update(app, new_client):
    from joltiq.db import session_scope

    a, b = _org_ids(new_client)
    with session_scope(a) as db:
        n = db.execute(text("update tickets set subject = 'pwned' where org_id = :b"), {"b": b}).rowcount
    assert n == 0


def test_app_role_cannot_bypass_rls_or_tamper_audit(app, new_client):
    from joltiq.db import session_scope

    a, _ = _org_ids(new_client)
    with session_scope(a) as db:
        assert db.execute(text("select rolbypassrls or rolsuper from pg_roles where rolname = current_user")).scalar() is False
    with pytest.raises((ProgrammingError, DBAPIError)):
        with session_scope(a) as db:
            db.execute(text("delete from audit_events"))
    with pytest.raises((ProgrammingError, DBAPIError)):
        with session_scope(a) as db:
            db.execute(text("update audit_events set action = 'x'"))


def test_audit_hash_chain_detects_tampering(app, new_client):
    import os

    from sqlalchemy import create_engine

    from joltiq.db import session_scope
    from joltiq.services import audit

    a, _ = _org_ids(new_client)
    with session_scope(a) as db:
        assert audit.verify_chain(db, a)["ok"] is True
    # Tamper as the database owner (simulating an attacker with direct DB access).
    owner = create_engine(os.environ["JOLTIQ_MIGRATION_DATABASE_URL"])
    with owner.begin() as c:
        c.execute(text("update audit_events set data = '{\"evil\": true}' where id = (select min(id) from audit_events where org_id = :o)"), {"o": a})
    owner.dispose()
    with session_scope(a) as db:
        res = audit.verify_chain(db, a)
    assert res["ok"] is False
