"""
CALLIOPE — Products API.

Read-only catalog listing for the frontend admin console. Full product
CRUD + admin auth lands in Phase 8; this endpoint just serves the
already-embedded catalog so the UI is never blind.

NOTE: any order flows must NOT mutate stock through this router — stock
is managed exclusively by the order service.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Query
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Product
from app.db.session import get_db
from app.schemas.products import ProductListResponse

router = APIRouter(prefix="/api/v1/products", tags=["products"])

DbSession = Annotated[AsyncSession, Depends(get_db)]


@router.get(
    "",
    response_model=ProductListResponse,
    summary="List products (read-only catalog)",
)
async def list_products_endpoint(
    db: DbSession,
    search: str | None = Query(
        default=None,
        max_length=255,
        description="Case-insensitive name filter",
    ),
    category_id: str | None = Query(
        default=None,
        description="Filter by category UUID",
    ),
    limit: int = Query(default=100, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
) -> ProductListResponse:
    """List products with optional name search and pagination."""
    filters = []
    if search and search.strip():
        filters.append(Product.name.ilike(f"%{search.strip()}%"))
    if category_id:
        filters.append(Product.category_id == category_id)

    base = select(Product)
    for f in filters:
        base = base.where(f)

    total = await db.scalar(select(func.count()).select_from(Product).where(*filters)) or 0

    stmt = base.order_by(Product.name.asc()).offset(offset).limit(limit)
    rows = (await db.scalars(stmt)).all()

    return ProductListResponse(products=list(rows), total=total)
