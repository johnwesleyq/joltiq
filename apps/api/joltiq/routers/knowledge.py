from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, File, Form, UploadFile
from pydantic import BaseModel, Field, HttpUrl
from sqlalchemy import select

from ..config import get_settings
from ..core.errors import AppError, Conflict, Forbidden, NotFound
from ..deps import Ctx, perm
from ..models import ArticleReview, KnowledgeArticle, KnowledgeDocument, Organization
from ..services import audit
from ..services.knowledge import service as kb
from ..services.knowledge.embeddings import embedder_for
from .common import page_params, paginate

router = APIRouter(prefix="/knowledge", tags=["knowledge"])


def ser_doc(d: KnowledgeDocument) -> dict:
    m = d.meta or {}
    return {"id": str(d.id), "title": d.title, "source_type": d.source_type, "source_uri": d.source_uri, "filename": d.filename,
            "status": d.status, "error": d.error, "version": d.current_version, "visibility": d.visibility,
            "chunks": m.get("chunks"), "embedder": m.get("embedder"), "semantic": m.get("semantic"), "signals": m.get("signals"),
            "created_at": d.created_at.isoformat(), "updated_at": d.updated_at.isoformat()}


@router.get("/documents")
def list_docs(ctx: Ctx = Depends(perm("knowledge.write")), pp=Depends(page_params), status: str | None = None, q: str | None = None):
    stmt = select(KnowledgeDocument).where(KnowledgeDocument.status != "deleted").order_by(KnowledgeDocument.updated_at.desc())
    if status:
        stmt = stmt.where(KnowledgeDocument.status == status)
    if q:
        stmt = stmt.where(KnowledgeDocument.title.ilike(f"%{q}%"))
    return paginate(ctx.db, stmt, pp, ser_doc)


@router.post("/documents", status_code=201)
async def upload(file: UploadFile = File(...), title: str = Form(""), visibility: str = Form(""), source_type: str = Form("upload"),
                 ctx: Ctx = Depends(perm("knowledge.write"))):
    data = await file.read()
    vis = [v.strip() for v in visibility.split(",") if v.strip()] or None
    if source_type not in ("upload", "sop", "manual", "transcript", "article"):
        raise AppError("invalid source_type", code="validation_error")
    d = kb.create_upload(ctx.db, ctx.org_id, title=title, filename=file.filename or "document", data=data, uploaded_by=ctx.p.user_id,
                         visibility=vis, source_type=source_type)
    audit.record(ctx.db, org_id=ctx.org_id, actor_kind="user", actor_id=ctx.p.user_id, action="knowledge.uploaded", resource_type="knowledge_document", resource_id=d.id)
    return ser_doc(d)


@router.post("/import", status_code=201)
async def bulk_import(file: UploadFile = File(...), kind: str = Form("tickets"), include_unresolved: bool = Form(False), visibility: str = Form(""),
                      ctx: Ctx = Depends(perm("knowledge.write"))):
    """Onboard history: past tickets (CSV/JSON helpdesk export) or help articles (ZIP)."""
    from ..services.knowledge import importer

    data = await file.read()
    if len(data) > 50 * 1024 * 1024:
        raise AppError("File is larger than 50 MB; split it into parts", code="upload_rejected")
    name = file.filename or "import"
    vis = [v.strip() for v in visibility.split(",") if v.strip()] or None
    if kind == "tickets":
        r = importer.import_tickets(ctx.db, ctx.org_id, filename=name, data=data, user_id=ctx.p.user_id, include_unresolved=include_unresolved,
                                    visibility=vis)
    elif kind == "articles":
        r = importer.import_articles_zip(ctx.db, ctx.org_id, filename=name, data=data, user_id=ctx.p.user_id, visibility=vis)
    else:
        raise AppError("kind must be tickets or articles", code="validation_error")
    return r.as_dict()


class UrlIn(BaseModel):
    url: HttpUrl
    title: str | None = Field(default=None, max_length=300)
    visibility: list[str] | None = None


