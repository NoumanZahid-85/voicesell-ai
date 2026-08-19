"""
Embedding service wrapping sentence-transformers (BAAI/bge-base-en-v1.5).

The model is loaded lazily on first use and kept as a process-wide singleton.
`sentence_transformers` (and its heavy torch dependency) is imported lazily so
that importing this module does not slow down tests or startup.

Why BAAI/bge-base-en-v1.5: 768-dim embeddings with strong retrieval quality on
English text — matches the Qdrant `products` collection dimension.
"""

from __future__ import annotations

import logging

from app.core.config import get_settings

logger = logging.getLogger(__name__)


class EmbeddingService:
    """Lazy singleton wrapper around a SentenceTransformer model."""

    def __init__(self, model_name: str | None = None) -> None:
        settings = get_settings()
        self.model_name = model_name or settings.embedding_model
        self._model = None

    @property
    def model(self):
        """The underlying SentenceTransformer, created on first access."""
        if self._model is None:
            # Lazy import — importing sentence_transformers pulls in torch (~45s).
            from sentence_transformers import SentenceTransformer

            logger.info(
                "Loading embedding model %s (first load may take a while)...",
                self.model_name,
            )
            self._model = SentenceTransformer(self.model_name)
            logger.info("Embedding model %s ready", self.model_name)
        return self._model

    def embed(self, texts: list[str]) -> list[list[float]]:
        """Embed a batch of texts → list of 768-dim normalized vectors."""
        if not texts:
            return []
        vectors = self.model.encode(
            texts,
            normalize_embeddings=True,
            show_progress_bar=False,
        )
        return vectors.tolist()

    def embed_one(self, text: str) -> list[float]:
        """Embed a single text → one 768-dim normalized vector."""
        return self.embed([text])[0]


_embedder: EmbeddingService | None = None


def get_embedder() -> EmbeddingService:
    """Return the shared embedding service singleton."""
    global _embedder
    if _embedder is None:
        _embedder = EmbeddingService()
    return _embedder


def warm_embedder() -> None:
    """Pre-load the embedding model at startup (avoids 30s first-request stall)."""
    try:
        _ = get_embedder().model
        logger.info("Embedding model warm-up complete")
    except Exception as exc:
        logger.warning("Embedding model warm-up failed (will retry lazily): %s", exc)
