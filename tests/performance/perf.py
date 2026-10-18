"""Performance smoke against a RUNNING stack (API :8000, browser worker :8100). Reports p50/p95 latencies.

    python tests/performance/perf.py --api http://127.0.0.1:8000 --worker http://127.0.0.1:8100 --concurrency 20 --requests 400

Measures: authenticated ticket list + create latency under concurrency, knowledge search latency, isolated browser session
startup time, and job-worker recovery (a job locked by a "crashed" worker is reclaimed after its lease expires).
"""
from __future__ import annotations

import argparse
import json
import os
import statistics
import time
import uuid
from concurrent.futures import ThreadPoolExecutor

import httpx


def pct(xs, p):
    xs = sorted(xs)
    return round(xs[min(len(xs) - 1, int(len(xs) * p))], 1) if xs else None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--api", default="http://127.0.0.1:8000")
    ap.add_argument("--worker", default="http://127.0.0.1:8100")
    ap.add_argument("--worker-token", default=os.environ.get("JOLTIQ_BROWSER_WORKER_TOKEN", "dev-browser-worker-token-change-me"))
    ap.add_argument("--concurrency", type=int, default=20)
    ap.add_argument("--requests", type=int, default=400)
    a = ap.parse_args()
    out: dict = {"concurrency": a.concurrency, "requests": a.requests}

    c = httpx.Client(base_url=a.api, timeout=30, headers={"x-joltiq-client": "api"})
    email = f"perf-{uuid.uuid4().hex[:8]}@example.com"
    r = c.post("/api/v1/auth/register", json={"email": email, "password": "correct-horse-battery-staple", "name": "Perf", "organization_name": "Perf Org"})
    r.raise_for_status()
    token = c.post("/api/v1/auth/login", json={"email": email, "password": "correct-horse-battery-staple"}).json()["access_token"]
    h = {"authorization": f"Bearer {token}"}

    def timed(method, path, **kw):
        t0 = time.perf_counter()
        resp = httpx.request(method, a.api + path, headers=h, timeout=30, **kw)
        return (time.perf_counter() - t0) * 1000, resp.status_code

    for name, fn in [("ticket_create", lambda i: timed("POST", "/api/v1/tickets", json={"subject": f"Perf ticket {i}", "description": "printer offline"})),
                     ("ticket_list", lambda i: timed("GET", "/api/v1/tickets?page_size=25")),
                     ("knowledge_search", lambda i: timed("POST", "/api/v1/knowledge/search", json={"query": "printer offline spooler"}))]:
        n = a.requests if name != "ticket_create" else a.requests // 4
        t0 = time.perf_counter()
        with ThreadPoolExecutor(a.concurrency) as ex:
            res = list(ex.map(fn, range(n)))
        lat = [x[0] for x in res]
        errs = sum(1 for x in res if x[1] >= 400)
        out[name] = {"n": n, "p50_ms": pct(lat, .5), "p95_ms": pct(lat, .95), "max_ms": round(max(lat), 1), "errors": errs,
                     "throughput_rps": round(n / (time.perf_counter() - t0), 1)}

    wh = {"authorization": f"Bearer {a.worker_token}"}
    starts = []
    for _ in range(5):
        sid = uuid.uuid4().hex
        t0 = time.perf_counter()
        httpx.post(f"{a.worker}/sessions", json={"session_id": sid, "allowed_domains": ["127.0.0.1"]}, headers=wh, timeout=30).raise_for_status()
        starts.append((time.perf_counter() - t0) * 1000)
        httpx.delete(f"{a.worker}/sessions/{sid}", headers=wh, timeout=30)
    out["browser_session_start"] = {"n": 5, "p50_ms": pct(starts, .5), "max_ms": round(max(starts), 1),
                                    "browser_launch_ms": httpx.get(f"{a.worker}/healthz").json().get("startup_ms")}

    # Worker recovery: simulate a crashed worker holding a job with an expired lease; a live worker must reclaim it.
    try:
        import sys

        sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "apps", "api"))
        from sqlalchemy import text

        from joltiq.db import session_scope

        jid = uuid.uuid4()
        with session_scope(None) as db:
            db.execute(text("insert into jobs(id, kind, payload, status, attempts, max_attempts, run_after, locked_by, locked_until, created_at, updated_at) "
                            "values (:id, 'approvals.expire', '{}', 'running', 1, 5, now(), 'crashed-worker', now() - interval '1 second', now(), now())"), {"id": jid})
        t0 = time.time()
        status = None
        while time.time() - t0 < 30:
            with session_scope(None) as db:
                status = db.execute(text("select status from jobs where id = :id"), {"id": jid}).scalar()
            if status == "done":
                break
            time.sleep(0.5)
        out["worker_recovery"] = {"reclaimed": status == "done", "seconds": round(time.time() - t0, 1)}
    except Exception as e:  # noqa: BLE001
        out["worker_recovery"] = {"skipped": str(e)[:200]}
    print(json.dumps(out, indent=2))
    _ = statistics


if __name__ == "__main__":
    main()
