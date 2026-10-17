from __future__ import annotations

from collections.abc import Callable
from typing import Any

from fastapi import Query
from sqlalchemy import Select, func, select
from sqlalchemy.orm import Session


class PageParams:
    def __init__(self, page: int, page_size: int):
        self.page = page
        self.page_size = page_size


def page_params(page: int = Query(1, ge=1, le=100000), page_size: int = Query(25, ge=1, le=200)) -> PageParams:
    return PageParams(page, page_size)


def paginate(db: Session, stmt: Select, pp: PageParams, mapper: Callable[[Any], dict]) -> dict:
    total = db.execute(select(func.count()).select_from(stmt.order_by(None).subquery())).scalar() or 0
    rows = db.execute(stmt.limit(pp.page_size).offset((pp.page - 1) * pp.page_size)).all()
    items = [mapper(r if len(r) > 1 else r[0]) for r in rows]
    return {"items": items, "page": pp.page, "page_size": pp.page_size, "total": total,
            "has_more": pp.page * pp.page_size < total}
