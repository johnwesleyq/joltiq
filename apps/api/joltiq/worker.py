"""Background worker process: durable job queue + periodic scheduler.

    python -m joltiq.worker            # all job kinds
    python -m joltiq.worker --kinds agent.advance,agent.device_result

Every periodic task is enqueued with a time-bucketed dedupe key, so running several workers never duplicates work.
"""
from __future__ import annotations

import argparse
import logging
import threading
import time

from sqlalchemy import select

from .core.logging import configure_logging
from .db import session_scope
from .models import Integration, Organization, utcnow
from .services import (  # noqa: F401 - register handlers
    agent,
    approval_notify,
    approvals,
    computer_use,
    email,
    integrations,
    knowledge,
    notifications,
    retention,
)
from .services.jobs import Worker, enqueue, handler

log = logging.getLogger("joltiq.worker")


@handler("approvals.expire")
def _expire(db, payload):
    approvals.expire_stale(db)
    approval_notify.send_reminders(db)


def schedule_tick() -> None:
    now = utcnow()
    minute, hour = now.strftime("%Y%m%d%H%M"), now.strftime("%Y%m%d%H")
    with session_scope(None, platform=True) as db:
        for org in db.execute(select(Organization).where(Organization.status == "active")).scalars():
            enqueue(db, "approvals.expire", {}, org_id=org.id, dedupe_key=f"exp:{org.id}:{minute}")
            enqueue(db, "retention.apply", {}, org_id=org.id, dedupe_key=f"ret:{org.id}:{now.strftime('%Y%m%d')}")
        for integ in db.execute(select(Integration).where(Integration.kind == "freshdesk", Integration.status == "connected")).scalars():
            integrations.queue_sync(db, integ, "pull", "tickets", None, {}, f"fd-pull:{integ.id}:{now.strftime('%Y%m%d%H')}{now.minute // 10}")
        _ = hour


def main() -> None:
    configure_logging()
    ap = argparse.ArgumentParser()
    ap.add_argument("--kinds", default="")
    ap.add_argument("--no-scheduler", action="store_true")
    a = ap.parse_args()
    w = Worker(kinds=[k for k in a.kinds.split(",") if k] or None)
    if not a.no_scheduler:
        def loop():
            while True:
                try:
                    schedule_tick()
                except Exception:  # noqa: BLE001
                    log.exception("scheduler tick failed")
                time.sleep(60)
        threading.Thread(target=loop, daemon=True, name="scheduler").start()
    log.info("worker %s starting", w.id)
    w.run()


if __name__ == "__main__":
    main()
