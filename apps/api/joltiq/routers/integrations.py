from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, Header, Request
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from ..core.errors import AppError, Unauthorized
from ..db import get_raw_session, set_tenant
from ..deps import Ctx, perm
from ..models import Integration
from ..security.crypto import hmac_sign, hmac_verify
from ..services import integrations as svc
from ..services import tickets as ticket_svc

router = APIRouter(prefix="/integrations", tags=["integrations"])


@router.get("/catalog")
def catalog(ctx: Ctx = Depends(perm("integrations.manage"))):
    from ..services.slack import _mock_base as slack_mock_base
    from ..services.teams import _mock_base as teams_mock_base

    return {"implemented": sorted(svc.IMPLEMENTED),
            "slack_mock_available": bool(slack_mock_base()), "teams_mock_available": bool(teams_mock_base()),
            "extension_points": [{"kind": k, "status": "not_implemented", "note": "Documented extension point; not available yet."}
                                 for k in sorted(svc.EXTENSION_POINTS)]}


@router.get("")
def list_integrations(ctx: Ctx = Depends(perm("integrations.manage"))):
    from sqlalchemy import select

    return [svc.ser(i, ctx.db) for i in ctx.db.execute(select(Integration).order_by(Integration.created_at)).scalars()]


class ConnectIn(BaseModel):
    kind: str = Field(max_length=40)
    name: str = Field(min_length=1, max_length=120)
    config: dict = Field(default_factory=dict)
    secret: str | None = Field(default=None, max_length=4000)
    scopes: list[str] = Field(default_factory=list)


@router.post("", status_code=201)
def connect(body: ConnectIn, ctx: Ctx = Depends(perm("integrations.manage"))):
    i = svc.connect(ctx.db, ctx.org_id, kind=body.kind, name=body.name, config=body.config, secret=body.secret, scopes=body.scopes, actor_id=ctx.p.user_id)
    out = svc.ser(i, ctx.db)
    if i.__dict__.get("_new_secret"):
        out["generated_secret"] = i.__dict__["_new_secret"]  # shown exactly once
    return out


@router.get("/{iid}")
def get(iid: uuid.UUID, ctx: Ctx = Depends(perm("integrations.manage"))):
    return svc.ser(svc.get(ctx.db, iid), ctx.db)


@router.post("/{iid}/test")
def test(iid: uuid.UUID, ctx: Ctx = Depends(perm("integrations.manage"))):
    i = svc.get(ctx.db, iid)
    return {"result": svc.test(ctx.db, i), "integration": svc.ser(i, ctx.db)}


@router.post("/{iid}/sync")
def sync(iid: uuid.UUID, ctx: Ctx = Depends(perm("integrations.manage"))):
    i = svc.get(ctx.db, iid)
    if i.kind != "freshdesk":
        raise AppError("This integration does not pull data", code="not_supported")
    if i.status != "connected":
        raise AppError("Test the connection successfully before syncing", code="not_connected")
    from ..models import utcnow

    sj = svc.queue_sync(ctx.db, i, "pull", "tickets", None, {}, f"fd-pull:{i.id}:{utcnow().strftime('%Y%m%d%H%M')}")
    return {"queued": sj is not None, "sync_job_id": str(sj.id) if sj else None}


@router.post("/{iid}/sync-jobs/{jid}/retry")
def retry(iid: uuid.UUID, jid: uuid.UUID, ctx: Ctx = Depends(perm("integrations.manage"))):
    from ..models import SyncJob
    from ..services.jobs import enqueue

    sj = ctx.db.get(SyncJob, jid)
    if not sj or sj.integration_id != iid:
        raise AppError("not found", status=404, code="not_found")
    sj.status, sj.conflict = "queued", None
    enqueue(ctx.db, "integration.sync", {"sync_job_id": str(sj.id)}, org_id=ctx.org_id)
    return {"ok": True}


class SecretIn(BaseModel):
    secret: str = Field(min_length=8, max_length=4000)


@router.post("/{iid}/rotate")
def rotate(iid: uuid.UUID, body: SecretIn, ctx: Ctx = Depends(perm("integrations.manage"))):
    i = svc.get(ctx.db, iid)
    if i.kind in {"slack", "teams"}:
        raise AppError(f"Disconnect and reconnect {i.kind.title()} to change its credential", code="validation_error")
    svc.rotate_credential(ctx.db, i, body.secret, ctx.p.user_id)
    i.status = "disconnected"  # must be re-tested
    return svc.ser(i, ctx.db)


