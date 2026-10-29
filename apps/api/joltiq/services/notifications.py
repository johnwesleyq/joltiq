"""In-app, email and webhook notifications honoring per-user preferences, with retries via the job queue."""
from __future__ import annotations

import json
import uuid

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..config import get_settings
from ..models import Delivery, Membership, Notification, NotificationPreference, User, WebhookEndpoint, utcnow
from ..models.enums import Role
from ..security.crypto import decrypt_secret, hmac_sign
from .email import send_email
from .jobs import enqueue, handler
from .realtime import publish

CATEGORIES = [
    "ticket",
    "approval",
    "approval_reminder",
    "approval_outcome",
    "session",
    "escalation",
    "resolution",
    "device",
    "security",
    "integration",
    "usage",
]


def staff_user_ids(db: Session, org_id: uuid.UUID, roles: tuple[str, ...] = (Role.TECHNICIAN, Role.ADMIN, Role.ORG_OWNER)) -> list[uuid.UUID]:
    return list(db.execute(select(Membership.user_id).where(Membership.org_id == org_id, Membership.role.in_(roles))).scalars())


def notify(db: Session, org_id: uuid.UUID, user_ids: list[uuid.UUID], category: str, title: str, body: str = "",
           link: str | None = None, *, email: bool = True) -> None:
    prefs = {
        (p.user_id, p.category): p
        for p in db.execute(select(NotificationPreference).where(NotificationPreference.user_id.in_(user_ids or [uuid.uuid4()]))).scalars()
    }
    for uid in dict.fromkeys(user_ids):
        p = prefs.get((uid, category))
        if p is None or p.in_app:
            n = Notification(org_id=org_id, user_id=uid, category=category, title=title[:300], body=body, link=link)
            db.add(n)
            db.flush()
            publish(db, org_id, "notification", {"id": str(n.id), "title": n.title, "category": category, "link": link}, audience=f"user:{uid}")
        if email and (p is None or p.email):
            u = db.get(User, uid)
            if u:
                full = f"{body}\n\nOpen: {get_settings().web_origin}{link}" if link else body
                send_email(db, org_id=org_id, to=u.email, subject=title, body=full)
    _fanout_webhooks(db, org_id, category, {"category": category, "title": title, "body": body, "link": link})


def _fanout_webhooks(db: Session, org_id: uuid.UUID, category: str, payload: dict) -> None:
    for ep in db.execute(select(WebhookEndpoint).where(WebhookEndpoint.enabled.is_(True))).scalars():
        if ep.categories and category not in ep.categories:
            continue
        d = Delivery(org_id=org_id, channel="webhook", target=ep.url, subject=category,
                     payload={"endpoint_id": str(ep.id), "event": {"id": uuid.uuid4().hex, "type": f"{category}.notification",
                                                                    "created": utcnow().isoformat(), "data": payload}})
        db.add(d)
        db.flush()
        enqueue(db, "webhook.deliver", {"delivery_id": str(d.id)}, org_id=org_id, max_attempts=8)


@handler("webhook.deliver")
def _deliver_webhook(db: Session, payload: dict) -> None:
    import httpx

    from ..models import Credential
    from ..security.ssrf import validate_url

    d = db.get(Delivery, uuid.UUID(payload["delivery_id"]))
    if not d or d.status == "delivered":
        return
    ep = db.get(WebhookEndpoint, uuid.UUID(d.payload["endpoint_id"]))
    if not ep or not ep.enabled:
        d.status = "cancelled"
        return
    d.attempts += 1
    body = json.dumps(d.payload["event"], separators=(",", ":"))
    ts = str(int(utcnow().timestamp()))
    headers = {"content-type": "application/json", "x-joltiq-timestamp": ts, "x-joltiq-event-id": d.payload["event"]["id"]}
    if ep.credential_id:
        cred = db.get(Credential, ep.credential_id)
        if cred and not cred.revoked_at:
            headers["x-joltiq-signature"] = "v1=" + hmac_sign(decrypt_secret(cred.ciphertext), f"{ts}.{body}")
    try:
        validate_url(ep.url, allow_private=get_settings().env != "production")
        r = httpx.post(ep.url, content=body, headers=headers, timeout=10)
        if r.status_code >= 300:
            raise RuntimeError(f"HTTP {r.status_code}")
        d.status, d.delivered_at, d.last_error = "delivered", utcnow(), None
    except Exception as e:
        d.status, d.last_error = "retrying", str(e)[:300]
        db.commit()
        raise
