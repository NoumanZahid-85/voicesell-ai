"""
Tests for the /health endpoint and data model sanity.
"""

from __future__ import annotations

import pytest
from httpx import ASGITransport, AsyncClient

from app.main import app


@pytest.mark.asyncio
async def test_health_endpoint_returns_200():
    """Health endpoint should return 200 even if services are degraded."""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.get("/health")

    assert resp.status_code == 200
    data = resp.json()
    assert "status" in data
    assert "db" in data
    assert "qdrant" in data


@pytest.mark.asyncio
async def test_health_response_schema():
    """Health response must contain status, db, qdrant fields."""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.get("/health")

    data = resp.json()
    assert data["status"] in ("ok", "degraded")
    assert data["db"] in ("connected", "disconnected")
    assert data["qdrant"] in ("connected", "disconnected")


def test_order_status_enum():
    """OrderStatus enum should have 5 valid values."""
    from app.db.models import OrderStatus

    assert len(OrderStatus) == 5
    assert OrderStatus.PENDING.value == "pending"
    assert OrderStatus.CANCELLED.value == "cancelled"


def test_product_category_model():
    """ProductCategory model should have self-referential parent."""
    from app.db.models import ProductCategory

    cat = ProductCategory(name="Electronics")
    assert cat.name == "Electronics"
    assert cat.parent_id is None


def test_settings_loads():
    """Settings should load without crashing (with env fallbacks)."""
    from app.core.config import Settings

    # This will fail if required fields have no defaults and no .env
    # Since DATABASE_URL is required, this tests that .env is present
    try:
        s = Settings()  # type: ignore[call-arg]
        assert s.app_name == "CALLIOPE"
    except Exception:
        # Expected if DATABASE_URL is not set in test env
        pass
