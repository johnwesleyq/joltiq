"""SMTP email delivery through the durable queue (retries + dead letter). Branded per tenant."""
from __future__ import annotations

import smtplib
import ssl
import uuid
from email.message import EmailMessage

from sqlalchemy.orm import Session

from ..config import get_settings
from ..models import Delivery, Organization, utcnow
from .jobs import enqueue, handler


def brand_for(db: Session, org_id: uuid.UUID | None) -> dict:
    s = get_settings()
    b = {"product_name": s.product_name, "support_email": s.support_email, "from_name": s.product_name}
    if org_id:
        org = db.get(Organization, org_id)
        if org and org.branding:
            b.update({k: v for k, v in org.branding.items() if v})
    return b


def send_email(db: Session, *, org_id: uuid.UUID | None, to: str, subject: str, body: str) -> uuid.UUID:
    brand = brand_for(db, org_id)
    d = Delivery(
        org_id=org_id, channel="email", target=to, subject=f"[{brand['product_name']}] {subject}",
        payload={"body": body + f"\n\n— {brand['product_name']} · {brand['support_email']}", "from_name": brand.get("from_name")},
    ) if org_id else None
    if d is None:
        # System emails (signup/reset before a tenant exists) are sent via a job without a Delivery row.
        enqueue(db, "email.send_system", {"to": to, "subject": f"[{brand['product_name']}] {subject}", "body": body})
        return uuid.uuid4()
    db.add(d)
    db.flush()
    enqueue(db, "email.deliver", {"delivery_id": str(d.id)}, org_id=org_id)
    return d.id


def _smtp_send(to: str, subject: str, body: str, from_name: str | None = None) -> None:
    s = get_settings()
    msg = EmailMessage()
    msg["From"] = f"{from_name or s.product_name} <{s.email_from}>"
    msg["To"] = to
    msg["Subject"] = subject
    msg.set_content(body)
    with smtplib.SMTP(s.smtp_host, s.smtp_port, timeout=15) as smtp:
        if s.smtp_starttls:
            smtp.starttls(context=ssl.create_default_context())
        if s.smtp_username and s.smtp_password:
            smtp.login(s.smtp_username, s.smtp_password)
        smtp.send_message(msg)


@handler("email.deliver")
def _deliver(db: Session, payload: dict) -> None:
    d = db.get(Delivery, uuid.UUID(payload["delivery_id"]))
    if not d or d.status == "delivered":
        return
    d.attempts += 1
    try:
        _smtp_send(d.target, d.subject, d.payload.get("body", ""), d.payload.get("from_name"))
        d.status, d.delivered_at, d.last_error = "delivered", utcnow(), None
    except Exception as e:
        d.status, d.last_error = "retrying", str(e)[:500]
        db.commit()
        raise


@handler("email.send_system")
def _send_system(db: Session, payload: dict) -> None:
    _smtp_send(payload["to"], payload["subject"], payload["body"])
