from __future__ import annotations

import uuid
from datetime import timedelta

import pytest
from sqlalchemy import select


def test_desktop_usage_never_exceeds_the_consented_session_window(owner):
    from joltiq.db import session_scope
    from joltiq.models import SupportSession, UsageEvent, utcnow
    from joltiq.services import sessions

    org_id = uuid.UUID(owner.org_id)
    user_id = uuid.UUID(owner.me["user"]["id"])
    consented_at = utcnow() - timedelta(hours=20)

    with session_scope(org_id) as db:
        session = SupportSession(
            org_id=org_id,
            kind="desktop",
            state="active",
            owner_id=user_id,
            consent={"minutes": 60},
            consented_by=user_id,
            consented_at=consented_at,
            expires_at=consented_at + timedelta(minutes=60),
        )
        db.add(session)
        db.flush()

        sessions.terminate(db, session, by_kind="system", reason="expired session cleanup")

        usage = db.execute(
            select(UsageEvent).where(
                UsageEvent.idempotency_key == f"session-minutes:{session.id}"
            )
        ).scalar_one()
        assert usage.meter == "desktop_minutes"
        assert usage.quantity == pytest.approx(60.0)
