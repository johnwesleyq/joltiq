"""Fix it for me entry point: one request → ticket + session + computer-use run waiting for ONE customer approval."""
from __future__ import annotations

import uuid
from typing import Any
from urllib.parse import urlsplit

from sqlalchemy.orm import Session

from ..core.errors import AppError
from ..models import Device, Organization


def validate_sites(raw: Any) -> list[dict[str, str]]:
    """Org setting ``fix_sites``: the sites an admin allows Browser Fix to operate (name + start URL). Only these
    domains load in the fix browser."""
    if not isinstance(raw, list) or len(raw) > 20:
        raise AppError("fix_sites must be a list of up to 20 {name, url}", code="validation_error")
    out = []
    for item in raw:
        if not isinstance(item, dict):
            raise AppError("each fix site needs name and url", code="validation_error")
        name, url = str(item.get("name") or "").strip()[:60], str(item.get("url") or "").strip()[:500]
        parts = urlsplit(url)
        if parts.scheme not in ("http", "https") or not parts.hostname or not name:
            raise AppError(f"invalid fix site: {name or url}", code="validation_error")
        out.append({"name": name, "url": url})
    return out


def sites(db: Session, org_id: uuid.UUID) -> list[dict[str, str]]:
    org = db.get(Organization, org_id)
    try:
        return validate_sites((org.settings or {}).get("fix_sites") or []) if org else []
    except AppError:
        return []


def pick_site(db: Session, org_id: uuid.UUID, url: str | None, message: str) -> dict[str, str]:
    allowed = sites(db, org_id)
    if not allowed:
        raise AppError("No sites are set up for Fix it for me yet. An admin adds them in Settings → Fix it for me.",
                       code="fix_sites_not_configured")
    if url:
        host = (urlsplit(url).hostname or "").lower()
        for s in allowed:
            if (urlsplit(s["url"]).hostname or "").lower() == host:
                return {"name": s["name"], "url": url}
        raise AppError("That site isn't on your organization's Fix it for me list.", code="domain_not_allowed")
    low = message.lower()
    return next((s for s in allowed if s["name"].lower() in low), allowed[0])


def device_ok(db: Session, org_id: uuid.UUID, device_id: uuid.UUID) -> Device:
    d = db.get(Device, device_id)
    if not d or d.org_id != org_id or d.status != "active":
        raise AppError("That computer isn't enrolled or active", code="device_required")
    return d
