"""
OMNIVOICE — Beach & Outdoor category seed.

Idempotent: creates the category if missing and inserts any product
that does not already exist (matched by name). Safe to re-run.

    uv run python scripts/seed_beach.py

After seeding, re-run scripts/embed_products.py so the new products
are searchable through the RAG vector index.
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

from app.db.models import Product, ProductCategory  # noqa: E402
from app.db.session import get_engine, get_session_factory  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(levelname)-7s | %(name)s | %(message)s")
logger = logging.getLogger(__name__)

CATEGORY_NAME = "Beach & Outdoor"

BEACH_PRODUCTS: list[dict] = [
    {
        "name": "Beach Umbrella",
        "description": "Oversized 7ft beach umbrella with UV50 canopy, rust-resistant aluminium pole and sand anchor. Tilts to block the sun all afternoon.",
        "price": 42.5,
        "stock_quantity": 30,
    },
    {
        "name": "Inflatable Pool Float",
        "description": "Extra-large inflatable lounge float in bright summer colours. Quick-inflate valve, rip-resistant PVC, holds up to 120kg.",
        "price": 24.99,
        "stock_quantity": 45,
    },
    {
        "name": "Snorkel Set",
        "description": "Comfort-fit mask and dry-top snorkel set with tempered glass lens and anti-fog coating. Sizes for adults and kids.",
        "price": 34.75,
        "stock_quantity": 25,
    },
    {
        "name": "XL Sand-Free Beach Towel",
        "description": "Large 180x90cm microfibre beach towel that sheds sand instantly and dries three times faster than cotton.",
        "price": 19.9,
        "stock_quantity": 50,
    },
    {
        "name": "Portable Beach Cooler",
        "description": "20-litre rotomoulded cooler chest with 24h ice retention, bottle opener and shoulder strap. Keeps drinks cold on the sand.",
        "price": 59.0,
        "stock_quantity": 20,
    },
    {
        "name": "Rash Guard",
        "description": "UPF50+ long-sleeve rash guard for sun protection while swimming or surfing. Quick-dry fabric, flatlock seams.",
        "price": 27.4,
        "stock_quantity": 35,
    },
    {
        "name": "Waterproof Phone Pouch",
        "description": "IPX8 waterproof phone pouch rated to 10m. Touchscreen-friendly, floatable, perfect for beach days and boat trips.",
        "price": 12.25,
        "stock_quantity": 60,
    },
    {
        "name": "Beach Volleyball Set",
        "description": "Official-size beach volleyball with soft-touch casing, plus two pumps and a carry net bag for the full setup.",
        "price": 38.6,
        "stock_quantity": 18,
    },
]


async def seed() -> int:
    """Insert the Beach & Outdoor category and its products. Returns rows added."""
    added = 0
    async with get_session_factory()() as session:
        async with session.begin():
            category = (await session.scalars(select(ProductCategory).where(ProductCategory.name == CATEGORY_NAME))).first()
            if category is None:
                category = ProductCategory(name=CATEGORY_NAME, id=uuid.uuid4())
                session.add(category)
                await session.flush()
                logger.info("Created category: %s", CATEGORY_NAME)

            existing = set((await session.scalars(select(Product.name))).all())
            for spec in BEACH_PRODUCTS:
                if spec["name"] in existing:
                    logger.info("  exists: %s", spec["name"])
                    continue
                session.add(
                    Product(
                        name=spec["name"],
                        description=spec["description"],
                        price=spec["price"],
                        stock_quantity=spec["stock_quantity"],
                        category_id=category.id,
                    )
                )
                added += 1
                logger.info("  added:  %s", spec["name"])

    logger.info("Beach & Outdoor seed complete — %d new products", added)
    return added


async def main():
    """Main entry point."""
    logger.info("═" * 60)
    logger.info("OMNIVOICE — Beach & Outdoor Catalog Seed")
    logger.info("═" * 60)
    await seed()
    await get_engine().dispose()


if __name__ == "__main__":
    asyncio.run(main())
