"""Real-time event fan-out for SSE/WebSocket clients.

publish() is called inside request/worker transactions; events are delivered after the transaction commits
(so clients never see uncommitted state). Redis pub/sub is used across processes when available; an
in-process broker is always used for local subscribers.
"""
from __future__ import annotations

import asyncio
import json
import logging
import threading
import uuid
from typing import Any

from sqlalchemy import event
from sqlalchemy.orm import Session

from ..config import get_settings

log = logging.getLogger("joltiq.realtime")
CHANNEL = "joltiq:events"


class Broker:
    def __init__(self) -> None:
        self._subs: dict[str, tuple[asyncio.AbstractEventLoop, asyncio.Queue, str]] = {}
        self._lock = threading.Lock()
        self._redis_thread: threading.Thread | None = None
        self.node_id = uuid.uuid4().hex

    def subscribe(self, org_id: str) -> tuple[str, asyncio.Queue]:
        q: asyncio.Queue = asyncio.Queue(maxsize=500)
        sid = uuid.uuid4().hex
        with self._lock:
            self._subs[sid] = (asyncio.get_running_loop(), q, org_id)
        self._ensure_redis_listener()
        return sid, q

    def unsubscribe(self, sid: str) -> None:
        with self._lock:
            self._subs.pop(sid, None)

    def deliver_local(self, msg: dict[str, Any]) -> None:
        with self._lock:
            subs = list(self._subs.values())
        for loop, q, org in subs:
            if msg.get("org_id") == org:
                try:
                    loop.call_soon_threadsafe(q.put_nowait, msg)
                except Exception:  # noqa: BLE001,S110 - queue full or loop closed; client will resync
                    pass

    def publish_now(self, msg: dict[str, Any]) -> None:
        self.deliver_local(msg)
        r = _redis()
        if r is not None:
            try:
                r.publish(CHANNEL, json.dumps({**msg, "_node": self.node_id}, default=str))
            except Exception:  # noqa: BLE001
                log.warning("redis publish failed")

    def _ensure_redis_listener(self) -> None:
        if self._redis_thread is not None or _redis() is None:
            return

        def run():
            r = _redis()
            if r is None:
                return
            ps = r.pubsub()
            ps.subscribe(CHANNEL)
            for m in ps.listen():
                if m.get("type") != "message":
                    continue
                try:
                    data = json.loads(m["data"])
                except Exception:  # noqa: BLE001,S112
                    continue
                if data.pop("_node", None) != self.node_id:
                    self.deliver_local(data)

        self._redis_thread = threading.Thread(target=run, daemon=True, name="realtime-redis")
        self._redis_thread.start()


_r = None


def _redis():
    global _r
    if _r is None:
        try:
            import redis

            c = redis.Redis.from_url(get_settings().redis_url, socket_connect_timeout=0.5)
            c.ping()
            _r = c
        except Exception:  # noqa: BLE001
            _r = False
    return _r or None


broker = Broker()


def publish(db: Session, org_id: uuid.UUID | str, kind: str, data: dict[str, Any], *, audience: str = "all") -> None:
    """audience: 'all' (org members with read access), 'staff' (exclude customers), or 'user:<id>'."""
    msg = {"org_id": str(org_id), "kind": kind, "data": data, "audience": audience}
    pending = db.info.setdefault("_rt_pending", [])
    pending.append(msg)


@event.listens_for(Session, "after_commit")
def _flush_after_commit(session: Session) -> None:
    pending = session.info.pop("_rt_pending", None)
    for m in pending or []:
        broker.publish_now(m)


@event.listens_for(Session, "after_rollback")
def _discard(session: Session) -> None:
    session.info.pop("_rt_pending", None)
