"""Org admin: models/providers, AI budget, policies, branding, retention, notifications, billing, usage, analytics, audit,
real-time events (SSE). Platform admin: tenants, plans, workers, queue, costs, security events."""
from __future__ import annotations

import asyncio
import json
import uuid
from datetime import timedelta

from fastapi import APIRouter, Depends, Header, Query, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..config import get_settings
from ..core.errors import AppError, NotFound
from ..db import get_raw_session
from ..deps import Ctx, get_ctx, get_platform_ctx, perm
from ..models import (
    AIBudget,
    AuditEvent,
    Credential,
    Job,
    ModelCall,
    ModelProviderConfig,
    Notification,
    NotificationPreference,
    Organization,
    PolicyRule,
    SecurityEvent,
    Subscription,
    UsageEvent,
    User,
    WebhookEndpoint,
    utcnow,
)
from ..models.enums import RiskLevel
from ..services import analytics, audit, billing, browser_client, research
from ..services.ai.providers import DEFAULT_MODELS, test_connectivity
from ..services.integrations import store_credential
from ..services.jobs import queue_stats
from ..services.realtime import broker
from .common import page_params, paginate

router = APIRouter(tags=["admin"])

# ======================= Models / AI =======================
PROVIDERS = ["openai", "anthropic", "gemini", "ollama", "local_fixture"]


def ser_provider(c: ModelProviderConfig, db: Session) -> dict:
    cred = db.get(Credential, c.credential_id) if c.credential_id else None
    s = get_settings()
    platform_key = bool({"openai": s.openai_api_key, "anthropic": s.anthropic_api_key, "gemini": s.gemini_api_key,
                         "ollama": s.ollama_base_url}.get(c.provider))
    return {"provider": c.provider, "enabled": c.enabled, "approved_for_tenant_data": c.approved_for_tenant_data, "models": c.models,
            "input_cost_per_mtok": c.input_cost_per_mtok, "output_cost_per_mtok": c.output_cost_per_mtok, "timeout_seconds": c.timeout_seconds,
            "max_retries": c.max_retries, "key_fingerprint": cred.fingerprint if cred and not cred.revoked_at else None,
            "platform_key_available": platform_key, "last_test_at": c.last_test_at.isoformat() if c.last_test_at else None,
            "last_test_ok": c.last_test_ok, "last_test_error": c.last_test_error,
            "is_test_fixture": c.provider == "local_fixture", "default_models": DEFAULT_MODELS.get(c.provider, {})}


def _org(ctx: Ctx) -> Organization:
    org = ctx.db.get(Organization, ctx.org_id)
    if org is None:
        raise NotFound("Organization not found")
    return org


def _cfg(db: Session, org_id, provider: str) -> ModelProviderConfig:
    if provider not in PROVIDERS:
        raise NotFound("Unknown provider")
    c = db.execute(select(ModelProviderConfig).where(ModelProviderConfig.provider == provider)).scalar_one_or_none()
    if c is None:
        c = ModelProviderConfig(org_id=org_id, provider=provider, models={})
        db.add(c)
        db.flush()
    return c


@router.get("/admin/models")
def list_models(ctx: Ctx = Depends(perm("models.manage"))):
    out = [ser_provider(_cfg(ctx.db, ctx.org_id, p), ctx.db) for p in PROVIDERS if p != "local_fixture" or get_settings().env != "production"]
    b = ctx.db.execute(select(AIBudget)).scalar_one_or_none()
    return {"providers": out, "budget": {"monthly_usd_limit": b.monthly_usd_limit, "per_run_usd_limit": b.per_run_usd_limit,
                                         "research_requests_per_day": b.research_requests_per_day, "routing": b.routing} if b else None,
            "research": research.provider_status()}


class ProviderIn(BaseModel):
    enabled: bool | None = None
    approved_for_tenant_data: bool | None = None
    api_key: str | None = Field(default=None, max_length=500)
    models: dict[str, str] | None = None
    input_cost_per_mtok: float | None = Field(default=None, ge=0)
    output_cost_per_mtok: float | None = Field(default=None, ge=0)
    timeout_seconds: int | None = Field(default=None, ge=5, le=300)
    max_retries: int | None = Field(default=None, ge=0, le=5)


