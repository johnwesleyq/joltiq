"""Integrations: encrypted credentials, connect/test/scope/sync/retry/idempotency/conflict history/revoke/rotate.

Implemented adapters
  * freshdesk        — Freshdesk API v2 (Basic auth `api_key:X`): GET /api/v2/agents/me (test), GET /api/v2/tickets
                       (pull, `updated_since`), POST /api/v2/tickets/{id}/notes (push), PUT /api/v2/tickets/{id} (status).
  * generic_webhook  — outbound signed JSON webhooks (HMAC-SHA256 `x-joltiq-signature: v1=<hex>` over `ts.body`).
  * generic_rest     — inbound ticket creation via API key (`POST /api/v1/integrations/inbound/{id}/tickets`).
  * email_intake     — inbound email (RFC 5322) → ticket / comment via `POST /api/v1/integrations/email/inbound`.
Extension points (documented, NOT implemented — never shown as working): zendesk, servicenow, jira_sm, halopsa,
connectwise, intune, ninjaone, datto, m365, google_workspace, slack, teams. See docs/api/integrations.md.
"""
from __future__ import annotations

import base64
import email
import email.policy
import re
import uuid
from datetime import timedelta
from typing import Any

import httpx
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from ..config import get_settings
from ..core.errors import AppError, NotFound, Unavailable
from ..models import Credential, Integration, SyncJob, Ticket, TicketComment, utcnow
from ..models.enums import TicketStatus as TS
from ..security.crypto import decrypt_secret, encrypt_secret, fingerprint, new_token
from . import audit
from . import tickets as ticket_svc
from .jobs import enqueue, handler

IMPLEMENTED = {"freshdesk", "generic_webhook", "generic_rest", "email_intake"}
EXTENSION_POINTS = {"zendesk", "servicenow", "jira_service_management", "halopsa", "connectwise", "intune", "ninjaone", "datto",
                    "microsoft_365", "google_workspace", "slack", "teams"}
FRESHDESK_STATUS_TO_JQ = {2: TS.OPEN, 3: TS.AWAITING_CUSTOMER, 4: TS.RESOLVED, 5: TS.CLOSED}
JQ_TO_FRESHDESK = {TS.RESOLVED: 4, TS.CLOSED: 5, TS.AWAITING_CUSTOMER: 3, TS.OPEN: 2, TS.IN_PROGRESS: 2, TS.ESCALATED: 2}


# ---------------- credentials ----------------
def store_credential(db: Session, org_id, name: str, kind: str, secret: str) -> Credential:
    c = Credential(org_id=org_id, name=name, kind=kind, ciphertext=encrypt_secret(secret), fingerprint=fingerprint(secret))
    db.add(c)
    db.flush()
    return c


def rotate_credential(db: Session, integ: Integration, secret: str, actor_id) -> None:
    old = db.get(Credential, integ.credential_id) if integ.credential_id else None
    new = store_credential(db, integ.org_id, f"{integ.kind}:{integ.name}", old.kind if old else "api_key", secret)
    if old:
        old.revoked_at = utcnow()
    new.rotated_at = utcnow()
    integ.credential_id = new.id
    audit.record(db, org_id=integ.org_id, actor_kind="user", actor_id=actor_id, action="integration.secret_rotated", resource_type="integration",
                 resource_id=integ.id, data={"fingerprint": new.fingerprint})


def secret_of(db: Session, integ: Integration) -> str:
    c = db.get(Credential, integ.credential_id) if integ.credential_id else None
    if not c or c.revoked_at:
        raise Unavailable("Integration has no active credential", code="integration_no_credential")
    return decrypt_secret(c.ciphertext)


