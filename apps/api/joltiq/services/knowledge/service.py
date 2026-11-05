"""Knowledge ingestion (async via job queue), versioning, indexing, hybrid retrieval with permission filtering."""
from __future__ import annotations

import hashlib
import uuid
from collections.abc import Sequence
from typing import Any

from sqlalchemy import delete, func, literal_column, select
from sqlalchemy.orm import Session

from ...core.errors import AppError, NotFound
from ...models import KnowledgeChunk, KnowledgeDocument, KnowledgeDocumentVersion, Ticket, TicketComment, utcnow
from .. import audit, storage
from ..jobs import enqueue, handler
from .chunking import chunk_sections
from .embeddings import embedder_for
from .extraction import extract
from .parsing import ParseError, Section, html_sections, parse

STAFF_VIS = ["technician", "admin", "org_owner", "auditor"]


def create_upload(db: Session, org_id: uuid.UUID, *, title: str, filename: str, data: bytes, uploaded_by, visibility: list[str] | None,
                  source_type: str = "upload", meta: dict | None = None) -> KnowledgeDocument:
    try:
        ctype = storage.sniff(filename, data)
    except storage.UploadRejected as e:
        raise AppError(str(e), code="upload_rejected") from e
    if ctype.startswith("image/"):
        raise AppError("Images are not indexed as knowledge (OCR not enabled)", code="upload_rejected")
    doc = KnowledgeDocument(org_id=org_id, title=title or filename, source_type=source_type, filename=filename, content_type=ctype,
                            status="pending", visibility=_vis(visibility), uploaded_by=uploaded_by, meta=meta or {})
    db.add(doc)
    db.flush()
    add_version(db, doc, data)
    return doc


def _vis(v: list[str] | None) -> list[str]:
    allowed = {"customer", *STAFF_VIS}
    v = [x for x in (v or STAFF_VIS) if x in allowed]
    return sorted(set(v) | {"admin", "org_owner"})


def add_version(db: Session, doc: KnowledgeDocument, data: bytes) -> KnowledgeDocumentVersion:
    from ..billing import check_entitlement, meter

    check_entitlement(db, doc.org_id, "storage_bytes", adding=len(data))
    key, sha = storage.put(doc.org_id, "knowledge", doc.filename or "doc", data)
    doc.current_version += 1
    v = KnowledgeDocumentVersion(org_id=doc.org_id, document_id=doc.id, version=doc.current_version, storage_key=key, sha256=sha, size_bytes=len(data))
    db.add(v)
    doc.status, doc.error = "pending", None
    db.flush()
    meter(db, doc.org_id, "storage_bytes", len(data), f"kdoc:{v.id}")
    enqueue(db, "knowledge.index", {"document_id": str(doc.id), "version": v.version}, org_id=doc.org_id,
            dedupe_key=f"kindex:{doc.id}:{v.version}")
    return v


def create_url(db: Session, org_id: uuid.UUID, *, url: str, title: str | None, uploaded_by, visibility: list[str] | None,
               allowed_domains: list[str]) -> KnowledgeDocument:
    from ...security.ssrf import SSRFBlocked, validate_url

    try:
        validate_url(url, allowed_domains=allowed_domains or None)
    except SSRFBlocked as e:
        audit.security_event(db, "ssrf_blocked", org_id=org_id, user_id=uploaded_by, severity="warn", data={"url": url[:300], "reason": str(e)})
        raise AppError(f"URL not allowed: {e}", code="url_blocked") from e
    doc = KnowledgeDocument(org_id=org_id, title=title or url, source_type="url", source_uri=url, content_type="text/html",
                            status="pending", visibility=_vis(visibility), uploaded_by=uploaded_by, meta={"allowed_domains": allowed_domains})
    db.add(doc)
    db.flush()
    doc.current_version = 1
    enqueue(db, "knowledge.index", {"document_id": str(doc.id), "version": 1}, org_id=org_id, dedupe_key=f"kindex:{doc.id}:1")
    return doc