@router.patch("/admin/models/{provider}")
def update_provider(provider: str, body: ProviderIn, ctx: Ctx = Depends(perm("models.manage"))):
    c = _cfg(ctx.db, ctx.org_id, provider)
    if body.api_key:
        if c.credential_id:
            old = ctx.db.get(Credential, c.credential_id)
            if old:
                old.revoked_at = utcnow()
        c.credential_id = store_credential(ctx.db, ctx.org_id, f"model:{provider}", "api_key", body.api_key.strip()).id
        c.last_test_ok = None
    for k in ("enabled", "approved_for_tenant_data", "models", "input_cost_per_mtok", "output_cost_per_mtok", "timeout_seconds", "max_retries"):
        v = getattr(body, k)
        if v is not None:
            setattr(c, k, v)
    audit.record(ctx.db, org_id=ctx.org_id, actor_kind="user", actor_id=ctx.p.user_id, action="models.updated", resource_type="model_provider",
                 resource_id=c.id, data=body.model_dump(exclude={"api_key"}, exclude_none=True) | {"key_changed": bool(body.api_key)})
    return ser_provider(c, ctx.db)


@router.post("/admin/models/{provider}/test")
def test_provider(provider: str, ctx: Ctx = Depends(perm("models.manage"))):
    from ..services.ai.providers import provider_credentials

    c = _cfg(ctx.db, ctx.org_id, provider)
    if provider == "local_fixture":
        return {"ok": True, "detail": "local test fixture (not an AI model)"}
    if provider == "ollama":
        import httpx

        base = get_settings().ollama_base_url
        if not c.enabled or not base:
            c.last_test_ok, c.last_test_error = False, "Ollama is disabled or its deployment URL is not configured"
            return {"ok": False, "detail": c.last_test_error}
        try:
            response = httpx.get(f"{base.rstrip('/')}/api/tags", timeout=20)
            response.raise_for_status()
            names = [m.get("name") for m in response.json().get("models", [])]
            model = (c.models or {}).get("computer_use") or get_settings().ollama_model
            ok, detail = any(n == model or str(n).startswith(model + ":") for n in names), f"local gateway reachable; model {model}"
            if not ok:
                detail += " is not installed"
        except (httpx.HTTPError, ValueError) as e:
            ok, detail = False, f"Ollama connection failed: {type(e).__name__}"
        c.last_test_at, c.last_test_ok, c.last_test_error = utcnow(), ok, None if ok else detail
        return {"ok": ok, "detail": detail}
    key = provider_credentials(ctx.db, ctx.org_id, provider, require_tenant_data_approval=False) if c.enabled else None
    if not key:
        c.last_test_ok, c.last_test_error = False, "Provider is disabled or has no API key"
        return {"ok": False, "detail": c.last_test_error}
    model = (c.models or {}).get("default") or DEFAULT_MODELS[provider]["default"]
    ok, detail = test_connectivity(provider, key, model)
    c.last_test_at, c.last_test_ok, c.last_test_error = utcnow(), ok, None if ok else detail
    return {"ok": ok, "detail": detail}


class BudgetIn(BaseModel):
    monthly_usd_limit: float | None = Field(default=None, ge=0)
    per_run_usd_limit: float | None = Field(default=None, ge=0)
    research_requests_per_day: int | None = Field(default=None, ge=0)
    routing: dict[str, list[str]] | None = None


@router.patch("/admin/ai-budget")
def update_budget(body: BudgetIn, ctx: Ctx = Depends(perm("models.manage"))):
    b = ctx.db.execute(select(AIBudget)).scalar_one_or_none() or AIBudget(org_id=ctx.org_id)
    ctx.db.add(b)
    if body.routing is not None:
        for task, order in body.routing.items():
            if any(p not in PROVIDERS for p in order):
                raise AppError(f"unknown provider in routing for {task}", code="validation_error")
    for k, v in body.model_dump(exclude_none=True).items():
        setattr(b, k, v)
    return {"ok": True}


# ======================= Policies =======================
class RuleIn(BaseModel):
    name: str = Field(min_length=2, max_length=120)
    priority: int = Field(default=100, ge=0, le=10000)
    effect: str = Field(pattern="^(allow|deny|require_approval)$")
    match: dict = Field(default_factory=dict)
    approver_roles: list[str] = Field(default_factory=list)
    enabled: bool = True


