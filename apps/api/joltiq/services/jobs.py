"""Durable Postgres job queue: SKIP LOCKED claiming, exponential backoff, dead-letter, visibility timeout
(crashed workers' jobs are reclaimed after lock expiry), dedupe keys, graceful shutdown."""
from __future__ import annotations

import logging
import os
import socket
import threading
import time
import traceback
import uuid
from collections.abc import Callable
from datetime import timedelta
from typing import Any

from sqlalchemy import select, text
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from ..db import session_scope
from ..models import Job, WorkerHeartbeat, utcnow

log = logging.getLogger("joltiq.jobs")
HANDLERS: dict[str, Callable[[Session, dict[str, Any]], None]] = {}


def handler(kind: str):
    def deco(fn):
        HANDLERS[kind] = fn
        return fn

    return deco


def enqueue(db: Session, kind: str, payload: dict[str, Any], *, org_id: uuid.UUID | None = None,
            delay_seconds: int = 0, dedupe_key: str | None = None, max_attempts: int = 5) -> None:
    stmt = insert(Job).values(
        id=uuid.uuid4(), org_id=org_id, kind=kind, payload=payload, status="queued", attempts=0,
        max_attempts=max_attempts, run_after=utcnow() + timedelta(seconds=delay_seconds), dedupe_key=dedupe_key,
    )
    if dedupe_key:
        stmt = stmt.on_conflict_do_nothing(index_elements=["dedupe_key"])
    db.execute(stmt)


def cancel_queued(db: Session, *dedupe_keys: str) -> None:
    """Mark still-queued idempotent jobs done when their workflow has reached a terminal decision."""
    if dedupe_keys:
        db.execute(
            text("update jobs set status='done', updated_at=now() where status='queued' and dedupe_key = any(:keys)"),
            {"keys": list(dedupe_keys)},
        )


def claim(db: Session, worker_id: str, kinds: list[str] | None = None, lease_seconds: int = 300) -> Job | None:
    cond = "(status = 'queued' and run_after <= now()) or (status = 'running' and locked_until < now())"
    params: dict[str, Any] = {"w": worker_id, "lease": lease_seconds}
    kind_filter = ""
    if kinds:
        kind_filter = " and kind = any(:kinds)"
        params["kinds"] = kinds
    row = db.execute(
        text(
            f"""
            update jobs set status='running', locked_by=:w, locked_until=now() + make_interval(secs => :lease),
                   attempts = attempts + 1, updated_at = now()
            where id = (select id from jobs where ({cond}){kind_filter}
                        order by run_after for update skip locked limit 1)
            returning id
            """  # noqa: S608 - kind_filter is a constant string
        ),
        params,
    ).scalar()
    if not row:
        return None
    return db.get(Job, row)


def run_one(worker_id: str, kinds: list[str] | None = None) -> bool:
    with session_scope(None) as db:
        job = claim(db, worker_id, kinds)
        if not job:
            return False
        job_id, kind, payload, org_id, attempts, max_attempts = job.id, job.kind, dict(job.payload), job.org_id, job.attempts, job.max_attempts
    fn = HANDLERS.get(kind)
    err = None
    if fn is None:
        err = f"no handler for {kind}"
    else:
        try:
            with session_scope(org_id) as db:
                fn(db, payload)
        except Exception as e:  # noqa: BLE001
            err = f"{type(e).__name__}: {e}\n{traceback.format_exc()[-1500:]}"
            log.exception("job failed kind=%s id=%s", kind, job_id)
    with session_scope(None) as db:
        j = db.get(Job, job_id)
        if j is None:
            return True
        if err is None:
            j.status, j.locked_by, j.locked_until = "done", None, None
        elif attempts >= max_attempts:
            j.status, j.last_error = "dead", err  # dead-letter: visible in platform admin
        else:
            j.status, j.last_error = "queued", err
            j.run_after = utcnow() + timedelta(seconds=min(600, 2 ** attempts * 5))
            j.locked_by = None
    return True


def drain(kinds: list[str] | None = None, max_jobs: int = 1000) -> int:
    """Run queued jobs synchronously (tests, CLI)."""
    n = 0
    while n < max_jobs and run_one(f"drain-{os.getpid()}", kinds):
        n += 1
    return n


def beat(worker_id: str, kind: str, info: dict | None = None) -> None:
    with session_scope(None) as db:
        hb = db.get(WorkerHeartbeat, worker_id)
        if hb is None:
            db.add(WorkerHeartbeat(worker_id=worker_id, kind=kind, last_beat=utcnow(), info=info or {}))
        else:
            hb.last_beat, hb.info = utcnow(), info or {}


class Worker:
    def __init__(self, kinds: list[str] | None = None, poll: float = 1.0):
        self.kinds = kinds
        self.poll = poll
        self.id = f"{socket.gethostname()}-{os.getpid()}-{uuid.uuid4().hex[:6]}"
        self._stop = threading.Event()

    def stop(self, *_):
        log.info("worker %s stopping gracefully", self.id)
        self._stop.set()

    def run(self) -> None:
        import signal

        signal.signal(signal.SIGTERM, self.stop)
        signal.signal(signal.SIGINT, self.stop)
        last_beat = 0.0
        while not self._stop.is_set():
            if time.time() - last_beat > 15:
                beat(self.id, "job-worker", {"kinds": self.kinds})
                last_beat = time.time()
            try:
                did = run_one(self.id, self.kinds)
            except Exception:  # noqa: BLE001
                log.exception("worker loop error")
                did = False
            if not did:
                self._stop.wait(self.poll)


def queue_stats(db: Session) -> dict[str, int]:
    rows = db.execute(select(Job.status, text("count(*)")).group_by(Job.status)).all()
    return {r[0]: r[1] for r in rows}
