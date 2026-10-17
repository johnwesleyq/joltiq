"""Embedding providers.

- `openai:text-embedding-3-small` (dimensions=384) when an approved OpenAI key is configured for the tenant.
- `local-hash-v1`: deterministic feature-hashing embedder (word unigrams + bigrams + char trigrams, signed
  hashing, L2-normalised). It is NOT a neural semantic model; it gives robust lexical/fuzzy similarity
  with zero data egress and is labelled as such everywhere it is used.
"""
from __future__ import annotations

import hashlib
import math
import re

import httpx

from ..._dims import EMBED_DIM

_WORD = re.compile(r"[a-z0-9][a-z0-9_.+#-]*")
STOP = set("the a an and or of to in on for with is are was were be been it this that at by from as your you i we my our".split())


def _h(token: str) -> tuple[int, float]:
    d = hashlib.blake2b(token.encode(), digest_size=8).digest()
    v = int.from_bytes(d, "little")
    return v % EMBED_DIM, (1.0 if (v >> 63) & 1 else -1.0)


def local_embed(text: str) -> list[float]:
    vec = [0.0] * EMBED_DIM
    words = [w for w in _WORD.findall(text.lower()) if w not in STOP]
    feats: list[tuple[str, float]] = [(f"w:{w}", 1.0) for w in words]
    feats += [(f"b:{a}_{b}", 0.7) for a, b in zip(words, words[1:], strict=False)]
    for w in words:
        if len(w) > 4:
            for i in range(len(w) - 2):
                feats.append((f"c:{w[i:i + 3]}", 0.25))
    for tok, wt in feats:
        i, s = _h(tok)
        vec[i] += s * wt
    norm = math.sqrt(sum(x * x for x in vec)) or 1.0
    return [x / norm for x in vec]


class Embedder:
    name = "local-hash-v1"
    semantic = False

    def embed(self, texts: list[str]) -> list[list[float]]:
        return [local_embed(t) for t in texts]


class OpenAIEmbedder(Embedder):
    name = "openai:text-embedding-3-small@384"
    semantic = True

    def __init__(self, api_key: str, base_url: str = "https://api.openai.com/v1"):
        self.api_key = api_key
        self.base_url = base_url

    def embed(self, texts: list[str]) -> list[list[float]]:
        r = httpx.post(f"{self.base_url}/embeddings", headers={"authorization": f"Bearer {self.api_key}"},
                       json={"model": "text-embedding-3-small", "input": texts, "dimensions": EMBED_DIM}, timeout=60)
        r.raise_for_status()
        return [d["embedding"] for d in r.json()["data"]]


def embedder_for(db, org_id) -> Embedder:  # noqa: ANN001
    """Use the tenant's approved OpenAI provider if configured; otherwise the local embedder."""
    from ..ai.providers import provider_credentials

    key = provider_credentials(db, org_id, "openai", require_tenant_data_approval=True)
    if key:
        return OpenAIEmbedder(key)
    return Embedder()
