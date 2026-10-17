"""Plans, server-side entitlements, idempotent usage metering, Stripe adapter + webhook processing.

Prices are HYPOTHETICAL placeholders from the product brief ($299/$799/$1,999) and are configurable.
Card data never touches JoltIQ: checkout and the billing portal are Stripe-hosted.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from ..config import get_settings
from ..core.errors import AppError, EntitlementExceeded, Unavailable
from ..models import BillingEvent, Device, Membership, Organization, Subscription, UsageEvent, utcnow
from ..models.enums import Role

PLANS: dict[str, dict[str, Any]] = {
    "trial": {"label": "Trial", "monthly_usd": 0, "technicians": 3, "devices": 10, "ai_usd_month": 25, "research_per_day": 50,
              "browser_minutes": 120, "desktop_minutes": 120, "storage_gb": 2, "integrations": 1},
    "starter": {"label": "Starter", "monthly_usd": 299, "annual_usd": 2990, "technicians": 3, "devices": 50, "ai_usd_month": 150,
                "research_per_day": 200, "browser_minutes": 1000, "desktop_minutes": 1000, "storage_gb": 20, "integrations": 2},
    "growth": {"label": "Growth", "monthly_usd": 799, "annual_usd": 7990, "technicians": 10, "devices": 250, "ai_usd_month": 500,
               "research_per_day": 1000, "browser_minutes": 5000, "desktop_minutes": 5000, "storage_gb": 100, "integrations": 5},
    "scale": {"label": "Scale", "monthly_usd": 1999, "annual_usd": 19990, "technicians": 30, "devices": 1000, "ai_usd_month": 1500,
              "research_per_day": 5000, "browser_minutes": 20000, "desktop_minutes": 20000, "storage_gb": 500, "integrations": 20},
    "enterprise": {"label": "Enterprise", "monthly_usd": None, "technicians": 10_000, "devices": 100_000, "ai_usd_month": 100_000,
                   "research_per_day": 100_000, "browser_minutes": 10**7, "desktop_minutes": 10**7, "storage_gb": 10_000, "integrations": 1000},
}
ACTIVE_STATUSES = {"trialing", "active", "past_due"}
METERS = {"ai_tokens", "ai_usd", "research_requests", "browser_minutes", "desktop_minutes", "storage_bytes",
          "sessions", "verified_resolutions"}


def subscription(db: Session, org_id: uuid.UUID) -> Subscription:
    sub = db.execute(select(Subscription).where(Subscription.org_id == org_id)).scalar_one_or_none()
    if sub is None:
        sub = Subscription(org_id=org_id, plan="trial", status="trialing", trial_ends_at=utcnow() + timedelta(days=14))
        db.add(sub)
        db.flush()
    return sub


def plan_limits(db: Session, org_id: uuid.UUID) -> dict[str, Any]:
    sub = subscription(db, org_id)
    if sub.status == "trialing" and sub.trial_ends_at and sub.trial_ends_at < utcnow():
        return {**PLANS["trial"], "expired": True, "plan": sub.plan, "status": "trial_expired"}
    if sub.status not in ACTIVE_STATUSES:
        return {**{k: 0 for k in PLANS["trial"] if k != "label"}, "label": "Inactive", "plan": sub.plan, "status": sub.status, "expired": True}
    return {**PLANS.get(sub.plan, PLANS["trial"]), "plan": sub.plan, "status": sub.status, "expired": False}


def _month_start() -> datetime:
    n = utcnow()
    return datetime(n.year, n.month, 1, tzinfo=timezone.utc)


def usage_total(db: Session, org_id: uuid.UUID, meter: str, since: datetime | None = None) -> float:
    since = since or _month_start()
    return float(db.execute(select(func.coalesce(func.sum(UsageEvent.quantity), 0)).where(
        UsageEvent.org_id == org_id, UsageEvent.meter == meter, UsageEvent.occurred_at >= since)).scalar() or 0)


def check_entitlement(db: Session, org_id: uuid.UUID, feature: str, adding: float = 1) -> None:
    lim = plan_limits(db, org_id)
    if lim.get("expired"):
        raise EntitlementExceeded("Your subscription is not active. Update billing to continue.", details={"status": lim["status"]})
    if feature == "technicians":
        n = db.execute(select(func.count()).select_from(Membership).where(
            Membership.org_id == org_id, Membership.role.in_([Role.TECHNICIAN, Role.ADMIN]))).scalar() or 0
        if n + adding > lim["technicians"]:
            raise EntitlementExceeded(f"Plan allows {lim['technicians']} technicians", details={"feature": feature})
    elif feature == "devices":
        n = db.execute(select(func.count()).select_from(Device).where(Device.status != "revoked")).scalar() or 0
        if n + adding > lim["devices"]:
            raise EntitlementExceeded(f"Plan allows {lim['devices']} devices", details={"feature": feature})
    elif feature == "ai_usd":
        if usage_total(db, org_id, "ai_usd") + adding > lim["ai_usd_month"]:
            raise EntitlementExceeded("Monthly AI budget for your plan is exhausted", details={"feature": feature})
    elif feature == "research_requests":
        if usage_total(db, org_id, "research_requests", utcnow() - timedelta(days=1)) + adding > lim["research_per_day"]:
            raise EntitlementExceeded("Daily research request limit reached", details={"feature": feature})
    elif feature in ("browser_minutes", "desktop_minutes"):
        if usage_total(db, org_id, feature) + adding > lim[feature]:
            raise EntitlementExceeded(f"Monthly {feature.replace('_', ' ')} exhausted", details={"feature": feature})
    elif feature == "storage_bytes":
        if usage_total(db, org_id, "storage_bytes", datetime(2000, 1, 1, tzinfo=timezone.utc)) + adding > lim["storage_gb"] * 1024**3:
            raise EntitlementExceeded("Storage quota exceeded", details={"feature": feature})


def meter(db: Session, org_id: uuid.UUID, meter_name: str, quantity: float, idempotency_key: str, meta: dict | None = None) -> bool:
    """Record usage exactly once per idempotency key. Returns False if it was a duplicate."""
    if meter_name not in METERS:
        raise ValueError(f"unknown meter {meter_name}")
    res = db.execute(insert(UsageEvent).values(
        id=uuid.uuid4(), org_id=org_id, meter=meter_name, quantity=quantity, idempotency_key=idempotency_key,
        occurred_at=utcnow(), meta=meta or {},
    ).on_conflict_do_nothing(index_elements=["org_id", "idempotency_key"]))
    return bool(res.rowcount)


# ---------------- Stripe ----------------
def _stripe():
    s = get_settings()
    if not s.stripe_secret_key:
        raise Unavailable("Stripe is not configured. Set JOLTIQ_STRIPE_SECRET_KEY (test key sk_test_...) to enable billing.",
                          code="billing_not_configured")
    if s.stripe_secret_key.startswith("sk_live") and not s.stripe_live_mode_authorized:
        raise Unavailable("Live Stripe key present but live mode not authorized (JOLTIQ_STRIPE_LIVE_MODE_AUTHORIZED).",
                          code="live_mode_not_authorized")
    import stripe

    stripe.api_key = s.stripe_secret_key
    return stripe


def price_lookup_key(plan: str, interval: str) -> str:
    return f"joltiq_{plan}_{interval}"


def create_checkout(db: Session, org: Organization, plan: str, interval: str, success_url: str, cancel_url: str) -> str:
    if plan not in ("starter", "growth", "scale"):
        raise AppError("Choose starter, growth or scale. Enterprise is sold by contract.", code="validation_error")
    if interval not in ("month", "year"):
        raise AppError("interval must be month or year", code="validation_error")
    stripe = _stripe()
    sub = subscription(db, org.id)
    prices = stripe.Price.list(lookup_keys=[price_lookup_key(plan, interval)], active=True, limit=1)
    if not prices.data:
        raise Unavailable(f"No active Stripe price with lookup key {price_lookup_key(plan, interval)}. Run scripts/stripe_bootstrap.py.",
                          code="price_missing")
    if not sub.stripe_customer_id:
        cust = stripe.Customer.create(name=org.name, metadata={"org_id": str(org.id)}, idempotency_key=f"cust-{org.id}")
        sub.stripe_customer_id = cust.id
    session = stripe.checkout.Session.create(
        mode="subscription", customer=sub.stripe_customer_id, line_items=[{"price": prices.data[0].id, "quantity": 1}],
        success_url=success_url, cancel_url=cancel_url, client_reference_id=str(org.id),
        subscription_data={"metadata": {"org_id": str(org.id), "plan": plan}},
    )
    return session.url


def create_portal(db: Session, org: Organization, return_url: str) -> str:
    stripe = _stripe()
    sub = subscription(db, org.id)
    if not sub.stripe_customer_id:
        raise AppError("No billing account yet — start a subscription first", code="no_customer")
    return stripe.billing_portal.Session.create(customer=sub.stripe_customer_id, return_url=return_url).url


def verify_webhook(payload: bytes, sig_header: str) -> dict:
    s = get_settings()
    if not s.stripe_webhook_secret:
        raise Unavailable("Stripe webhook secret not configured", code="billing_not_configured")
    import stripe

    try:
        event = stripe.Webhook.construct_event(payload, sig_header, s.stripe_webhook_secret)
    except Exception as e:  # invalid signature or payload
        raise AppError("Invalid Stripe signature", code="invalid_signature", status=400) from e
    return event.to_dict() if hasattr(event, "to_dict") else dict(event)


def process_event(db: Session, event: dict) -> str:
    """Idempotent: the same Stripe event id is applied at most once; failures are retried by Stripe."""
    from ..db import set_tenant

    existing = db.execute(select(BillingEvent).where(BillingEvent.stripe_event_id == event["id"])).scalar_one_or_none()
    if existing and existing.processed_at:
        return "duplicate"
    if existing is None:
        existing = BillingEvent(stripe_event_id=event["id"], type=event["type"], payload={"type": event["type"]})
        db.add(existing)
        db.flush()
    existing.attempts += 1
    obj = event["data"]["object"]
    set_tenant(db, None, platform=True)
    org_id = None
    md = obj.get("metadata") or {}
    if md.get("org_id"):
        org_id = uuid.UUID(md["org_id"])
    elif obj.get("client_reference_id"):
        org_id = uuid.UUID(obj["client_reference_id"])
    elif obj.get("customer"):
        sub = db.execute(select(Subscription).where(Subscription.stripe_customer_id == obj["customer"])).scalar_one_or_none()
        org_id = sub.org_id if sub else None
    existing.org_id = org_id
    t = event["type"]
    if org_id:
        sub = subscription(db, org_id)
        org = db.get(Organization, org_id)
        if t in ("customer.subscription.created", "customer.subscription.updated", "customer.subscription.deleted"):
            sub.stripe_subscription_id = obj.get("id")
            sub.status = obj.get("status", sub.status) if t != "customer.subscription.deleted" else "canceled"
            sub.cancel_at_period_end = bool(obj.get("cancel_at_period_end"))
            plan = md.get("plan")
            items = (obj.get("items") or {}).get("data") or []
            if items:
                lk = ((items[0].get("price") or {}).get("lookup_key") or "")
                if lk.startswith("joltiq_"):
                    _, plan, interval = lk.split("_", 2)
                    sub.interval = interval
            if plan and plan in PLANS:
                sub.plan = plan
                if org:
                    org.plan = plan
            if obj.get("current_period_end"):
                sub.current_period_end = datetime.fromtimestamp(obj["current_period_end"], tz=timezone.utc)
            sub.live_mode = bool(event.get("livemode"))
        elif t == "checkout.session.completed":
            if obj.get("customer"):
                sub.stripe_customer_id = obj["customer"]
        elif t in ("invoice.paid", "invoice.payment_failed"):
            sub.latest_invoice_ref = obj.get("id")
            if t == "invoice.payment_failed":
                sub.status = "past_due"
    existing.processed_at = utcnow()
    existing.error = None
    return "processed"