def index_document(db: Session, doc: KnowledgeDocument, version: int) -> int:
    from ...security.ssrf import SSRFBlocked, safe_fetch

    doc.status = "processing"
    db.flush()
    try:
        if doc.source_type == "url":
            try:
                res = safe_fetch(doc.source_uri or "", allowed_domains=(doc.meta or {}).get("allowed_domains") or None)
            except SSRFBlocked as e:
                raise ParseError(f"URL blocked: {e}") from e
            if res.status >= 400:
                raise ParseError(f"URL returned HTTP {res.status}")
            if "html" not in res.content_type and "text" not in res.content_type:
                raise ParseError(f"unsupported content-type {res.content_type}")
            sections = html_sections(res.body.decode("utf-8", errors="replace"))
            sha = hashlib.sha256(res.body).hexdigest()
            v = db.execute(select(KnowledgeDocumentVersion).where(KnowledgeDocumentVersion.document_id == doc.id,
                                                                  KnowledgeDocumentVersion.version == version)).scalar_one_or_none()
            if v is None:
                v = KnowledgeDocumentVersion(org_id=doc.org_id, document_id=doc.id, version=version, sha256=sha, size_bytes=len(res.body))
                db.add(v)
        elif doc.source_type in ("ticket", "imported_ticket", "learned_fix"):
            sections = [Section((doc.meta or {}).get("text", ""), heading=doc.title)]
            v = None
        else:
            v = db.execute(select(KnowledgeDocumentVersion).where(KnowledgeDocumentVersion.document_id == doc.id,
                                                                  KnowledgeDocumentVersion.version == version)).scalar_one()
            data = storage.get(doc.org_id, v.storage_key or "")
            sections = parse(data, doc.content_type or "text/plain", doc.filename or "")
        chunks = chunk_sections(sections)
        if not chunks:
            raise ParseError("no text content found")
        emb = embedder_for(db, doc.org_id)
        vectors = emb.embed([f"{c.section or ''}\n{c.text}" for c in chunks])
        db.execute(delete(KnowledgeChunk).where(KnowledgeChunk.document_id == doc.id))
        for c, vec in zip(chunks, vectors, strict=True):
            db.add(KnowledgeChunk(org_id=doc.org_id, document_id=doc.id, version=version, ordinal=c.ordinal, text=c.text,
                                  page=c.page, section=c.section, embedding=vec, embedder=emb.name))
        signals = extract("\n".join(s.text for s in sections)[:200_000])
        if v is not None:
            v.extracted = signals
        doc.meta = {**(doc.meta or {}), "signals": signals, "chunks": len(chunks), "embedder": emb.name, "semantic": emb.semantic}
        doc.status, doc.error = "indexed", None
        db.flush()
        return len(chunks)
    except ParseError as e:
        doc.status, doc.error = "failed", str(e)
        return 0


@handler("knowledge.index")
def _index_job(db: Session, payload: dict) -> None:
    doc = db.get(KnowledgeDocument, uuid.UUID(payload["document_id"]))
    if not doc or doc.status == "deleted":
        return
    index_document(db, doc, int(payload["version"]))


def reindex(db: Session, doc: KnowledgeDocument) -> None:
    enqueue(db, "knowledge.index", {"document_id": str(doc.id), "version": doc.current_version}, org_id=doc.org_id)
    doc.status = "pending"


def delete_document(db: Session, doc: KnowledgeDocument, actor_id) -> None:
    db.execute(delete(KnowledgeChunk).where(KnowledgeChunk.document_id == doc.id))
    for v in db.execute(select(KnowledgeDocumentVersion).where(KnowledgeDocumentVersion.document_id == doc.id)).scalars():
        if v.storage_key:
            storage.delete(doc.org_id, v.storage_key)
            v.storage_key = None
    doc.status = "deleted"
    audit.record(db, org_id=doc.org_id, actor_kind="user", actor_id=actor_id, action="knowledge.deleted", resource_type="knowledge_document", resource_id=doc.id)


def index_ticket(db: Session, t: Ticket) -> KnowledgeDocument:
    """Resolved tickets become retrievable history — labelled with whether the outcome was VERIFIED."""
    comments = db.execute(select(TicketComment).where(TicketComment.ticket_id == t.id, TicketComment.internal.is_(False))
                          .order_by(TicketComment.created_at)).scalars().all()
    text = f"{t.subject}\n\n{t.description}\n\n" + "\n".join(c.body for c in comments)
    existing = db.execute(select(KnowledgeDocument).where(KnowledgeDocument.source_type == "ticket",
                                                          KnowledgeDocument.source_uri == f"ticket:{t.id}")).scalar_one_or_none()
    outcome = "verified" if t.resolution_verified else "unverified"
    if existing is None:
        existing = KnowledgeDocument(org_id=t.org_id, title=f"Ticket #{t.number}: {t.subject}", source_type="ticket", source_uri=f"ticket:{t.id}",
                                     content_type="text/plain", visibility=_vis(None), status="pending")
        db.add(existing)
        db.flush()
    existing.current_version += 1
    existing.meta = {"text": text[:100_000], "ticket_id": str(t.id), "outcome": outcome}
    index_document(db, existing, existing.current_version)
    return existing


