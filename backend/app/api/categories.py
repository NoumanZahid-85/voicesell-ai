"""
CALLIOPE — Categories API.

Serves the category index with live product counts so the catalog
browser can render a "radio tuner" rail of departments. Read-only,
like the products endpoint.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Product, ProductCategory
from app.db.session import get_db
from app.schemas.products import CategoryListResponse, CategorySummary

router = APIRouter(prefix="/api/v1/categories", tags=["categories"])

DbSession = Annotated[AsyncSession, Depends(get_db)]


@router.get(
    "",
    response_model=CategoryListResponse,
    summary="List categories with live product counts",
)
async def list_categories_endpoint(db: DbSession) -> CategoryListResponse:
    """List every category with the number of products it contains."""
    rows = await db.execute(
        select(ProductCategory.id, ProductCategory.name, func.count(Product.id))
        .outerjoin(Product, Product.category_id == ProductCategory.id)
        .group_by(ProductCategory.id, ProductCategory.name)
        .order_by(ProductCategory.name.asc())
    )
    categories = [CategorySummary(id=row[0], name=row[1], product_count=row[2]) for row in rows]
    return CategoryListResponse(categories=categories, total=len(categories))
