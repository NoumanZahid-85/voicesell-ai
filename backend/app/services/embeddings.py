"""
Embedding service — calls Google's Gemini embeddings API (gemini-embedding-001)
instead of loading a local sentence-transformers model.

WHY hosted instead of local: the local model path required torch +
transformers + sentence-transformers (~2-3GB of deps, 500MB+ RAM at
inference) which OOM-kills on Render's free tier (512MB web service).
Calling a hosted embeddings API needs no local model weights and no extra
RAM beyond a small HTTP client, at the cost of a per-call API round trip.

WHY Gemini and not OpenAI: gemini-embedding-001 outputs 768 dimensions
natively — matches the existing Qdrant `products` collection
(VECTOR_DIMENSION=768) with no truncation/config needed. GEMINI_API_KEY is
already a configured secret for this project (see render.yaml).

WHY not Groq: Groq's API is inference-only (chat, Whisper transcription,
TTS) — it does not offer an embeddings endpoint as of this writing.

If you move the backend to a paid plan with enough RAM, you can swap this
back to a local sentence-transformers implementation for zero-marginal-cost
embeddings; keep the `embed`/`embed_one` interface the same and callers
(app/services/rag.py, scripts/embed_products.py) won't need to change.
"""

from __future__ import annotations

import logging

import httpx

from app.core.config import get_settings

logger = logging.getLogger(__name__)

EMBEDDING_MODEL = "gemini-embedding-001"
EMBEDDING_DIMENSIONS = 768  # truncated via output_dimensionality — must match qdrant_client.VECTOR_DIMENSION
_API_BASE = "https://generativelanguage.googleapis.com/v1beta"
_BATCH_ENDPOINT = f"{_API_BASE}/models/{EMBEDDING_MODEL}:batchEmbedContents"


class EmbeddingService:
    """Thin wrapper around the Gemini embeddings REST endpoint."""

    def __init__(self) -> None:
        settings = get_settings()
        if not settings.gemini_api_key:
            raise RuntimeError(
                "GEMINI_API_KEY is required for embeddings (used for the "
                "Gemini gemini-embedding-001 endpoint, independent of which "
                "LLM provider you use for chat)."
            )
        self._api_key = settings.gemini_api_key
        self._client = httpx.AsyncClient(timeout=30.0)

    async def embed(self, texts: list[str]) -> list[list[float]]:
        """Embed a batch of texts → list of 768-dim vectors."""
        if not texts:
            return []
        payload = {
            "requests": [
                {
                    "model": f"models/{EMBEDDING_MODEL}",
                    "content": {"parts": [{"text": text}]},
                    "output_dimensionality": EMBEDDING_DIMENSIONS,
                }
                for text in texts
            ]
        }
        response = await self._client.post(
            _BATCH_ENDPOINT,
            headers={"x-goog-api-key": self._api_key},
            json=payload,
        )
        response.raise_for_status()
        data = response.json()
        return [item["values"] for item in data["embeddings"]]

    async def embed_one(self, text: str) -> list[float]:
        """Embed a single text → one 768-dim vector."""
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
    logger.info("Embedding service uses Gemini API — no local warm-up needed")