# ---------------- Retrieval ----------------
def search(db: Session, org_id: uuid.UUID, query: str, *, role: str | None, limit: int = 8,
           document_ids: list[uuid.UUID] | None = None) -> list[dict[str, Any]]:
    """Hybrid retrieval: Postgres full-text (BM25-like ts_rank_cd) + vector cosine, fused with Reciprocal Rank Fusion.
    Permission filter: the caller's role must be in the document's visibility list."""
    if not query.strip():
        return []
    vis_role = role if role else "customer"
    base = (select(KnowledgeChunk, KnowledgeDocument)
            .join(KnowledgeDocument, KnowledgeDocument.id == KnowledgeChunk.document_id)
            .where(KnowledgeDocument.status == "indexed"))
    if vis_role not in ("platform_owner",):
        base = base.where(KnowledgeDocument.visibility.any(vis_role))  # type: ignore[arg-type]  # SQLAlchemy ARRAY.any(value) → value = ANY(col)
    if document_ids:
        base = base.where(KnowledgeDocument.id.in_(document_ids))
    # Keyword: OR semantics over query lexemes so partial matches still rank.
    tsq = func.to_tsquery("english", func.replace(func.cast(func.plainto_tsquery("english", query), _Text()), " & ", " | "))
    kw_rank = func.ts_rank_cd(KnowledgeChunk.tsv, tsq)
    kw = db.execute(base.add_columns(kw_rank.label("s")).where(KnowledgeChunk.tsv.op("@@")(tsq)).order_by(literal_column("s").desc()).limit(40)).all()
    emb = embedder_for(db, org_id)
    try:
        qv = emb.embed([query])[0]
    except Exception:  # noqa: BLE001 - v14: embedding provider down/unauthorised -> keyword-only ranking
        qv = None
    vec: Sequence[Any] = []
    if qv is not None:
        dist = KnowledgeChunk.embedding.cosine_distance(qv)
        vec = db.execute(base.add_columns(dist.label("d")).where(KnowledgeChunk.embedder == emb.name)
                         .order_by(dist).limit(40)).all()
    scores: dict[uuid.UUID, float] = {}
    rows: dict[uuid.UUID, tuple] = {}
    detail: dict[uuid.UUID, dict] = {}
    K = 60
    for rank, (ch, doc, _s) in enumerate(kw):
        scores[ch.id] = scores.get(ch.id, 0) + 1 / (K + rank + 1)
        rows[ch.id] = (ch, doc)
        detail.setdefault(ch.id, {})["keyword_rank"] = rank + 1
    for rank, (ch, doc, d) in enumerate(vec):
        sim = 1 - float(d)
        if sim < 0.08:  # ignore noise
            continue
        scores[ch.id] = scores.get(ch.id, 0) + 1 / (K + rank + 1)
        rows[ch.id] = (ch, doc)
        detail.setdefault(ch.id, {})["vector_similarity"] = round(sim, 4)
    ranked = sorted(scores.items(), key=lambda kv: kv[1], reverse=True)[:limit]
    out = []
    for cid, sc in ranked:
        ch, doc = rows[cid]
        out.append({
            "chunk_id": str(ch.id), "document_id": str(doc.id), "title": doc.title, "source_type": doc.source_type,
            "source_uri": doc.source_uri, "page": ch.page, "section": ch.section, "version": ch.version,
            "text": ch.text, "score": round(sc, 5), "signals": detail[cid],
            "outcome": (doc.meta or {}).get("outcome"), "embedder": ch.embedder,
            "citation": _cite(doc, ch),
        })
    return out


def _cite(doc: KnowledgeDocument, ch: KnowledgeChunk) -> str:
    parts = [doc.title]
    if ch.page:
        parts.append(f"p. {ch.page}")
    if ch.section:
        parts.append(f"§ {ch.section}")
    parts.append(f"v{ch.version}")
    return " · ".join(parts)


def _Text():  # noqa: N802
    from sqlalchemy import Text

    return Text()


def get_doc(db: Session, doc_id: uuid.UUID) -> KnowledgeDocument:
    d = db.get(KnowledgeDocument, doc_id)
    if not d or d.status == "deleted":
        raise NotFound("Document not found")
    return d


_ = utcnow
