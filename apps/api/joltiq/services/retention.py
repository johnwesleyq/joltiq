"""Tenant data retention: deletes session events, verification screenshots/evidence and old notifications past the tenant's
configured windows. Audit events are never deleted by the app (append-only role); their retention is an ops procedure."""
from __future__ import annotations

from datetime import timedelta

from sqlalchemy import delete
from sqlalchemy.orm import Session

from ..models import Notification, Organization, SessionEvent, VerificationEvidence, utcnow
from .jobs import handler


def apply_retention(db: Session, org_id) -> dict:
    org = db.get(Organization, org_id)
    r = ((org.settings if org else {}) or {}).get("retention_days", {})
    now = utcnow()
    out = {}
    out["session_events"] = db.execute(delete(SessionEvent).where(SessionEvent.created_at < now - timedelta(days=int(r.get("session_events", 90))))).rowcount
    out["verification_evidence"] = db.execute(delete(VerificationEvidence).where(
        VerificationEvidence.created_at < now - timedelta(days=int(r.get("screenshots", 30))))).rowcount
    out["notifications"] = db.execute(delete(Notification).where(Notification.created_at < now - timedelta(days=int(r.get("notifications", 180))))).rowcount
    return out


@handler("retention.apply")
def _job(db: Session, payload: dict) -> None:
    from ..db import current_org

    apply_retention(db, current_org(db))