ALLOWED_MATCH = {"action", "actions", "channel", "risk_at_least", "risk_at_most", "device_id", "role", "param_equals", "domain"}


def ser_rule(r: PolicyRule) -> dict:
    return {"id": str(r.id), "name": r.name, "priority": r.priority, "effect": r.effect, "match": r.match, "approver_roles": r.approver_roles, "enabled": r.enabled}


@router.get("/admin/policies")
def list_rules(ctx: Ctx = Depends(perm("policies.manage"))):
    return [ser_rule(r) for r in ctx.db.execute(select(PolicyRule).order_by(PolicyRule.priority)).scalars()]


def _validate_rule(body: RuleIn) -> None:
    bad = set(body.match) - ALLOWED_MATCH
    if bad:
        raise AppError(f"unknown match keys {sorted(bad)}", code="validation_error")
    for k in ("risk_at_least", "risk_at_most"):
        if k in body.match:
            RiskLevel(body.match[k])
    if body.effect == "allow" and body.match.get("risk_at_least") == "DESTRUCTIVE":
        raise AppError("Destructive actions cannot be auto-allowed", code="validation_error")
    if set(body.approver_roles) - {"customer", "technician", "admin", "org_owner"}:
        raise AppError("invalid approver role", code="validation_error")


@router.post("/admin/policies", status_code=201)
def create_rule(body: RuleIn, ctx: Ctx = Depends(perm("policies.manage"))):
    _validate_rule(body)
    r = PolicyRule(org_id=ctx.org_id, **body.model_dump())
    ctx.db.add(r)
    ctx.db.flush()
    audit.record(ctx.db, org_id=ctx.org_id, actor_kind="user", actor_id=ctx.p.user_id, action="policy.created", resource_type="policy_rule", resource_id=r.id, data=body.model_dump())
    return ser_rule(r)


@router.put("/admin/policies/{rid}")
def update_rule(rid: uuid.UUID, body: RuleIn, ctx: Ctx = Depends(perm("policies.manage"))):
    _validate_rule(body)
    r = ctx.db.get(PolicyRule, rid)
    if not r:
        raise NotFound("Rule not found")
    for k, v in body.model_dump().items():
        setattr(r, k, v)
    audit.record(ctx.db, org_id=ctx.org_id, actor_kind="user", actor_id=ctx.p.user_id, action="policy.updated", resource_type="policy_rule", resource_id=r.id, data=body.model_dump())
    return ser_rule(r)


@router.delete("/admin/policies/{rid}")
def delete_rule(rid: uuid.UUID, ctx: Ctx = Depends(perm("policies.manage"))):
    r = ctx.db.get(PolicyRule, rid)
    if not r:
        raise NotFound("Rule not found")
    ctx.db.delete(r)
    audit.record(ctx.db, org_id=ctx.org_id, actor_kind="user", actor_id=ctx.p.user_id, action="policy.deleted", resource_type="policy_rule", resource_id=rid)
    return {"ok": True}


class SimIn(BaseModel):
    action: str
    params: dict = Field(default_factory=dict)


@router.post("/admin/policies/simulate")
def simulate(body: SimIn, ctx: Ctx = Depends(perm("policies.manage"))):
    """Dry-run the policy engine for an action, as if inside an active session with SENSITIVE consent."""
    from ..models import SupportSession
    from ..services import policy
    from ..services.actions.registry import ACTIONS

    spec = ACTIONS.get(body.action)
    s = SupportSession(org_id=ctx.org_id, kind="browser" if spec and spec.channel == "browser" else "desktop", state="active",
                       owner_id=ctx.p.user_id, allowed_domains=["example.com"], consent={"max_risk": "PRIVILEGED"})
    from ..models import Device

    d = Device(org_id=ctx.org_id, hostname="sim", status="active", capabilities=[body.action])
    return policy.evaluate(ctx.db, ctx.org_id, body.action, body.params, session=s, device=d).as_dict()