# ---------------- Freshdesk adapter ----------------
class Freshdesk:
    def __init__(self, domain: str, api_key: str, transport: httpx.BaseTransport | None = None, base_url: str | None = None):
        if not base_url:
            if not re.fullmatch(r"[a-z0-9-]{1,63}", domain or ""):
                raise AppError("Freshdesk domain must be the subdomain, e.g. 'acme' for acme.freshdesk.com", code="validation_error")
            base_url = f"https://{domain}.freshdesk.com"
        auth = base64.b64encode(f"{api_key}:X".encode()).decode()
        self.c = httpx.Client(base_url=base_url, headers={"authorization": f"Basic {auth}", "content-type": "application/json"},
                              timeout=20, transport=transport)

    def _r(self, method: str, path: str, **kw) -> Any:
        r = self.c.request(method, path, **kw)
        if r.status_code == 429:
            raise Unavailable(f"Freshdesk rate limited (retry after {r.headers.get('retry-after', '?')}s)", code="integration_rate_limited")
        if r.status_code >= 400:
            raise Unavailable(f"Freshdesk HTTP {r.status_code}: {r.text[:200]}", code="integration_error")
        return r.json() if r.content else None

    def me(self) -> dict:
        return self._r("GET", "/api/v2/agents/me")

    def tickets_since(self, since_iso: str | None, page: int = 1) -> list[dict]:
        params: dict[str, Any] = {"per_page": 50, "page": page, "order_by": "updated_at", "order_type": "asc", "include": "description"}
        if since_iso:
            params["updated_since"] = since_iso
        return self._r("GET", "/api/v2/tickets", params=params)

    def get_ticket(self, tid: str) -> dict:
        return self._r("GET", f"/api/v2/tickets/{tid}")

    def add_note(self, tid: str, body: str, private: bool) -> dict:
        return self._r("POST", f"/api/v2/tickets/{tid}/notes", json={"body": body, "private": private})

    def set_status(self, tid: str, status: int) -> dict:
        return self._r("PUT", f"/api/v2/tickets/{tid}", json={"status": status})


_TRANSPORT_OVERRIDE: dict[str, httpx.BaseTransport] = {}  # tests inject a labelled fake transport here


def freshdesk_for(db: Session, integ: Integration) -> Freshdesk:
    return Freshdesk(integ.config.get("domain", ""), secret_of(db, integ), transport=_TRANSPORT_OVERRIDE.get("freshdesk"),
                     base_url=integ.config.get("base_url_override") if get_settings().env != "production" else None)


# ---------------- lifecycle ----------------
def connect(db: Session, org_id, *, kind: str, name: str, config: dict, secret: str | None, scopes: list[str], actor_id) -> Integration:
    if kind in EXTENSION_POINTS:
        raise AppError(f"{kind} is a documented extension point and is not implemented yet", code="integration_not_implemented", status=501)
    if kind not in IMPLEMENTED:
        raise AppError("Unknown integration kind", code="validation_error")
    from .billing import PLANS, plan_limits

    n = len(db.execute(select(Integration.id).where(Integration.status != "revoked")).all())
    if n + 1 > plan_limits(db, org_id).get("integrations", PLANS["trial"]["integrations"]):
        from ..core.errors import EntitlementExceeded

        raise EntitlementExceeded("Your plan's integration limit is reached")
    integ = Integration(org_id=org_id, kind=kind, name=name, config=_clean_config(kind, config), scopes=scopes, status="disconnected")
    db.add(integ)
    db.flush()
    if kind in ("generic_rest", "generic_webhook") and not secret:
        secret = new_token(32)
    if secret:
        integ.credential_id = store_credential(db, org_id, f"{kind}:{name}", "api_key", secret).id
    audit.record(db, org_id=org_id, actor_kind="user", actor_id=actor_id, action="integration.created", resource_type="integration", resource_id=integ.id,
                 data={"kind": kind, "scopes": scopes})
    integ.__dict__["_new_secret"] = secret if kind in ("generic_rest", "generic_webhook") else None
    return integ


def _clean_config(kind: str, cfg: dict) -> dict:
    allowed = {"freshdesk": {"domain", "sync_direction", "push_notes", "push_status", "base_url_override"},
               "generic_webhook": {"url", "categories"}, "generic_rest": {"default_priority"}, "email_intake": {"address"}}[kind]
    out = {k: v for k, v in (cfg or {}).items() if k in allowed}
    if kind == "generic_webhook":
        from ..security.ssrf import SSRFBlocked, validate_url

        try:
            validate_url(out.get("url", ""), allow_private=get_settings().env != "production")
        except SSRFBlocked as e:
            raise AppError(f"Webhook URL not allowed: {e}", code="url_blocked") from e
    return out


def test(db: Session, integ: Integration) -> dict:
    integ.last_test_at = utcnow()
    try:
        if integ.kind == "freshdesk":
            me = freshdesk_for(db, integ).me()
            res = {"ok": True, "detail": f"authenticated as agent {me.get('id')}"}
        elif integ.kind == "generic_webhook":
            from .notifications import _deliver_webhook  # noqa: F401 - ensures handler registered
            r = httpx.post(integ.config["url"], json={"type": "joltiq.test", "id": uuid.uuid4().hex}, timeout=10)
            res = {"ok": r.status_code < 300, "detail": f"HTTP {r.status_code}"}
        else:
            res = {"ok": True, "detail": "inbound integration — ready to receive"}
    except (AppError, httpx.HTTPError) as e:
        res = {"ok": False, "detail": getattr(e, "message", str(e))[:300]}
    integ.last_test_result = res
    integ.status = "connected" if res["ok"] else "error"
    return res