@router.post("/documents/url", status_code=201)
def add_url(body: UrlIn, ctx: Ctx = Depends(perm("knowledge.write"))):
    org = ctx.db.get(Organization, ctx.org_id)
    allowed = list(((org.settings or {}) if org else {}).get("ingest_allowed_domains") or get_settings().allowed_research_domains)
    d = kb.create_url(ctx.db, ctx.org_id, url=str(body.url), title=body.title, uploaded_by=ctx.p.user_id, visibility=body.visibility,
                      allowed_domains=allowed)
    return ser_doc(d)


@router.get("/documents/{doc_id}")
def get_doc(doc_id: uuid.UUID, ctx: Ctx = Depends(perm("knowledge.write"))):
    from ..models import KnowledgeChunk

    d = kb.get_doc(ctx.db, doc_id)
    chunks = ctx.db.execute(select(KnowledgeChunk).where(KnowledgeChunk.document_id == d.id).order_by(KnowledgeChunk.ordinal).limit(200)).scalars()
    return {**ser_doc(d), "chunks_preview": [{"ordinal": c.ordinal, "page": c.page, "section": c.section, "text": c.text[:400]} for c in chunks]}


@router.post("/documents/{doc_id}/versions", status_code=201)
async def new_version(doc_id: uuid.UUID, file: UploadFile = File(...), ctx: Ctx = Depends(perm("knowledge.write"))):
    d = kb.get_doc(ctx.db, doc_id)
    if d.source_type in ("url", "ticket"):
        raise Conflict("Use reindex for URL/ticket sources")
    kb.add_version(ctx.db, d, await file.read())
    return ser_doc(d)


@router.post("/documents/{doc_id}/reindex")
def reindex(doc_id: uuid.UUID, ctx: Ctx = Depends(perm("knowledge.write"))):
    d = kb.get_doc(ctx.db, doc_id)
    kb.reindex(ctx.db, d)
    return ser_doc(d)


class VisIn(BaseModel):
    visibility: list[str]


@router.patch("/documents/{doc_id}")
def set_visibility(doc_id: uuid.UUID, body: VisIn, ctx: Ctx = Depends(perm("knowledge.write"))):
    d = kb.get_doc(ctx.db, doc_id)
    d.visibility = kb._vis(body.visibility)
    return ser_doc(d)


@router.delete("/documents/{doc_id}")
def delete_doc(doc_id: uuid.UUID, ctx: Ctx = Depends(perm("knowledge.write"))):
    kb.delete_document(ctx.db, kb.get_doc(ctx.db, doc_id), ctx.p.user_id)
    return {"ok": True}


class SearchIn(BaseModel):
    query: str = Field(min_length=1, max_length=2000)
    limit: int = Field(default=8, ge=1, le=25)


@router.post("/search")
def search(body: SearchIn, ctx: Ctx = Depends(perm("knowledge.read"))):
    hits = kb.search(ctx.db, ctx.org_id, body.query, role=ctx.p.role if not ctx.p.is_platform else "platform_owner", limit=body.limit)
    emb = embedder_for(ctx.db, ctx.org_id)
    return {"results": hits, "retrieval": {"keyword": "postgres-fts", "vector": emb.name, "semantic_model": emb.semantic, "fusion": "rrf"}}


# ---------------- Articles ----------------
def ser_art(a: KnowledgeArticle) -> dict:
    return {"id": str(a.id), "title": a.title, "status": a.status, "version": a.version, "body": a.body, "citations": a.citations,
            "source_ticket_id": str(a.source_ticket_id) if a.source_ticket_id else None, "verified_resolution": a.verified_resolution,
            "executable": a.executable, "drafted_by_ai": a.drafted_by_ai, "procedure_hash": a.procedure_hash,
            "created_at": a.created_at.isoformat(),
            "updated_at": a.updated_at.isoformat(), "previous_version_id": str(a.previous_version_id) if a.previous_version_id else None}


