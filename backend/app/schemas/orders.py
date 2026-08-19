"""Pydantic request/response schemas for Orders and OrderItems."""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, Field

from app.db.models import OrderStatus

# ── Order Items ─────────────────────────────────────────────────────


class OrderItemBase(BaseModel):
    product_id: UUID
    quantity: int = Field(ge=1, le=100)


class OrderItemCreate(OrderItemBase):
    pass


class OrderItemResponse(OrderItemBase):
    id: UUID
    unit_price: float
    subtotal: float

    model_config = {"from_attributes": True}


# ── Orders ──────────────────────────────────────────────────────────


class OrderCreate(BaseModel):
    customer_id: UUID
    items: list[OrderItemCreate] = Field(..., min_length=1)
    idempotency_key: str | None = None


class OrderResponse(BaseModel):
    id: UUID
    customer_id: UUID
    status: OrderStatus
    total_amount: float
    idempotency_key: str | None
    items: list[OrderItemResponse] = []
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


class OrderListResponse(BaseModel):
    orders: list[OrderResponse]
    total: int