def revoke(db: Session, integ: Integration, actor_id) -> None:
    integ.status = "revoked"
    if integ.credential_id:
        c = db.get(Credential, integ.credential_id)
        if c:
            c.revoked_at = utcnow()
    audit.record(db, org_id=integ.org_id, actor_kind="user", actor_id=actor_id, action="integration.revoked", resource_type="integration", resource_id=integ.id)


# ---------------- sync ----------------
def queue_sync(db: Session, integ: Integration, direction: str, entity: str, entity_id: str | None, request: dict, key: str) -> SyncJob | None:
    res = db.execute(insert(SyncJob).values(id=uuid.uuid4(), org_id=integ.org_id, integration_id=integ.id, direction=direction, entity=entity,
                                            entity_id=entity_id, status="queued", attempts=0, idempotency_key=key, request=request)
                     .on_conflict_do_nothing(index_elements=["org_id", "idempotency_key"]).returning(SyncJob.id))
    sid = res.scalar()
    if sid is None:
        return None  # duplicate — idempotent
    enqueue(db, "integration.sync", {"sync_job_id": str(sid)}, org_id=integ.org_id, max_attempts=6)
    return db.get(SyncJob, sid)


@handler("integration.sync")
def _run_sync(db: Session, payload: dict) -> None:
    sj = db.get(SyncJob, uuid.UUID(payload["sync_job_id"]))
    if not sj or sj.status in ("ok", "conflict"):
        return
    integ = db.get(Integration, sj.integration_id)
    if not integ or integ.status == "revoked":
        sj.status, sj.error = "dead", "integration revoked"
        return
    sj.attempts += 1
    try:
        if integ.kind == "freshdesk":
            fd = freshdesk_for(db, integ)
            if sj.direction == "pull":
                sj.response = _pull_freshdesk(db, integ, fd)
            elif sj.entity == "note":
                sj.response = fd.add_note(sj.request["external_id"], sj.request["body"], sj.request.get("private", True))
            elif sj.entity == "status":
                remote = fd.get_ticket(sj.request["external_id"])
                if remote.get("updated_at") and sj.request.get("expected_remote_updated_at") and remote["updated_at"] > sj.request["expected_remote_updated_at"] \
                        and remote.get("status") != sj.request.get("expected_remote_status"):
                    sj.status = "conflict"
                    sj.conflict = {"remote_status": remote.get("status"), "remote_updated_at": remote.get("updated_at"), "wanted": sj.request["status"]}
                    return
                sj.response = fd.set_status(sj.request["external_id"], sj.request["status"])
        sj.status, sj.error = "ok", None
        integ.last_sync_at = utcnow()
    except Exception as e:
        sj.status, sj.error = ("dead" if sj.attempts >= 6 else "failed"), str(e)[:500]
        if sj.status == "dead":
            from .notifications import notify, staff_user_ids

            notify(db, integ.org_id, staff_user_ids(db, integ.org_id, ("admin", "org_owner")), "integration",
                   f"Integration {integ.name} sync failing", sj.error, f"/admin/integrations/{integ.id}", email=True)
        db.commit()
        raise


def _pull_freshdesk(db: Session, integ: Integration, fd: Freshdesk) -> dict:
    created = updated = 0
    since = integ.sync_cursor
    newest = since
    for page in range(1, 11):
        items = fd.tickets_since(since, page)
        for ft in items:
            ref = f"freshdesk:{ft['id']}"
            t = db.execute(select(Ticket).where(Ticket.external_ref == ref)).scalar_one_or_none()
            if t is None:
                prio = {1: "low", 2: "normal", 3: "high", 4: "urgent"}.get(ft.get("priority"), "normal")
                ticket_svc.create(db, integ.org_id, subject=ft.get("subject") or "(no subject)", description=ft.get("description_text") or "",
                                  priority=prio, source="integration", actor_kind="integration", external_ref=ref)
                created += 1
            else:
                want = FRESHDESK_STATUS_TO_JQ.get(ft.get("status"))
                if want and t.status != want and want in ticket_svc.TRANSITIONS.get(TS(t.status), set()):
                    try:
                        ticket_svc.transition(db, t, want, actor_kind="integration", reason="updated in Freshdesk", force_close_reason="closed in Freshdesk")
                        updated += 1
                    except AppError:
                        pass
            if ft.get("updated_at") and (newest is None or ft["updated_at"] > newest):
                newest = ft["updated_at"]
        if len(items) < 50:
            break
    integ.sync_cursor = newest
    return {"created": created, "updated": updated, "cursor": newest}


