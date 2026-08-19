"""
Startup / shutdown bootstrap — the single owner of app lifecycle wiring.

Everything the process needs before serving traffic lives here so the
entrypoint (main.py) stays a thin assembly list and the embed script can
reuse the same collection contract.
"""

from __future__ import annotations

import logging

from qdrant_client.models import Distance, VectorParams

from app.db.models import Base
from app.db.session import get_engine
from app.services.embeddings import warm_embedder
from app.services.qdrant_client import (
    PRODUCTS_COLLECTION,
    VECTOR_DIMENSION,
    get_qdrant_client,
)
from app.voice import daily_client, session_registry

logger = logging.getLogger(__name__)


async def ensure_schema() -> None:
    """Create DB tables if missing (dev convenience — Alembic owns prod)."""
    engine = get_engine()
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    logger.info("Connected to PostgreSQL — tables synced")


async def ensure_products_collection() -> None:
    """Create the Qdrant products collection + payload indexes if missing.

    Idempotent and safe to call from the embed script as well as startup —
    one contract for the vector store.
    """
    qdrant = get_qdrant_client()
    collections = await qdrant.get_collections()
    existing_names = [c.name for c in collections.collections]

    if PRODUCTS_COLLECTION not in existing_names:
        await qdrant.create_collection(
            collection_name=PRODUCTS_COLLECTION,
            vectors_config=VectorParams(
                size=VECTOR_DIMENSION,
                distance=Distance.COSINE,
            ),
        )
        logger.info(
            "Created Qdrant collection '%s' (dims=%d, cosine)",
            PRODUCTS_COLLECTION,
            VECTOR_DIMENSION,
        )
    else:
        logger.info("Qdrant collection '%s' already exists", PRODUCTS_COLLECTION)

    for field_name, schema in (("category", "keyword"), ("price", "float")):
        try:
            await qdrant.create_payload_index(
                collection_name=PRODUCTS_COLLECTION,
                field_name=field_name,
                field_schema=schema,
            )
        except Exception as exc:
            logger.info("Payload index on '%s' already present (%s)", field_name, exc)


async def ensure_vector_store() -> None:
    """Ensure Qdrant is ready, degrading to a lazy retry when unreachable."""
    try:
        await ensure_products_collection()
    except Exception as exc:
        logger.warning("Qdrant setup skipped (will retry on first use): %s", exc)


async def warm_embeddings() -> None:
    """Pre-load the embedding model without blocking the event loop.

    SentenceTransformer load is CPU/disk heavy and synchronous; running it
    in the default executor keeps startup from freezing the loop.
    """
    import asyncio

    await asyncio.to_thread(warm_embedder)


async def shutdown() -> None:
    """Ordered teardown: voice sessions → Daily client pool → DB engine."""
    await session_registry.shutdown_all()
    await daily_client.close_client()
    await get_engine().dispose()
    logger.info("Shutdown complete")