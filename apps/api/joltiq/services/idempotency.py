"""Idempotency-Key support for POST endpoints: same key + same endpoint within 24h returns the stored response.
Stored in the `idempotency_records` table (migration 0003)."""
from __future__ import annotations

import json
from collections.abc import Callable
from typing import Any

from sqlalchemy import text

from ..core.errors import AppError


def idempotent(ctx, key: str | None, scope: str, fn: Callable[[], Any]) -> Any:
    if not key:
        return fn()
    if len(key) > 100:
        raise AppError("Idempotency-Key too long", code="validation_error")
    full = f"{ctx.org_id}:{ctx.p.user_id}:{scope}:{key}"
    db = ctx.db
    row = db.execute(text("select response from idempotency_records where key = :k and created_at > now() - interval '24 hours'"),
                     {"k": full}).scalar()
    if row is not None:
        return row
    # Serialize concurrent duplicates.
    db.execute(text("select pg_advisory_xact_lock(hashtext(:k))"), {"k": full})
    row = db.execute(text("select response from idempotency_records where key = :k"), {"k": full}).scalar()
    if row is not None:
        return row
    result = fn()
    db.execute(text("insert into idempotency_records(key, response) values (:k, cast(:r as jsonb)) on conflict (key) do nothing"),
               {"k": full, "r": json.dumps(result, default=str)})
    return result

