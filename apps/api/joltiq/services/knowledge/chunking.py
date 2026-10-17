from __future__ import annotations

import re
from dataclasses import dataclass

from .parsing import Section


@dataclass
class Chunk:
    text: str
    page: int | None
    section: str | None
    ordinal: int


def chunk_sections(sections: list[Section], target_chars: int = 1200, overlap_chars: int = 150) -> list[Chunk]:
    """Paragraph-aware chunking that never crosses a page/section boundary, so citations stay exact."""
    out: list[Chunk] = []
    n = 0
    for s in sections:
        paras = [p.strip() for p in re.split(r"\n\s*\n|\n(?=[-*•\d]+[.)]?\s)", s.text) if p.strip()]
        buf = ""
        for p in paras:
            if len(p) > target_chars:
                # Split very long paragraphs on sentence boundaries.
                sents = re.split(r"(?<=[.!?])\s+", p)
            else:
                sents = [p]
            for piece in sents:
                if buf and len(buf) + len(piece) + 1 > target_chars:
                    out.append(Chunk(buf.strip(), s.page, s.heading, n))
                    n += 1
                    buf = buf[-overlap_chars:] + " " + piece if overlap_chars else piece
                else:
                    buf = f"{buf}\n{piece}" if buf else piece
        if buf.strip():
            out.append(Chunk(buf.strip(), s.page, s.heading, n))
            n += 1
    return out
