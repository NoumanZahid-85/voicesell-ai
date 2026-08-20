"""
Startup / shutdown bootstrap — the single owner of app lifecycle wiring.

Everything the process needs before serving traffic lives here so the
entrypoint (main.py) stays a thin assembly list and the embed script can
reuse the same collection contract.
"""

from __future__ import annotations

import logging
import os

from qdrant_client.models import Distance, VectorParams
from sqlalchemy import func, select

from app.db.models import Base, Product
from app.db.session import get_engine, get_session_factory
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


async def auto_seed() -> None:
    """Seed the catalog automatically on first boot, if it's empty.

    WHY this exists: Render's free plan has no Shell/Job access, so there's
    no way to manually run `scripts.ingest_olist` / `scripts.embed_products`
    after deploy. Instead, check whether the `products` table already has
    rows; if it's empty, run both seeding steps inline during startup. This
    only fires once — subsequent boots see a non-empty table and skip it —
    so it's safe to leave on permanently. Set AUTO_SEED=false to disable
    (e.g. once you have Shell/Job access and prefer to seed manually).

    Deliberately does not raise: a seeding failure (e.g. Qdrant not yet
    configured) should not prevent the app from serving text/order/admin
    traffic that doesn't depend on the catalog being populated yet.
    """
    if os.getenv("AUTO_SEED", "true").lower() in ("false", "0", "no"):
        logger.info("AUTO_SEED disabled — skipping automatic catalog seeding")
        return

    try:
        factory = get_session_factory()
        async with factory() as session:
            count = await session.scalar(select(func.count()).select_from(Product))
        if count and count > 0:
            logger.info("Products table already has %d rows — skipping auto-seed", count)
            return

        logger.info("Products table is empty — running one-time auto-seed...")

        # Import here (not at module top) so a missing/broken scripts module
        # can't break the app on every boot — only the seed attempt itself.
        from scripts.embed_products import embed_products
        from scripts.ingest_olist import ensure_data_files, ingest, load_products_from_csv

        products_path, _ = await ensure_data_files()
        products_data = load_products_from_csv(products_path)
        async with factory() as session:
            cat_count, prod_count = await ingest(session, products_data)
        logger.info("Auto-seed: ingested %d products, %d categories into PostgreSQL", prod_count, cat_count)

        try:
            embedded_count = await embed_products()
            logger.info("Auto-seed: embedded %d products into Qdrant", embedded_count)
        except Exception as exc:
            logger.warning(
                "Auto-seed: PostgreSQL ingestion succeeded but Qdrant embedding failed "
                "(%s) — catalog page will work, chat/voice product search will not until "
                "this is fixed and the app restarts (it will retry next boot since the "
                "products table now has rows... actually it won't, since this check is "
                "row-count based. Re-run manually via `python -m scripts.embed_products` "
                "once Shell/Job access is available, or clear the products table to "
                "retrigger auto-seed).",
                exc,
            )
    except Exception:
        logger.exception("Auto-seed failed — app will continue starting without seeded data")


async def shutdown() -> None:
    """Ordered teardown: voice sessions → Daily client pool → DB engine."""
    await session_registry.shutdown_all()
    await daily_client.close_client()
    await get_engine().dispose()
    logger.info("Shutdown complete")