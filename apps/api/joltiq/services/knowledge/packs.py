"""v14 built-in knowledge packs. ``msp-default`` = 7 generic DEMO/DEFAULT MSP procedures (Markdown) an admin can load
with one click (POST /knowledge/packs/msp-default). Loading is idempotent: documents already loaded from the pack are
skipped, deleted ones are re-added. They are ordinary SOP documents afterwards: edit, replace or delete them freely."""
from __future__ import annotations

import uuid
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session

from ...models import KnowledgeDocument
from . import service as kb

PACK_DIR = Path(__file__).resolve().parents[2] / "knowledge_packs"
PACKS = {"msp-default": ("msp_default", "JoltIQ default MSP pack (DEMO/DEFAULT)", 1)}


def files(pack: str) -> list[tuple[str, str, bytes]]:
    sub = PACKS[pack][0]
    out = []
    for p in sorted((PACK_DIR / sub).glob("*.md")):
        data = p.read_bytes()
        title = data.decode("utf-8").splitlines()[0].lstrip("# ").strip()
        out.append((p.name, title, data))
    return out


def describe(pack: str) -> dict:
    return {"id": pack, "label": PACKS[pack][1], "version": PACKS[pack][2], "documents": [t for _f, t, _d in files(pack)]}


def load(db: Session, org_id: uuid.UUID, pack: str, *, user_id) -> dict:
    existing = {d.filename for d in db.execute(select(KnowledgeDocument).where(
        KnowledgeDocument.org_id == org_id, KnowledgeDocument.status != "deleted")).scalars()
        if (d.meta or {}).get("pack") == pack}
    added, skipped = [], []
    for fname, title, data in files(pack):
        if fname in existing:
            skipped.append(title)
            continue
        d = kb.create_upload(db, org_id, title=title, filename=fname, data=data, uploaded_by=user_id, visibility=["customer", *kb.STAFF_VIS], source_type="sop",
                             meta={"pack": pack, "pack_version": PACKS[pack][2], "label": "DEMO/DEFAULT"})
        added.append({"id": str(d.id), "title": title})
    return {"pack": pack, "label": PACKS[pack][1], "added": added, "skipped": skipped}
