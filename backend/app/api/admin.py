"""
Admin console aggregates — the numbers the supervisor scans at a glance:
order counters, revenue, and per-category remaining stock with one-click
drill-down into the catalog.

Read-only; no auth yet (Phase 8 adds JWT admin auth).
"""

from __future__ import annotations

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy import case, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Order, OrderStatus, Product, ProductCategory
from app.db.session import get_db

router = APIRouter(prefix="/api/v1/admin", tags=["admin"])

LOW_STOCK_THRESHOLD = 10


class CategoryInventory(BaseModel):
    id: str
    name: str
    product_count: int
    total_stock: int
    low_stock_count: int


class LowStockItem(BaseModel):
    id: str
    name: str
    stock_quantity: int
    category: str | None


class AdminStatsResponse(BaseModel):
    orders_total: int
    orders_active: int
    orders_cancelled: int
    revenue_total: float
    categories: list[CategoryInventory]
    low_stock: list[LowStockItem]


@router.get(
    "/stats",
    response_model=AdminStatsResponse,
    summary="Order counters, revenue, and per-category inventory for the admin console",
)
async def admin_stats(db: AsyncSession = Depends(get_db)) -> AdminStatsResponse:
    """One round trip for every number the admin landing page shows."""

    orders_total = await db.scalar(select(func.count()).select_from(Order)) or 0
    orders_cancelled = (
        await db.scalar(
            select(func.count()).select_from(Order).where(Order.status == OrderStatus.CANCELLED)
        )
        or 0
    )
    revenue_total = (
        await db.scalar(
            select(func.coalesce(func.sum(Order.total_amount), 0.0)).where(
                Order.status != OrderStatus.CANCELLED
            )
        )
        or 0.0
    )

    cat_rows = (
        await db.execute(
            select(
                ProductCategory.id,
                ProductCategory.name,
                func.count(Product.id),
                func.coalesce(func.sum(Product.stock_quantity), 0),
                func.coalesce(
                    func.sum(case((Product.stock_quantity < LOW_STOCK_THRESHOLD, 1), else_=0)),
                    0,
                ),
            )
            .outerjoin(Product, Product.category_id == ProductCategory.id)
            .group_by(ProductCategory.id, ProductCategory.name)
            .order_by(ProductCategory.name.asc())
        )
    ).all()

    categories = [
        CategoryInventory(
            id=str(row[0]),
            name=row[1],
            product_count=row[2],
            total_stock=int(row[3]),
            low_stock_count=int(row[4]),
        )
        for row in cat_rows
    ]

    low_rows = (
        await db.execute(
            select(Product, ProductCategory.name)
            .outerjoin(ProductCategory, Product.category_id == ProductCategory.id)
            .where(Product.stock_quantity < LOW_STOCK_THRESHOLD)
            .order_by(Product.stock_quantity.asc())
            .limit(12)
        )
    ).all()

    low_stock = [
        LowStockItem(
            id=str(p.id),
            name=p.name,
            stock_quantity=p.stock_quantity,
            category=cat_name,
        )
        for p, cat_name in low_rows
    ]

    return AdminStatsResponse(
        orders_total=int(orders_total),
        orders_active=int(orders_total) - int(orders_cancelled),
        orders_cancelled=int(orders_cancelled),
        revenue_total=float(revenue_total),
        categories=categories,
        low_stock=low_stock,
    )
