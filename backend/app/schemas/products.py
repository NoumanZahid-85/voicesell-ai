"""Pydantic request/response schemas for Products and Categories."""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, Field

# ── Product Category ────────────────────────────────────────────────


class CategoryBase(BaseModel):
    name: str = Field(..., max_length=255)
    parent_id: UUID | None = None


class CategoryCreate(CategoryBase):
    pass


class CategoryResponse(CategoryBase):
    id: UUID

    model_config = {"from_attributes": True}


class CategorySummary(BaseModel):
    """Category with its live product count — used by the catalog browser."""

    id: UUID
    name: str
    product_count: int


class CategoryListResponse(BaseModel):
    categories: list[CategorySummary]
    total: int


# ── Product ─────────────────────────────────────────────────────────


class DimensionsJSON(BaseModel):
    length: float | None = None
    width: float | None = None
    height: float | None = None


class ProductBase(BaseModel):
    name: str = Field(..., max_length=255)
    description: str = ""
    price: float = Field(ge=0)
    stock_quantity: int = Field(ge=0, default=0)
    weight_kg: float | None = None
    dimensions_json: DimensionsJSON | None = None
    image_url: str | None = None


class ProductCreate(ProductBase):
    category_id: UUID | None = None


class ProductUpdate(BaseModel):
    name: str | None = None
    description: str | None = None
    price: float | None = Field(default=None, ge=0)
    stock_quantity: int | None = Field(default=None, ge=0)
    weight_kg: float | None = None
    dimensions_json: DimensionsJSON | None = None
    image_url: str | None = None
    category_id: UUID | None = None


class ProductResponse(ProductBase):
    id: UUID
    category_id: UUID | None = None
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


class ProductListResponse(BaseModel):
    products: list[ProductResponse]
    total: int
