"""Database engine, sessions and tenant (RLS) context.

Tenant isolation is enforced twice:
  1. Application layer: every tenant query goes through `tenant_session`, which binds org_id.
  2. Database layer: PostgreSQL row-level security policies compare each row's org_id with the
     transaction-local GUC `app.org_id`. The app connects as `joltiq_app` (NOBYPASSRLS), so a
     missing/incorrect org filter in code cannot leak rows across tenants.
"""
from __future__ import annotations

import uuid
from collections.abc import Iterator
from contextlib import contextmanager

from sqlalchemy import create_engine, event, text
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from .config import get_settings


class Base(DeclarativeBase):
    pass


_engine = None
_SessionLocal: sessionmaker[Session] | None = None


def engine():
    global _engine, _SessionLocal
    if _engine is None:
        _engine = create_engine(get_settings().database_url, pool_pre_ping=True, pool_size=10, max_overflow=20)
        _SessionLocal = sessionmaker(bind=_engine, expire_on_commit=False, autoflush=False)
    return _engine


def reset_engine() -> None:
    global _engine, _SessionLocal
    if _engine is not None:
        _engine.dispose()
    _engine = None
    _SessionLocal = None


def _factory() -> sessionmaker[Session]:
    engine()
    assert _SessionLocal is not None
    return _SessionLocal


def _apply_ctx(conn, info: dict) -> None:
    conn.execute(text("select set_config('app.org_id', :v, true)"), {"v": info.get("org_id", "")})
    conn.execute(text("select set_config('app.platform_admin', :v, true)"), {"v": info.get("platform", "off")})


@event.listens_for(Session, "after_begin")
def _after_begin(session: Session, transaction, connection) -> None:  # noqa: ANN001
    # Re-applied at the start of EVERY transaction so context survives commits within a request.
    if "org_id" in session.info:
        _apply_ctx(connection, session.info)


def set_tenant(db: Session, org_id: uuid.UUID | str | None, *, platform: bool = False) -> None:
    """Bind RLS context (transaction-local GUCs) for this session, now and after each commit."""
    db.info["org_id"] = str(org_id) if org_id else ""
    db.info["platform"] = "on" if platform else "off"
    if db.in_transaction():
        _apply_ctx(db.connection(), db.info)


def current_org(db: Session) -> uuid.UUID | None:
    v = db.info.get("org_id")
    return uuid.UUID(v) if v else None


@contextmanager
def session_scope(org_id: uuid.UUID | str | None = None, *, platform: bool = False) -> Iterator[Session]:
    db = _factory()()
    try:
        set_tenant(db, org_id, platform=platform)
        yield db
        db.commit()
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


def get_raw_session() -> Iterator[Session]:
    """FastAPI dependency: unscoped session (no tenant). RLS hides all tenant rows."""
    db = _factory()()
    try:
        set_tenant(db, None)
        yield db
        db.commit()
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()
