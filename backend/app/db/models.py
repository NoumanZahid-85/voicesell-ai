"""
Async SQLAlchemy models for the product catalog and order system.

Why SQLAlchemy over raw SQL: ORM provides type safety, relationship loading,
and migration support. Async engine matches FastAPI's event loop.
"""

from __future__ import annotations

import enum
import uuid
from datetime import datetime

from sqlalchemy import (
    JSON,
    Column,
    DateTime,
    Enum,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import DeclarativeBase, relationship

# JSONB is Postgres-only; SQLite (tests) falls back to plain JSON.
JSONB = JSON().with_variant(JSON, "sqlite")


class Base(DeclarativeBase):
    """Declarative base for all models."""

    pass


# ── Enums ───────────────────────────────────────────────────────────


class OrderStatus(enum.StrEnum):
    PENDING = "pending"
    CONFIRMED = "confirmed"
    SHIPPED = "shipped"
    DELIVERED = "delivered"
    CANCELLED = "cancelled"


# ── Models ──────────────────────────────────────────────────────────


class ProductCategory(Base):
    __tablename__ = "product_categories"

    id: uuid.UUID = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)  # type: ignore[assignment]
    name: str = Column(String(255), nullable=False, unique=True, index=True)  # type: ignore[assignment]
    parent_id: uuid.UUID | None = Column(  # type: ignore[assignment]
        UUID(as_uuid=True),
        ForeignKey("product_categories.id", ondelete="SET NULL"),
        nullable=True,
    )

    # Self-referential relationship for category hierarchy
    parent = relationship("ProductCategory", remote_side="ProductCategory.id", backref="children")
    products = relationship("Product", back_populates="category")


class Product(Base):
    __tablename__ = "products"

    id: uuid.UUID = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)  # type: ignore[assignment]
    name: str = Column(String(255), nullable=False, index=True)  # type: ignore[assignment]
    description: str = Column(String, nullable=False, default="")  # type: ignore[assignment]
    category_id: uuid.UUID | None = Column(  # type: ignore[assignment]
        UUID(as_uuid=True),
        ForeignKey("product_categories.id", ondelete="SET NULL"),
        nullable=True,
    )
    price: float = Column(Float, nullable=False, default=0.0)  # type: ignore[assignment]
    stock_quantity: int = Column(Integer, nullable=False, default=0)  # type: ignore[assignment]
    weight_kg: float | None = Column(Float, nullable=True)  # type: ignore[assignment]
    dimensions_json = Column(JSONB, nullable=True)  # {"length": x, "width": y, "height": z}
    image_url: str | None = Column(String(512), nullable=True)  # type: ignore[assignment]
    created_at: datetime = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)  # type: ignore[assignment]
    updated_at: datetime = Column(  # type: ignore[assignment]
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )

    category = relationship("ProductCategory", back_populates="products")
    order_items = relationship("OrderItem", back_populates="product")

    __table_args__ = (Index("ix_products_category", "category_id"),)


class Customer(Base):
    __tablename__ = "customers"

    id: uuid.UUID = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)  # type: ignore[assignment]
    email: str = Column(String(255), nullable=False, unique=True, index=True)  # type: ignore[assignment]
    name: str = Column(String(255), nullable=False)  # type: ignore[assignment]
    auth_user_id: str | None = Column(
        String(255), nullable=True, unique=True
    )  # Supabase Auth FK  # type: ignore[assignment]
    created_at: datetime = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)  # type: ignore[assignment]

    orders = relationship("Order", back_populates="customer")


