"""Tenant data retention: deletes session events, verification screenshots/evidence and old notifications past the tenant's
configured windows. Audit events are never deleted by the app (append-only role); their retention is an ops procedure."""
from __future__ import annotations

from datetime import timedelta

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from ..models import (
    ComputerUseRun,
    ComputerUseStep,
    Notification,
    Organization,
    SessionEvent,
    VerificationEvidence,
    utcnow,
)
from . import storage
from .jobs import handler


def apply_retention(db: Session, org_id) -> dict:
    org = db.get(Organization, org_id)
    r = ((org.settings if org else {}) or {}).get("retention_days", {})
    now = utcnow()
    out = {}
    out["session_events"] = db.execute(delete(SessionEvent).where(SessionEvent.created_at < now - timedelta(days=int(r.get("session_events", 90))))).rowcount
    out["verification_evidence"] = db.execute(delete(VerificationEvidence).where(
        VerificationEvidence.created_at < now - timedelta(days=int(r.get("screenshots", 30))))).rowcount
    screen_cutoff = now - timedelta(days=int(r.get("screenshots", 30)))
    steps = list(db.execute(select(ComputerUseStep).where(
        ComputerUseStep.created_at < screen_cutoff,
        ComputerUseStep.screenshot_key.is_not(None),
    )).scalars())
    purged_keys: set[str] = set()
    for step in steps:
        key = step.screenshot_key
        if not key:
            continue
        if key not in purged_keys:
            storage.delete(org_id, key)
            purged_keys.add(key)
        step.screenshot_key = None
    if purged_keys:
        for run in db.execute(select(ComputerUseRun).where(ComputerUseRun.last_screenshot_key.in_(purged_keys))).scalars():
            run.last_screenshot_key = None
    out["computer_use_screenshots"] = len(purged_keys)
    out["notifications"] = db.execute(delete(Notification).where(Notification.created_at < now - timedelta(days=int(r.get("notifications", 180))))).rowcount
    return out


@handler("retention.apply")
def _job(db: Session, payload: dict) -> None:
    from ..db import current_org

    apply_retention(db, current_org(db))
