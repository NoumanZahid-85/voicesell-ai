"""
Embedding service — calls OpenAI's embeddings API (text-embedding-3-small,
truncated to 768 dims) instead of loading a local sentence-transformers model.

WHY: the local model path required torch + transformers + sentence-transformers
(~2-3GB of deps, 500MB+ RAM at inference) which OOM-kills on Render's free tier
(512MB). Calling a hosted embeddings API needs no local model weights and no
extra RAM beyond a small HTTP client, at the cost of a per-call API round trip
and per-token API cost. Output dimension is pinned to 768 via the `dimensions`
param so the vector size matches the existing Qdrant `products` collection
(VECTOR_DIMENSION=768) — no re-indexing schema change needed.

If you move the backend to a paid plan with enough RAM, you can swap this
back to the local sentence-transformers implementation for zero-marginal-cost
embeddings; keep the `embed`/`embed_one` interface the same and callers
(app/services/rag.py) won't need to change.
"""

from __future__ import annotations

import logging

from openai import AsyncOpenAI

from app.core.config import get_settings

logger = logging.getLogger(__name__)

EMBEDDING_MODEL = "text-embedding-3-small"
EMBEDDING_DIMENSIONS = 768  # must match qdrant_client.VECTOR_DIMENSION


class EmbeddingService:
    """Thin wrapper around the OpenAI embeddings endpoint."""

    def __init__(self) -> None:
        settings = get_settings()
        if not settings.openai_api_key:
            raise RuntimeError(
                "OPENAI_API_KEY is required for embeddings (used for "
                "OpenAI's text-embedding-3-small endpoint, independent of "
                "which LLM provider you use for chat)."
            )
        self._client = AsyncOpenAI(api_key=settings.openai_api_key)

    async def embed(self, texts: list[str]) -> list[list[float]]:
        """Embed a batch of texts → list of 768-dim normalized vectors."""
        if not texts:
            return []
        response = await self._client.embeddings.create(
            model=EMBEDDING_MODEL,
            input=texts,
            dimensions=EMBEDDING_DIMENSIONS,
        )
        return [item.embedding for item in response.data]

    async def embed_one(self, text: str) -> list[float]:
        """Embed a single text → one 768-dim normalized vector."""
        return (await self.embed([text]))[0]


_embedder: EmbeddingService | None = None


def get_embedder() -> EmbeddingService:
    """Return the shared embedding service singleton."""
    global _embedder
    if _embedder is None:
        _embedder = EmbeddingService()
    return _embedder


def warm_embedder() -> None:
    """No-op now — nothing to pre-load for an API-backed embedder.

    Kept so app/bootstrap.py doesn't need an import-site change.
    """
    logger.info("Embedding service uses OpenAI API — no local warm-up needed")