# ======================= Branding / retention =======================
class BrandingIn(BaseModel):
    product_name: str | None = Field(default=None, max_length=60)
    logo_url: str | None = Field(default=None, max_length=500)
    favicon_url: str | None = Field(default=None, max_length=500)
    primary_color: str | None = Field(default=None, pattern=r"^#[0-9a-fA-F]{6}$")
    accent_color: str | None = Field(default=None, pattern=r"^#[0-9a-fA-F]{6}$")
    support_email: str | None = Field(default=None, max_length=320)
    support_phone: str | None = Field(default=None, max_length=40)
    email_footer: str | None = Field(default=None, max_length=500)
    custom_domain: str | None = Field(default=None, max_length=253, pattern=r"^[a-z0-9.-]+\.[a-z]{2,}$")


@router.get("/branding/public")
def public_branding(host: str = Query(default="", max_length=253), db: Session = Depends(get_raw_session)):
    """Unauthenticated: branding for a white-label custom domain (login page)."""
    from ..db import set_tenant

    set_tenant(db, None, platform=True)
    s = get_settings()
    org = db.execute(select(Organization).where(Organization.custom_domain == host.lower())).scalar_one_or_none() if host else None
    b = {"product_name": s.product_name, "support_email": s.support_email}
    if org:
        b.update({k: v for k, v in (org.branding or {}).items() if k in ("product_name", "logo_url", "favicon_url", "primary_color", "accent_color", "support_email")})
        b["organization"] = org.name
    return b


@router.patch("/admin/branding")
def update_branding(body: BrandingIn, ctx: Ctx = Depends(perm("branding.manage"))):
    org = _org(ctx)
    data = body.model_dump(exclude_none=True)
    dom = data.pop("custom_domain", None)
    for k in ("logo_url", "favicon_url"):
        if k in data and not data[k].startswith("https://"):
            raise AppError(f"{k} must be an https URL", code="validation_error")
    if dom is not None:
        taken = ctx.db.execute(select(Organization.id).where(Organization.custom_domain == dom, Organization.id != org.id)).scalar()
        if taken:
            raise AppError("Domain already in use", code="conflict", status=409)
        org.custom_domain = dom
    org.branding = {**(org.branding or {}), **data}
    return {"branding": org.branding, "custom_domain": org.custom_domain,
            "custom_domain_dns": f"CNAME {org.custom_domain} → tenants.{get_settings().product_domain}" if org.custom_domain else None}


@router.post("/admin/retention/run")
def run_retention(ctx: Ctx = Depends(perm("settings.manage"))):
    from ..services.retention import apply_retention

    return apply_retention(ctx.db, ctx.org_id)


# ======================= Notifications =======================
@router.get("/notifications")
def list_notifications(ctx: Ctx = Depends(get_ctx), pp=Depends(page_params), unread: bool = False):
    stmt = select(Notification).where(Notification.user_id == ctx.p.user_id).order_by(Notification.created_at.desc())
    if unread:
        stmt = stmt.where(Notification.read_at.is_(None))
    page = paginate(ctx.db, stmt, pp, lambda n: {"id": str(n.id), "category": n.category, "title": n.title, "body": n.body, "link": n.link,
                                                 "read": n.read_at is not None, "at": n.created_at.isoformat()})
    page["unread_count"] = ctx.db.execute(select(func.count()).select_from(Notification).where(Notification.user_id == ctx.p.user_id, Notification.read_at.is_(None))).scalar()
    return page


class ReadIn(BaseModel):
    ids: list[uuid.UUID] | None = None


@router.post("/notifications/read")
def mark_read(body: ReadIn, ctx: Ctx = Depends(get_ctx)):
    stmt = select(Notification).where(Notification.user_id == ctx.p.user_id, Notification.read_at.is_(None))
    if body.ids:
        stmt = stmt.where(Notification.id.in_(body.ids))
    n = 0
    for x in ctx.db.execute(stmt).scalars():
        x.read_at = utcnow()
        n += 1
    return {"marked": n}


class PrefIn(BaseModel):
    category: str
    in_app: bool = True
    email: bool = True


@router.get("/notifications/preferences")
def prefs(ctx: Ctx = Depends(get_ctx)):
    from ..services.notifications import CATEGORIES

    have = {p.category: p for p in ctx.db.execute(select(NotificationPreference).where(NotificationPreference.user_id == ctx.p.user_id)).scalars()}
    return [{"category": c, "in_app": have[c].in_app if c in have else True, "email": have[c].email if c in have else True} for c in CATEGORIES]


