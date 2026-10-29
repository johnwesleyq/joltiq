"""V7 schema port: linear migration head, backward-compatible defaults, and tenant isolation."""
from __future__ import annotations

import os
import uuid

import pytest
from alembic import command
from alembic.config import Config
from alembic.script import ScriptDirectory
from sqlalchemy import create_engine, select, text
from sqlalchemy.exc import IntegrityError


def _alembic_config() -> Config:
    here = os.path.dirname(__file__)
    cfg = Config(os.path.join(here, "..", "alembic.ini"))
    cfg.set_main_option("script_location", os.path.join(here, "..", "migrations"))
    cfg.attributes["url"] = os.environ["JOLTIQ_MIGRATION_DATABASE_URL"]
    return cfg


def _v7_classes():
    import joltiq.models as models

    required = ("NotificationDispatch",)
    missing = [name for name in required if not hasattr(models, name)]
    assert not missing, f"missing V7 persistence models: {', '.join(missing)}"
    return tuple(getattr(models, name) for name in required)


def test_v7_migrations_follow_oidc_head():
    script = ScriptDirectory.from_config(_alembic_config())
    expected = {
        "0010": "0009",
        "0011": "0010",
        "0012": "0011",
        "0013": "0012",
    }
    for revision, parent in expected.items():
        node = script.get_revision(revision)
        assert node is not None, f"missing migration {revision}"
        assert node.down_revision == parent
    assert script.get_current_head() == "0013"


def test_v7_upgrade_preserves_existing_oidc_device_integration_and_ticket_rows(app, owner):
    from joltiq.db import session_scope
    from joltiq.models import Device, Integration, OidcProvider, Ticket

    org_id = uuid.UUID(owner.org_id)
    suffix = uuid.uuid4().hex[:10]
    with session_scope(org_id) as db:
        provider = OidcProvider(
            org_id=org_id,
            display_name="Upgrade provider",
            kind="entra",
            issuer=f"https://login.microsoftonline.com/{suffix}/v2.0",
            client_id=f"client-{suffix}",
            scopes="openid email profile",
            default_role="technician",
        )
        device = Device(org_id=org_id, hostname=f"upgrade-{suffix}")
        integration = Integration(org_id=org_id, kind="freshdesk", name=f"upgrade-{suffix}")
        ticket = Ticket(org_id=org_id, number=900000 + int(suffix[:4], 16), subject="Upgrade sentinel", description="preserve me")
        db.add_all([provider, device, integration, ticket])
        db.flush()
        ids = {"oidc_providers": provider.id, "devices": device.id, "integrations": integration.id, "tickets": ticket.id}

    cfg = _alembic_config()
    try:
        command.downgrade(cfg, "0009")
        command.upgrade(cfg, "head")
    finally:
        command.upgrade(cfg, "head")

    owner_engine = create_engine(os.environ["JOLTIQ_MIGRATION_DATABASE_URL"])
    with owner_engine.connect() as conn:
        for table, row_id in ids.items():
            assert conn.execute(text(f"SELECT count(*) FROM {table} WHERE id = :id"), {"id": row_id}).scalar_one() == 1
    owner_engine.dispose()


def test_v7_new_columns_have_backward_compatible_defaults(app, owner):
    from joltiq.db import session_scope
    from joltiq.models import AgentPlan, AgentRun, ComputerUseRun, SupportSession, VerificationRun

    org_id = uuid.UUID(owner.org_id)
    user_id = uuid.UUID(owner.me["user"]["id"])
    with session_scope(org_id) as db:
        run = AgentRun(org_id=org_id, correlation_id=uuid.uuid4().hex)
        session = SupportSession(org_id=org_id, kind="browser", owner_id=user_id)
        db.add_all([run, session])
        db.flush()
        plan = AgentPlan(org_id=org_id, run_id=run.id, revision=1, summary="Safe plan", plan_hash="a" * 64)
        verification = VerificationRun(org_id=org_id, run_id=run.id)
        computer = ComputerUseRun(
            org_id=org_id,
            session_id=session.id,
            goal="Inspect only",
            mode="guide",
        )
        db.add_all([plan, verification, computer])
        db.flush()
        assert verification.kind == "verification"
        assert verification.evidence_hash is None
        assert plan.rollback_steps == []
        assert plan.rollback_availability == {}
        assert session.lease_epoch == 0
        assert computer.rollback == {}


def test_notification_dispatch_is_rls_isolated(app, owner, new_client):
    from conftest import register

    from joltiq.db import session_scope

    (dispatch_cls,) = _v7_classes()
    org_a = uuid.UUID(owner.org_id)
    other = register(new_client(), "Dispatch isolation tenant")
    org_b = uuid.UUID(other.org_id)
    dispatch_id = uuid.uuid4()
    with session_scope(org_a) as db:
        db.add(dispatch_cls(id=dispatch_id, org_id=org_a, user_id=uuid.UUID(owner.me["user"]["id"]), dedupe_key="same-key", kind="approval_requested"))
    with session_scope(org_b) as db:
        assert db.execute(select(dispatch_cls).where(dispatch_cls.id == dispatch_id)).scalar_one_or_none() is None


def test_notification_dispatch_dedupe_is_per_org(app, owner, new_client):
    from conftest import register

    from joltiq.db import session_scope

    (dispatch_cls,) = _v7_classes()
    org_a = uuid.UUID(owner.org_id)
    other = register(new_client(), "Dispatch dedupe tenant")
    org_b = uuid.UUID(other.org_id)
    key = f"approval:{uuid.uuid4().hex}"
    with session_scope(org_a) as db:
        db.add(dispatch_cls(org_id=org_a, user_id=uuid.UUID(owner.me["user"]["id"]), dedupe_key=key, kind="approval_requested"))
    with session_scope(org_b) as db:
        db.add(dispatch_cls(org_id=org_b, user_id=uuid.UUID(other.me["user"]["id"]), dedupe_key=key, kind="approval_requested"))
    with pytest.raises(IntegrityError):
        with session_scope(org_a) as db:
            db.add(dispatch_cls(org_id=org_a, user_id=uuid.UUID(owner.me["user"]["id"]), dedupe_key=key, kind="approval_requested"))
            db.flush()
