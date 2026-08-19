"""
Singleton async Qdrant client.

Lazy-initialized so the import doesn't fail if Qdrant is unreachable —
only actual usage does.
"""

from __future__ import annotations

from qdrant_client import AsyncQdrantClient

from app.core.config import get_settings

_client: AsyncQdrantClient | None = None

PRODUCTS_COLLECTION = "products"
VECTOR_DIMENSION = 768  # BAAI/bge-base-en-v1.5


def get_qdrant_client() -> AsyncQdrantClient:
    """Return the shared async Qdrant client (created on first call)."""
    global _client
    if _client is None:
        settings = get_settings()
        if settings.qdrant_api_key:
            _client = AsyncQdrantClient(
                url=settings.qdrant_url,
                api_key=settings.qdrant_api_key,
                timeout=10,
            )
        else:
            # Local Qdrant (Docker) — no API key needed
            _client = AsyncQdrantClient(
                url=settings.qdrant_url,
                timeout=10,
            )
    return _client