@router.put("/notifications/preferences")
def set_pref(body: PrefIn, ctx: Ctx = Depends(get_ctx)):
    from ..services.notifications import CATEGORIES

    if body.category not in CATEGORIES:
        raise AppError("unknown category", code="validation_error")
    if body.category == "security" and not body.email:
        raise AppError("Security emails cannot be disabled", code="validation_error")
    p = ctx.db.execute(select(NotificationPreference).where(NotificationPreference.user_id == ctx.p.user_id, NotificationPreference.category == body.category)).scalar_one_or_none()
    if p is None:
        p = NotificationPreference(org_id=ctx.org_id, user_id=ctx.p.user_id, category=body.category)
        ctx.db.add(p)
    p.in_app, p.email = body.in_app, body.email
    return {"ok": True}


class WebhookIn(BaseModel):
    url: str = Field(max_length=2000)
    categories: list[str] = Field(default_factory=list)


@router.get("/admin/webhooks")
def list_webhooks(ctx: Ctx = Depends(perm("integrations.manage"))):
    return [{"id": str(w.id), "url": w.url, "categories": w.categories, "enabled": w.enabled} for w in ctx.db.execute(select(WebhookEndpoint)).scalars()]


@router.post("/admin/webhooks", status_code=201)
def add_webhook(body: WebhookIn, ctx: Ctx = Depends(perm("integrations.manage"))):
    from ..security.crypto import new_token
    from ..security.ssrf import SSRFBlocked, validate_url

    try:
        validate_url(body.url, allow_private=get_settings().env != "production")
    except SSRFBlocked as e:
        raise AppError(f"URL not allowed: {e}", code="url_blocked") from e
    secret = new_token(32)
    w = WebhookEndpoint(org_id=ctx.org_id, url=body.url, categories=body.categories,
                        credential_id=store_credential(ctx.db, ctx.org_id, "webhook", "webhook_secret", secret).id)
    ctx.db.add(w)
    ctx.db.flush()
    return {"id": str(w.id), "url": w.url, "signing_secret": secret, "note": "Store this secret now; it is not shown again."}


@router.delete("/admin/webhooks/{wid}")
def del_webhook(wid: uuid.UUID, ctx: Ctx = Depends(perm("integrations.manage"))):
    w = ctx.db.get(WebhookEndpoint, wid)
    if not w:
        raise NotFound("not found")
    w.enabled = False
    return {"ok": True}


# ======================= Billing / usage =======================
@router.get("/billing")
def billing_overview(ctx: Ctx = Depends(perm("billing.manage"))):
    sub = billing.subscription(ctx.db, ctx.org_id)
    lim = billing.plan_limits(ctx.db, ctx.org_id)
    usage = {m: billing.usage_total(ctx.db, ctx.org_id, m) for m in sorted(billing.METERS)}
    s = get_settings()
    return {"subscription": {"plan": sub.plan, "interval": sub.interval, "status": sub.status, "trial_ends_at": sub.trial_ends_at.isoformat() if sub.trial_ends_at else None,
                             "current_period_end": sub.current_period_end.isoformat() if sub.current_period_end else None,
                             "cancel_at_period_end": sub.cancel_at_period_end, "latest_invoice_ref": sub.latest_invoice_ref, "live_mode": sub.live_mode},
            "limits": lim, "usage_this_month": usage,
            "plans": {k: {kk: vv for kk, vv in v.items()} for k, v in billing.PLANS.items()},
            "prices_note": "Prices are hypothetical placeholders from the product brief and have not been validated.",
            "stripe": {"configured": bool(s.stripe_secret_key), "mode": "live" if (s.stripe_secret_key or "").startswith("sk_live") else "test" if s.stripe_secret_key else None}}


class CheckoutIn(BaseModel):
    plan: str
    interval: str = "month"


@router.post("/billing/checkout")
def checkout(body: CheckoutIn, ctx: Ctx = Depends(perm("billing.manage"))):
    org = _org(ctx)
    o = get_settings().web_origin
    return {"url": billing.create_checkout(ctx.db, org, body.plan, body.interval, f"{o}/admin/billing?checkout=success", f"{o}/admin/billing?checkout=cancelled")}


@router.post("/billing/portal")
def portal(ctx: Ctx = Depends(perm("billing.manage"))):
    org = _org(ctx)
    return {"url": billing.create_portal(ctx.db, org, f"{get_settings().web_origin}/admin/billing")}