class SlackSettingsIn(BaseModel):
    channel: str | None = Field(default=None, max_length=80)
    events: list[str] | None = None


@router.patch("/{iid}/slack")
def slack_settings(iid: uuid.UUID, body: SlackSettingsIn, ctx: Ctx = Depends(perm("integrations.manage"))):
    from ..services import audit, slack

    integration = svc.get(ctx.db, iid)
    if integration.kind != "slack" or integration.status == "revoked":
        raise AppError("Not an active Slack integration", code="validation_error")
    changes = slack.update_config(integration, body.model_dump(exclude_unset=True))
    audit.record(ctx.db, org_id=integration.org_id, actor_kind="user", actor_id=ctx.p.user_id,
                 action="integration.slack_settings_updated", resource_type="integration",
                 resource_id=integration.id, data={"changes": changes})
    return svc.ser(integration, ctx.db)


class TeamsSettingsIn(BaseModel):
    events: list[str]


@router.patch("/{iid}/teams")
def teams_settings(iid: uuid.UUID, body: TeamsSettingsIn, ctx: Ctx = Depends(perm("integrations.manage"))):
    from ..services import audit, teams

    integration = svc.get(ctx.db, iid)
    if integration.kind != "teams" or integration.status == "revoked":
        raise AppError("Not an active Teams integration", code="validation_error")
    changes = teams.update_config(integration, body.model_dump())
    audit.record(ctx.db, org_id=integration.org_id, actor_kind="user", actor_id=ctx.p.user_id,
                 action="integration.teams_settings_updated", resource_type="integration",
                 resource_id=integration.id, data={"changes": changes})
    return svc.ser(integration, ctx.db)


@router.post("/{iid}/revoke")
def revoke(iid: uuid.UUID, ctx: Ctx = Depends(perm("integrations.manage"))):
    i = svc.get(ctx.db, iid)
    svc.revoke(ctx.db, i, ctx.p.user_id)
    return svc.ser(i, ctx.db)


# ---------------- inbound (machine) endpoints ----------------
class InboundTicket(BaseModel):
    subject: str = Field(min_length=3, max_length=300)
    description: str = Field(default="", max_length=50_000)
    priority: str = Field(default="normal", pattern="^(low|normal|high|urgent)$")
    external_ref: str | None = Field(default=None, max_length=200)


def _inbound(db: Session, iid: uuid.UUID, auth: str, kind: str) -> Integration:
    set_tenant(db, None, platform=True)
    i = db.get(Integration, iid)
    if not i or i.kind != kind or i.status == "revoked":
        raise Unauthorized("invalid integration")
    set_tenant(db, i.org_id)
    token = auth[7:] if auth.lower().startswith("bearer ") else ""
    # constant-time comparison of the presented token with the stored (decrypted) secret
    if not token or not hmac_verify("cmp", token, hmac_sign("cmp", svc.secret_of(db, i))):
        raise Unauthorized("invalid integration credential")
    return i


@router.post("/inbound/{iid}/tickets", status_code=201)
def inbound_ticket(iid: uuid.UUID, body: InboundTicket, db: Session = Depends(get_raw_session), authorization: str = Header(default="")):
    i = _inbound(db, iid, authorization, "generic_rest")
    if body.external_ref:
        from sqlalchemy import select

        from ..models import Ticket

        ref = f"rest:{i.id}:{body.external_ref}"
        existing = db.execute(select(Ticket).where(Ticket.external_ref == ref)).scalar_one_or_none()
        if existing:
            return {"id": str(existing.id), "number": existing.number, "duplicate": True}
    t = ticket_svc.create(db, i.org_id, subject=body.subject, description=body.description, priority=body.priority, source="api",
                          actor_kind="integration", external_ref=f"rest:{i.id}:{body.external_ref}" if body.external_ref else None)
    return {"id": str(t.id), "number": t.number, "duplicate": False}


@router.post("/email/inbound/{iid}")
async def inbound_email(iid: uuid.UUID, request: Request, db: Session = Depends(get_raw_session), authorization: str = Header(default="")):
    """Receives a raw RFC 5322 message (e.g. from an SMTP relay / mail provider webhook) authenticated with the integration secret."""
    i = _inbound(db, iid, authorization, "email_intake")
    raw = await request.body()
    if len(raw) > 10 * 1024 * 1024:
        raise AppError("message too large", code="too_large")
    return svc.ingest_email(db, i.org_id, raw)
