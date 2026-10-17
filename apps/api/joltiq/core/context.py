from __future__ import annotations

import contextvars
import uuid

request_id_var: contextvars.ContextVar[str] = contextvars.ContextVar("request_id", default="")


def new_request_id() -> str:
    return uuid.uuid4().hex


def current_request_id() -> str:
    return request_id_var.get()