@router.post("/billing/webhook", include_in_schema=False)
async def stripe_webhook(request: Request, db: Session = Depends(get_raw_session), stripe_signature: str = Header(default="", alias="Stripe-Signature")):
    payload = await request.body()
    event = billing.verify_webhook(payload, stripe_signature)
    return {"result": billing.process_event(db, event)}


@router.get("/usage")
def usage(ctx: Ctx = Depends(perm("billing.manage")), days: int = Query(30, ge=1, le=366)):
    since = utcnow() - timedelta(days=days)
    rows = ctx.db.execute(select(UsageEvent.meter, func.date_trunc("day", UsageEvent.occurred_at).label("d"), func.sum(UsageEvent.quantity))
                          .where(UsageEvent.occurred_at >= since).group_by(UsageEvent.meter, "d").order_by("d")).all()
    return [{"meter": m, "date": d.date().isoformat(), "quantity": float(q)} for m, d, q in rows]


# ======================= Analytics / audit =======================
@router.get("/analytics/dashboard")
def dashboard(ctx: Ctx = Depends(perm("analytics.read")), days: int = Query(30, ge=1, le=365)):
    return analytics.dashboard(ctx.db, days)


@router.get("/audit")
def audit_log(ctx: Ctx = Depends(perm("audit.read")), pp=Depends(page_params), action: str | None = None, resource_type: str | None = None):
    stmt = select(AuditEvent).where(AuditEvent.org_id == ctx.org_id).order_by(AuditEvent.id.desc())
    if action:
        stmt = stmt.where(AuditEvent.action.like(f"{action}%"))
    if resource_type:
        stmt = stmt.where(AuditEvent.resource_type == resource_type)
    return paginate(ctx.db, stmt, pp, lambda e: {"id": e.id, "at": e.at.isoformat(), "actor_kind": e.actor_kind, "actor_id": e.actor_id, "action": e.action,
                                                 "resource_type": e.resource_type, "resource_id": e.resource_id, "data": e.data, "request_id": e.request_id, "hash": e.hash[:16]})


@router.get("/audit/verify")
def audit_verify(ctx: Ctx = Depends(perm("audit.read"))):
    return audit.verify_chain(ctx.db, ctx.org_id)


@router.get("/audit/export")
def audit_export(ctx: Ctx = Depends(perm("audit.read"))):
    def gen():
        for e in ctx.db.execute(select(AuditEvent).where(AuditEvent.org_id == ctx.org_id).order_by(AuditEvent.id)).scalars():
            yield json.dumps({"id": e.id, "at": e.at.isoformat(), "actor_kind": e.actor_kind, "actor_id": e.actor_id, "action": e.action,
                              "resource_type": e.resource_type, "resource_id": e.resource_id, "data": e.data, "prev_hash": e.prev_hash, "hash": e.hash}) + "\n"
    return StreamingResponse(gen(), media_type="application/x-ndjson", headers={"content-disposition": "attachment; filename=audit.ndjson"})


# ======================= Real-time (SSE) =======================
@router.get("/events/stream")
async def events_stream(request: Request, ctx: Ctx = Depends(get_ctx)):
    """Server-Sent Events for the current org, filtered by audience (customers only receive their own / customer-visible events)."""
    org, uid, is_customer = str(ctx.org_id), str(ctx.p.user_id), ctx.p.is_customer
    ctx.db.commit()
    ctx.db.close()

    async def gen():
        sid, q = broker.subscribe(org)
        try:
            yield f"event: ready\ndata: {json.dumps({'org_id': org})}\n\n"
            while True:
                if await request.is_disconnected():
                    break
                try:
                    msg = await asyncio.wait_for(q.get(), timeout=15)
                except TimeoutError:
                    yield ": keepalive\n\n"
                    continue
                aud = msg.get("audience", "all")
                if aud.startswith("user:") and aud != f"user:{uid}":
                    continue
                if aud == "staff" and is_customer:
                    continue
                yield f"event: {msg['kind']}\ndata: {json.dumps(msg['data'], default=str)}\n\n"
        finally:
            broker.unsubscribe(sid)

    return StreamingResponse(gen(), media_type="text/event-stream", headers={"cache-control": "no-store", "x-accel-buffering": "no"})