def on_ticket_event(db: Session, t: Ticket, kind: str, data: dict) -> None:
    """Push JoltIQ changes back to the source system (idempotent keys)."""
    if not t.external_ref or not t.external_ref.startswith("freshdesk:"):
        return
    ext = t.external_ref.split(":", 1)[1]
    for integ in db.execute(select(Integration).where(Integration.kind == "freshdesk", Integration.status == "connected")).scalars():
        if kind == "comment" and integ.config.get("push_notes", True):
            queue_sync(db, integ, "push", "note", str(t.id), {"external_id": ext, "body": data["body"], "private": data.get("internal", True)},
                       f"fd-note:{data['comment_id']}")
        if kind == "status" and integ.config.get("push_status", True) and data["to"] in JQ_TO_FRESHDESK:
            queue_sync(db, integ, "push", "status", str(t.id), {"external_id": ext, "status": JQ_TO_FRESHDESK[data["to"]]},
                       f"fd-status:{t.id}:{data['to']}:{utcnow().isoformat(timespec='seconds')}")


# ---------------- email intake ----------------
TICKET_TAG = re.compile(r"\[#(\d{3,10})\]")


def ingest_email(db: Session, org_id, raw: bytes) -> dict:
    msg = email.message_from_bytes(raw, policy=email.policy.default)
    subject = str(msg.get("subject") or "(no subject)")[:300]
    sender = email.utils.parseaddr(str(msg.get("from") or ""))[1].lower()
    body_part = msg.get_body(preferencelist=("plain", "html"))
    body = body_part.get_content() if body_part else ""
    if body_part is not None and body_part.get_content_type() == "text/html":
        from bs4 import BeautifulSoup

        body = BeautifulSoup(body, "html.parser").get_text("\n")
    body = re.split(r"\n(?:On .+ wrote:|-----Original Message-----|From: )", body, maxsplit=1)[0].strip()[:50_000]
    from ..models import Contact, Membership, User

    user = db.execute(select(User).join(Membership, Membership.user_id == User.id).where(User.email == sender, Membership.org_id == org_id)).scalar_one_or_none()
    contact = db.execute(select(Contact).where(Contact.email == sender)).scalars().first()
    m = TICKET_TAG.search(subject)
    if m:
        t = db.execute(select(Ticket).where(Ticket.number == int(m.group(1)))).scalar_one_or_none()
        if t and ((user and t.requester_id == user.id) or (contact and t.customer_id == contact.customer_id)):
            c = ticket_svc.comment(db, t, body, author_id=user.id if user else None, author_kind="user" if user else "integration")
            return {"action": "comment", "ticket_id": str(t.id), "comment_id": str(c.id)}
    t = ticket_svc.create(db, org_id, subject=subject, description=f"{body}\n\n— via email from {sender}", source="email",
                          customer_id=contact.customer_id if contact else None, requester_id=user.id if user else None, actor_kind="integration")
    return {"action": "created", "ticket_id": str(t.id), "number": t.number, "known_sender": bool(user or contact)}


def ser(i: Integration, db: Session) -> dict:
    c = db.get(Credential, i.credential_id) if i.credential_id else None
    jobs = db.execute(select(SyncJob).where(SyncJob.integration_id == i.id).order_by(SyncJob.created_at.desc()).limit(20)).scalars()
    return {"id": str(i.id), "kind": i.kind, "name": i.name, "status": i.status, "config": i.config, "scopes": i.scopes,
            "credential": {"fingerprint": c.fingerprint, "rotated_at": c.rotated_at.isoformat() if c.rotated_at else None} if c and not c.revoked_at else None,
            "last_test_at": i.last_test_at.isoformat() if i.last_test_at else None, "last_test_result": i.last_test_result,
            "last_sync_at": i.last_sync_at.isoformat() if i.last_sync_at else None, "sync_cursor": i.sync_cursor,
            "history": [{"id": str(j.id), "direction": j.direction, "entity": j.entity, "status": j.status, "attempts": j.attempts,
                         "error": j.error, "conflict": j.conflict, "at": j.created_at.isoformat()} for j in jobs]}


def get(db: Session, iid) -> Integration:
    i = db.get(Integration, iid)
    if not i:
        raise NotFound("Integration not found")
    return i


_ = (timedelta, TicketComment)