@router.get("/articles")
def list_articles(ctx: Ctx = Depends(perm("knowledge.review")), pp=Depends(page_params), status: str | None = None):
    stmt = select(KnowledgeArticle).order_by(KnowledgeArticle.updated_at.desc())
    if status:
        stmt = stmt.where(KnowledgeArticle.status == status)
    return paginate(ctx.db, stmt, pp, ser_art)


class ArticleIn(BaseModel):
    title: str = Field(min_length=3, max_length=300)
    body: dict


@router.post("/articles", status_code=201)
def create_article(body: ArticleIn, ctx: Ctx = Depends(perm("knowledge.write"))):
    a = KnowledgeArticle(org_id=ctx.org_id, title=body.title, body=body.body, status="draft", drafted_by_ai=False, executable=False)
    ctx.db.add(a)
    ctx.db.flush()
    return ser_art(a)


@router.get("/articles/{aid}")
def get_article(aid: uuid.UUID, ctx: Ctx = Depends(perm("knowledge.review"))):
    a = ctx.db.get(KnowledgeArticle, aid)
    if not a:
        raise NotFound("Article not found")
    reviews = ctx.db.execute(select(ArticleReview).where(ArticleReview.article_id == aid).order_by(ArticleReview.created_at)).scalars()
    return {**ser_art(a), "reviews": [{"decision": r.decision, "comment": r.comment, "reviewer_id": str(r.reviewer_id),
                                        "procedure_hash": r.procedure_hash,
                                        "auth_session_id": str(r.auth_session_id) if r.auth_session_id else None,
                                        "mfa_passed": r.mfa_passed, "at": r.created_at.isoformat()} for r in reviews]}


@router.patch("/articles/{aid}")
def edit_article(aid: uuid.UUID, body: ArticleIn, ctx: Ctx = Depends(perm("knowledge.write"))):
    a = ctx.db.get(KnowledgeArticle, aid)
    if not a:
        raise NotFound("Article not found")
    if a.status in ("published", "archived"):
        # Editing a published article creates a new draft version; the published one stays intact.
        new = KnowledgeArticle(org_id=a.org_id, title=body.title, body=body.body, status="draft", version=a.version + 1, citations=a.citations,
                               source_ticket_id=a.source_ticket_id, verified_resolution=a.verified_resolution, executable=False,
                               drafted_by_ai=False, previous_version_id=a.id)
        ctx.db.add(new)
        ctx.db.flush()
        return ser_art(new)
    a.title, a.body = body.title, body.body
    a.status = "draft"
    return ser_art(a)


class ReviewIn(BaseModel):
    decision: str = Field(pattern="^(submit|approve|reject|request_changes|publish|archive)$")
    comment: str = Field(default="", max_length=5000)
    executable: bool = False  # only meaningful on publish