# ======================= Platform admin =======================
@router.get("/admin/platform/tenants")
def tenants(ctx: Ctx = Depends(get_platform_ctx), pp=Depends(page_params)):
    stmt = select(Organization).order_by(Organization.created_at.desc())

    def m(o: Organization):
        sub = ctx.db.execute(select(Subscription).where(Subscription.org_id == o.id)).scalar_one_or_none()
        cost = ctx.db.execute(select(func.coalesce(func.sum(ModelCall.cost_usd), 0)).where(ModelCall.org_id == o.id,
                              ModelCall.created_at >= utcnow() - timedelta(days=30))).scalar()
        return {"id": str(o.id), "name": o.name, "status": o.status, "plan": sub.plan if sub else o.plan, "sub_status": sub.status if sub else None,
                "ai_cost_30d": round(float(cost or 0), 4), "created_at": o.created_at.isoformat()}
    return paginate(ctx.db, stmt, pp, m)


class TenantPatch(BaseModel):
    status: str | None = Field(default=None, pattern="^(active|suspended)$")
    plan: str | None = None


@router.patch("/admin/platform/tenants/{oid}")
def patch_tenant(oid: uuid.UUID, body: TenantPatch, ctx: Ctx = Depends(get_platform_ctx)):
    o = ctx.db.get(Organization, oid)
    if not o:
        raise NotFound("tenant not found")
    if body.status:
        o.status = body.status
    if body.plan:
        if body.plan not in billing.PLANS:
            raise AppError("unknown plan", code="validation_error")
        sub = billing.subscription(ctx.db, oid)
        sub.plan, sub.status, o.plan = body.plan, "active", body.plan
    audit.record(ctx.db, org_id=oid, actor_kind="user", actor_id=ctx.p.user_id, action="platform.tenant_updated", resource_type="organization",
                 resource_id=oid, data=body.model_dump(exclude_none=True))
    return {"ok": True}


@router.get("/admin/platform/health")
def platform_health(ctx: Ctx = Depends(get_platform_ctx)):
    dead = ctx.db.execute(select(Job).where(Job.status == "dead").order_by(Job.updated_at.desc()).limit(20)).scalars()
    return {"workers": analytics.worker_health(ctx.db), "queue": queue_stats(ctx.db), "browser_worker": browser_client.health(),
            "dead_letters": [{"id": str(j.id), "kind": j.kind, "attempts": j.attempts, "error": (j.last_error or "")[:300], "at": j.updated_at.isoformat()} for j in dead],
            "users": ctx.db.execute(select(func.count()).select_from(User)).scalar(),
            "research": research.provider_status()}


@router.post("/admin/platform/jobs/{jid}/requeue")
def requeue(jid: uuid.UUID, ctx: Ctx = Depends(get_platform_ctx)):
    j = ctx.db.get(Job, jid)
    if not j or j.status != "dead":
        raise NotFound("dead job not found")
    j.status, j.attempts, j.run_after = "queued", 0, utcnow()
    return {"ok": True}


@router.get("/admin/platform/costs")
def platform_costs(ctx: Ctx = Depends(get_platform_ctx), days: int = 30):
    rows = ctx.db.execute(select(ModelCall.provider, ModelCall.model, func.sum(ModelCall.cost_usd), func.count(), func.sum(func.cast(ModelCall.live, type_=_int())))
                          .where(ModelCall.created_at >= utcnow() - timedelta(days=days)).group_by(ModelCall.provider, ModelCall.model)).all()
    return [{"provider": p, "model": m, "cost_usd": round(float(c or 0), 4), "calls": n, "live_calls": int(lv or 0)} for p, m, c, n, lv in rows]


@router.get("/admin/platform/security-events")
def security_events(ctx: Ctx = Depends(get_platform_ctx), pp=Depends(page_params), severity: str | None = None):
    stmt = select(SecurityEvent).order_by(SecurityEvent.created_at.desc())
    if severity:
        stmt = stmt.where(SecurityEvent.severity == severity)
    return paginate(ctx.db, stmt, pp, lambda e: {"id": str(e.id), "kind": e.kind, "severity": e.severity, "org_id": str(e.org_id) if e.org_id else None,
                                                 "user_id": str(e.user_id) if e.user_id else None, "ip": e.ip, "data": e.data, "at": e.created_at.isoformat()})


def _int():
    from sqlalchemy import Integer

    return Integer()
