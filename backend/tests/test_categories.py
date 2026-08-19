"""
Tests for GET /api/v1/categories — the catalog category index.
"""

from __future__ import annotations

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select

from app.db.models import Product, ProductCategory
from app.db.session import get_db
from app.main import app


async def _seed_catalog() -> tuple[str, str]:
    """Insert two categories with one product each; return their UUIDs."""
    async for session in get_db():
        kitchen = ProductCategory(name="Test Kitchen")
        toys = ProductCategory(name="Test Toys")
        session.add_all([kitchen, toys])
        await session.flush()

        session.add_all(
            [
                Product(name="Spatula", price=5.0, stock_quantity=10, category_id=kitchen.id),
                Product(name="Skipping Rope", price=3.0, stock_quantity=5, category_id=toys.id),
                Product(name="Uncategorised", price=1.0, stock_quantity=1),
            ]
        )
        await session.commit()
        return str(kitchen.id), str(toys.id)


async def _cleanup():
    async for session in get_db():
        await session.execute(Product.__table__.delete())
        await session.execute(ProductCategory.__table__.delete())
        await session.commit()


@pytest.mark.asyncio
async def test_categories_endpoint_lists_with_counts():
    """Categories endpoint returns every category with a live product count."""
    kitchen_id, toys_id = await _seed_catalog()
    try:
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.get("/api/v1/categories")

        assert resp.status_code == 200
        data = resp.json()
        assert data["total"] == 2

        by_name = {c["name"]: c for c in data["categories"]}
        assert by_name["Test Kitchen"]["product_count"] == 1
        assert by_name["Test Toys"]["product_count"] == 1
        assert by_name["Test Kitchen"]["id"] == kitchen_id
        assert by_name["Test Toys"]["id"] == toys_id
    finally:
        await _cleanup()


@pytest.mark.asyncio
async def test_categories_include_empty_categories():
    """Categories with zero products must still appear (count 0)."""
    await _seed_catalog()
    try:
        async for session in get_db():
            session.add(ProductCategory(name="Test Empty"))
            await session.commit()

        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.get("/api/v1/categories")

        data = resp.json()
        by_name = {c["name"]: c for c in data["categories"]}
        assert by_name["Test Empty"]["product_count"] == 0
    finally:
        await _cleanup()
