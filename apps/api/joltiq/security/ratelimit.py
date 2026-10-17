"""Fixed-window rate limiter. Uses Redis when reachable, Postgres otherwise (single source of truth
per deployment; never silently disabled)."""
from __future__ import annotations

import time
from datetime import timedelta

from sqlalchemy import text
from sqlalchemy.orm import Session

from ..config import get_settings
from ..core.errors import RateLimited

_redis = None
_redis_checked = 0.0


def _get_redis():
    global _redis, _redis_checked
    if _redis is not None:
        return _redis
    if time.time() - _redis_checked < 30:
        return None
    _redis_checked = time.time()
    try:
        import redis

        r = redis.Redis.from_url(get_settings().redis_url, socket_timeout=0.5, socket_connect_timeout=0.5)
        r.ping()
        _redis = r
    except Exception:
        _redis = None
    return _redis


def hit(db: Session, key: str, limit: int, window_seconds: int = 60) -> None:
    r = _get_redis()
    if r is not None:
        try:
            bucket = f"rl:{key}:{int(time.time() // window_seconds)}"
            n = r.incr(bucket)
            if n == 1:
                r.expire(bucket, window_seconds + 1)
            if n > limit:
                raise RateLimited("Too many requests; try again shortly.", details={"retry_after": window_seconds})
            return
        except RateLimited:
            raise
        except Exception:  # noqa: S110 - fall through to DB limiter
            pass
    row = db.execute(
        text(
            """
            insert into rate_limit_buckets(key, window_start, count) values (:k, now(), 1)
            on conflict (key) do update set
              count = case when rate_limit_buckets.window_start < now() - make_interval(secs => :w) then 1
                           else rate_limit_buckets.count + 1 end,
              window_start = case when rate_limit_buckets.window_start < now() - make_interval(secs => :w) then now()
                           else rate_limit_buckets.window_start end
            returning count
            """
        ),
        {"k": key, "w": window_seconds},
    ).scalar()
    if row and row > limit:
        raise RateLimited("Too many requests; try again shortly.", details={"retry_after": window_seconds})


_ = timedelta
