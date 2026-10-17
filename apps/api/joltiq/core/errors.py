"""Consistent error envelope: {"error": {"code", "message", "details", "request_id"}}."""
from __future__ import annotations

from typing import Any


class AppError(Exception):
    status = 400
    code = "bad_request"

    def __init__(self, message: str = "", *, details: Any = None, code: str | None = None, status: int | None = None):
        super().__init__(message or self.code)
        self.message = message or self.code
        self.details = details
        if code:
            self.code = code
        if status:
            self.status = status


class NotFound(AppError):
    status, code = 404, "not_found"


class Forbidden(AppError):
    status, code = 403, "forbidden"


class Unauthorized(AppError):
    status, code = 401, "unauthorized"


class Conflict(AppError):
    status, code = 409, "conflict"


class RateLimited(AppError):
    status, code = 429, "rate_limited"


class PolicyDenied(AppError):
    status, code = 403, "policy_denied"


class EntitlementExceeded(AppError):
    status, code = 402, "entitlement_exceeded"


class Unavailable(AppError):
    """Feature is real but not configured (e.g. missing credentials). Never a fake success."""

    status, code = 503, "unavailable"