class Order(Base):
    __tablename__ = "orders"

    id: uuid.UUID = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)  # type: ignore[assignment]
    customer_id: uuid.UUID = Column(  # type: ignore[assignment]
        UUID(as_uuid=True),
        ForeignKey("customers.id", ondelete="CASCADE"),
        nullable=False,
    )
    status: OrderStatus = Column(  # type: ignore[assignment]
        Enum(OrderStatus, name="order_status", create_constraint=True),
        default=OrderStatus.PENDING,
        nullable=False,
    )
    total_amount: float = Column(Float, nullable=False, default=0.0)  # type: ignore[assignment]
    idempotency_key: str | None = Column(String(64), unique=True, nullable=True)  # type: ignore[assignment]
    created_at: datetime = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)  # type: ignore[assignment]
    updated_at: datetime = Column(  # type: ignore[assignment]
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )

    customer = relationship("Customer", back_populates="orders")
    items = relationship("OrderItem", back_populates="order", cascade="all, delete-orphan")

    __table_args__ = (Index("ix_orders_customer_status", "customer_id", "status"),)


class OrderItem(Base):
    __tablename__ = "order_items"

    id: uuid.UUID = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)  # type: ignore[assignment]
    order_id: uuid.UUID = Column(  # type: ignore[assignment]
        UUID(as_uuid=True),
        ForeignKey("orders.id", ondelete="CASCADE"),
        nullable=False,
    )
    product_id: uuid.UUID = Column(  # type: ignore[assignment]
        UUID(as_uuid=True),
        ForeignKey("products.id", ondelete="RESTRICT"),
        nullable=False,
    )
    quantity: int = Column(Integer, nullable=False)  # type: ignore[assignment]
    unit_price: float = Column(Float, nullable=False)  # type: ignore[assignment]
    subtotal: float = Column(Float, nullable=False)  # type: ignore[assignment]

    order = relationship("Order", back_populates="items")
    product = relationship("Product", back_populates="order_items")

    @property
    def product_name(self) -> str:
        """Human display name for API responses.

        Strips the seeded "#NN" stock-keeping suffix ("Bluetooth Speaker
        #07" -> "Bluetooth Speaker") so orders never show jargon codes.
        Falls back to the raw name, then the product id.
        """
        import re

        if self.product and self.product.name:
            return re.sub(r"\s*#\d+\s*$", "", self.product.name).strip() or self.product.name
        return str(self.product_id)


class ProductAssociation(Base):
    """Frequently-bought-together rules mined from order history (Phase 5)."""

    __tablename__ = "product_associations"

    id: uuid.UUID = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)  # type: ignore[assignment]
    product_a_id: uuid.UUID = Column(  # type: ignore[assignment]
        UUID(as_uuid=True),
        ForeignKey("products.id", ondelete="CASCADE"),
        nullable=False,
    )
    product_b_id: uuid.UUID = Column(  # type: ignore[assignment]
        UUID(as_uuid=True),
        ForeignKey("products.id", ondelete="CASCADE"),
        nullable=False,
    )
    confidence: float = Column(Float, nullable=False)  # type: ignore[assignment]
    support: float = Column(Float, nullable=False)  # type: ignore[assignment]
    lift: float = Column(Float, nullable=False)  # type: ignore[assignment]

    product_a = relationship("Product", foreign_keys="ProductAssociation.product_a_id")
    product_b = relationship("Product", foreign_keys="ProductAssociation.product_b_id")

    __table_args__ = (
        UniqueConstraint("product_a_id", "product_b_id", name="uq_product_association"),
        Index("ix_assoc_product_a", "product_a_id"),
    )


class RecommendationLog(Base):
    """Logs every upsell recommendation for future optimisation (Phase 5)."""

    __tablename__ = "recommendation_logs"

    id: uuid.UUID = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)  # type: ignore[assignment]
    session_id: str = Column(String(255), nullable=False, index=True)  # type: ignore[assignment]
    recommended_product_id: uuid.UUID = Column(  # type: ignore[assignment]
        UUID(as_uuid=True),
        ForeignKey("products.id", ondelete="CASCADE"),
        nullable=False,
    )
    source: str = Column(String(32), nullable=False)  # "association" | "vector"
    was_accepted: bool = Column(Integer, nullable=False, default=False)  # type: ignore[assignment]
    timestamp: datetime = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)  # type: ignore[assignment]

    recommended_product = relationship("Product", foreign_keys="RecommendationLog.recommended_product_id")

    __table_args__ = (Index("ix_reclogs_session", "session_id"),)