@router.post("/articles/{aid}/review")
def review(aid: uuid.UUID, body: ReviewIn, ctx: Ctx = Depends(perm("knowledge.review"))):
    a = ctx.db.get(KnowledgeArticle, aid)
    if not a:
        raise NotFound("Article not found")
    d = body.decision
    flow = {"submit": ({"draft", "rejected"}, "in_review"), "approve": ({"in_review", "draft"}, "approved"),
            "reject": ({"in_review", "draft", "approved"}, "rejected"), "request_changes": ({"in_review", "approved"}, "draft"),
            "publish": ({"approved"}, "published"), "archive": ({"published", "approved", "rejected", "draft"}, "archived")}
    allowed_from, to = flow[d]
    if a.status not in allowed_from:
        raise Conflict(f"Cannot {d} an article that is {a.status}", code="invalid_transition")
    review_decision = d
    review_hash = None
    if d == "approve" and body.executable:
        ctx.p.require("knowledge.publish")
        if not ctx.p.user.mfa_enabled or not ctx.p.session.mfa_passed:
            raise Forbidden("Executable procedure approval requires MFA", code="mfa_required")
        from ..services.procedures import publication_hash

        try:
            review_hash = publication_hash(a.body or {})
        except ValueError as exc:
            raise AppError(f"Procedure is not executable: {exc}", code="validation_error") from exc
        a.procedure_hash = review_hash
        review_decision = "approve_executable"
    if d == "publish":
        ctx.p.require("knowledge.publish")
        if body.executable:
            if not ctx.p.user.mfa_enabled or not ctx.p.session.mfa_passed:
                raise Forbidden("Executable procedure publication requires MFA", code="mfa_required")
            # Executable procedures require: verified resolution evidence, not derived solely from external research,
            # and an explicit admin decision. This is the ONLY way a procedure becomes runnable by the AI.
            if a.drafted_by_ai and not a.verified_resolution:
                raise Conflict("AI-drafted articles can only be made executable when they come from a VERIFIED resolution",
                               code="not_verified")
            if (a.body or {}).get("derived_from_external_research"):
                raise Conflict("Procedures derived only from external research cannot be made executable automatically; "
                               "re-author it as a technician-written procedure", code="external_fix")
            from ..services.procedures import publication_hash

            try:
                review_hash = publication_hash(a.body or {})
            except ValueError as exc:
                raise AppError(f"Procedure is not executable: {exc}", code="validation_error") from exc
            if not a.procedure_hash or a.procedure_hash != review_hash:
                raise Conflict("Executable procedure changed after approval", code="procedure_changed")
            prior = ctx.db.execute(select(ArticleReview).where(
                ArticleReview.article_id == a.id,
                ArticleReview.decision == "approve_executable",
                ArticleReview.procedure_hash == review_hash,
                ArticleReview.mfa_passed.is_(True),
            ).order_by(ArticleReview.created_at.desc())).scalars().first()
            if not prior:
                raise Conflict("Executable procedure needs an MFA-backed admin approval", code="approval_required")
            if prior.reviewer_id == ctx.p.user_id:
                raise Conflict("A second administrator must publish this executable procedure", code="second_admin_required")
        a.executable = bool(body.executable)
        _publish_as_document(ctx, a)
        if a.previous_version_id:
            prev = ctx.db.get(KnowledgeArticle, a.previous_version_id)
            if prev and prev.status == "published":
                prev.status = "archived"
    if d == "archive" and a.published_document_id:
        doc = ctx.db.get(KnowledgeDocument, a.published_document_id)
        if doc:
            kb.delete_document(ctx.db, doc, ctx.p.user_id)
        a.executable = False
    a.status = to
    ctx.db.add(ArticleReview(
        org_id=ctx.org_id, article_id=a.id, reviewer_id=ctx.p.user_id, decision=review_decision, comment=body.comment,
        procedure_hash=review_hash, auth_session_id=ctx.p.session.id if review_hash else None,
        mfa_passed=bool(review_hash and ctx.p.user.mfa_enabled and ctx.p.session.mfa_passed),
    ))
    audit.record(ctx.db, org_id=ctx.org_id, actor_kind="user", actor_id=ctx.p.user_id, action=f"article.{d}", resource_type="knowledge_article",
                 resource_id=a.id, data={"executable": a.executable})
    return ser_art(a)


def _publish_as_document(ctx: Ctx, a: KnowledgeArticle) -> None:
    b = a.body or {}

    def sec(name, items):
        if not items:
            return ""
        if isinstance(items, str):
            return f"## {name}\n{items}\n\n"
        return f"## {name}\n" + "\n".join(f"- {x}" for x in items) + "\n\n"

    md = (f"# {a.title}\n\n" + sec("Symptoms", b.get("symptoms")) + sec("Environment", b.get("environment")) +
          sec("Prerequisites", b.get("prerequisites")) + sec("Diagnostics", b.get("diagnostics")) + sec("Remediation", b.get("remediation")) +
          sec("Risk", b.get("risk")) + sec("Verification", b.get("verification")))
    vis = b.get("visibility") or ["technician", "admin", "org_owner", "auditor", "customer"]
    d = kb.create_upload(ctx.db, ctx.org_id, title=a.title, filename=f"article-{a.id}.md", data=md.encode(), uploaded_by=ctx.p.user_id,
                         visibility=vis, source_type="article", meta={"article_id": str(a.id)})
    a.published_document_id = d.id
