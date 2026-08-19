"""
Embedding pipeline: products (PostgreSQL) → chunks → Qdrant vectors.

Loads products, chunks long descriptions (50-char overlap), embeds with
BAAI/bge-base-en-v1.5, and upserts into Qdrant with metadata
(product_id, name, category, price, stock_quantity).

Idempotent: point IDs are deterministic (uuid5 of product_id + chunk index),
so re-running upserts in place and removes points for deleted products.

Usage:
    cd backend
    uv run python -m scripts.embed_products
"""

from __future__ import annotations

import asyncio
import logging
import sys
import uuid
from pathlib import Path

from sqlalchemy import select

# Add backend to path so we can import app modules
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.bootstrap import ensure_products_collection  # noqa: E402
from app.db.models import Product, ProductCategory  # noqa: E402
from app.db.session import get_engine, get_session_factory  # noqa: E402
from app.services.embeddings import get_embedder  # noqa: E402
from app.services.qdrant_client import (  # noqa: E402
    PRODUCTS_COLLECTION,
    VECTOR_DIMENSION,
    get_qdrant_client,
)

logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(levelname)-7s | %(message)s")
logger = logging.getLogger(__name__)

CHUNK_SIZE = 300
CHUNK_OVERLAP = 50
BATCH_SIZE = 64


def chunk_text(text: str, chunk_size: int = CHUNK_SIZE, overlap: int = CHUNK_OVERLAP) -> list[str]:
    """Split text into overlapping chunks (keeps short texts whole)."""
    text = text.strip()
    if not text:
        return []
    if len(text) <= chunk_size:
        return [text]
    chunks: list[str] = []
    start = 0
    while start < len(text):
        end = min(start + chunk_size, len(text))
        chunks.append(text[start:end])
        if end >= len(text):
            break
        start = max(0, end - overlap)
    return chunks


def point_id(product_id: str, index: int) -> uuid.UUID:
    """Deterministic Qdrant point ID for a (product, chunk index) pair."""
    return uuid.uuid5(uuid.NAMESPACE_URL, f"product:{product_id}:chunk:{index}")


async def load_products():
    """Yield (product, category_name) rows from PostgreSQL."""
    stmt = (
        select(Product, ProductCategory.name.label("category_name"))
        .join(ProductCategory, Product.category_id == ProductCategory.id, isouter=True)
        .order_by(Product.id)
    )
    factory = get_session_factory()
    async with factory() as session:
        return (await session.execute(stmt)).all()


async def embed_products() -> int:
    """Embed all products and upsert into Qdrant. Returns product count."""
    embedder = get_embedder()
    qdrant = get_qdrant_client()

    # ── Ensure collection + payload indexes exist (idempotent) ──────
    # Shared contract with app startup — one definition of the vector store.
    await ensure_products_collection()

    rows = await load_products()
    logger.info("Loaded %d products from PostgreSQL", len(rows))

    if not rows:
        logger.info("No products to embed — run scripts.ingest_olist first.")
        return 0

    # Pre-fetch existing points so we can prune points for deleted products.
    # We collect (point_id, product_id) pairs and delete stale ones by point id
    # (avoids needing a keyword index on product_id).
    existing_points: list[tuple[str, str]] = []
    next_offset = None
    while True:
        points, next_offset = await qdrant.scroll(
            collection_name=PRODUCTS_COLLECTION,
            limit=256,
            offset=next_offset,
            with_payload=["product_id"],
            with_vectors=False,
        )
        existing_points.extend(
            (p.id, p.payload.get("product_id")) for p in points if p.payload
        )
        if next_offset is None:
            break

    db_product_ids = {str(product.id) for product, _ in rows}
    stale_point_ids = [
        point_id for point_id, product_id in existing_points if product_id not in db_product_ids
    ]
    if stale_point_ids:
        await qdrant.delete(
            collection_name=PRODUCTS_COLLECTION,
            points_selector=stale_point_ids,
            wait=True,
        )
        logger.info(
            "Removed %d stale points for %d deleted products",
            len(stale_point_ids),
            len({pid for _, pid in existing_points if pid not in db_product_ids}),
        )

    points = []
    for product, category_name in rows:
        chunk_texts = chunk_text(f"{product.name}. {product.description}")
        for i, chunk in enumerate(chunk_texts):
            points.append(
                {
                    "id": point_id(str(product.id), i),
                    "vector": None,  # filled in batch
                    "payload": {
                        "product_id": str(product.id),
                        "name": product.name,
                        "category": category_name or "",
                        "price": product.price,
                        "stock_quantity": product.stock_quantity,
                        "description": chunk,
                    },
                }
            )

    # Embed in batches and upsert
    from qdrant_client.models import PointStruct

    total_upserted = 0
    for batch_start in range(0, len(points), BATCH_SIZE):
        batch = points[batch_start : batch_start + BATCH_SIZE]
        texts = [p["payload"]["description"] for p in batch]
        vectors = embedder.embed(texts)
        qdrant_points = [
            PointStruct(id=p["id"], vector=vec, payload=p["payload"]) for p, vec in zip(batch, vectors, strict=True)
        ]
        await qdrant.upsert(
            collection_name=PRODUCTS_COLLECTION,
            points=qdrant_points,
            wait=True,
        )
        total_upserted += len(qdrant_points)
        logger.info("  Upserted %d/%d chunks...", total_upserted, len(points))

    return len(rows)


async def main():
    """Main entry point."""
    logger.info("═" * 60)
    logger.info("CALLIOPE — Product Embedding Pipeline")
    logger.info("═" * 60)

    product_count = await embed_products()

    logger.info("═" * 60)
    logger.info(
        "Embedded and indexed %d products into Qdrant (collection: %s, dims: %d)",
        product_count,
        PRODUCTS_COLLECTION,
        VECTOR_DIMENSION,
    )
    logger.info("═" * 60)

    await get_engine().dispose()


if __name__ == "__main__":
    asyncio.run(main())
