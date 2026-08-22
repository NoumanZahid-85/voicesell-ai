"""
Order service — Phase 4 transactional commerce layer.

Responsibilities:
  1. Create orders with stock validation + row-level locking.
  2. Fetch order details for a given customer.
  3. Cancel orders (soft-delete via status change).
  4. Search products by name for the voice agent to quote before ordering.

Design principles:

  Stock integrity ("never oversell"):
    SELECT ... FOR UPDATE NOWAIT is used on every product row we are about
    to deduct from.  NOWAIT means we fail immediately instead of waiting
    for a lock holder — in a concurrent environment this surfaces as a
    retryable error.  The alternative (FOR UPDATE SKIP LOCKED) would silently
    skip the row, which is wrong for a purchase.

  Idempotency ("never double-charge"):
    Every create_order call accepts an idempotency_key (SHA-256 of session_id
    + sorted product UUIDs + quantities).  The orders table has a UNIQUE
    constraint on this column.  A duplicate request will raise
    IntegrityError which we catch and return the existing order — same result,
    zero duplicates.

  Price snapshot:
    unit_price is captured from products.price at order creation time.
    This means price changes after the order is placed do not retroactively
    alter the charged amount.

  Soft deletion:
    Orders are never hard-deleted.  status=CANCELLED preserves the audit trail.
    Only PENDING or CONFIRMED orders can be cancelled by the customer.
    SHIPPED/DELIVERED orders require human intervention.

  Atomic transactions:
    All DB mutations for a single order (deducting stock + inserting order +
    inserting order_items) happen in a single SQLAlchemy transaction.
    If any step raises, the entire transaction is rolled back automatically.
"""

from __future__ import annotations

import hashlib
import json
import logging
import uuid
from dataclasses import dataclass

from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError, NoResultFound
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Customer, Order, OrderItem, OrderStatus, Product
from app.schemas.orders import OrderResponse

logger = logging.getLogger(__name__)


# ── Domain-level exceptions ───────────────────────────────────────────

class OrderError(Exception):
    """Base for all order-domain errors — safe to surface to the voice agent."""


class ProductNotFound(OrderError):
    def __init__(self, name_or_id: str):
        super().__init__(f"Product not found: '{name_or_id}'")
        self.name_or_id = name_or_id


class InsufficientStock(OrderError):
    def __init__(self, product_name: str, requested: int, available: int):
        super().__init__(
            f"Only {available} unit(s) of '{product_name}' in stock "
            f"(requested {requested})."
        )
        self.product_name = product_name
        self.requested = requested
        self.available = available


class OrderNotFound(OrderError):
    def __init__(self, order_id: str):
        super().__init__(f"Order not found: {order_id}")


class OrderNotCancellable(OrderError):
    def __init__(self, status: str):
        super().__init__(
            f"Orders with status '{status}' cannot be cancelled. "
            "Please contact support for shipped or delivered orders."
        )


# ── Data transfer objects ─────────────────────────────────────────────

@dataclass
class OrderLineInput:
    product_name: str   # natural-language name from voice (fuzzy matched to DB)
    quantity: int


@dataclass
class OrderItemResult:
    product_name: str
    quantity: int
    unit_price: float
    subtotal: float


@dataclass
class OrderResult:
    order_id: str
    status: str
    total_amount: float
    items: list[OrderItemResult]
    created_at: str


# ── Service functions ─────────────────────────────────────────────────


def build_idempotency_key(session_id: str, lines: list[OrderLineInput]) -> str:
    """
    Deterministic idempotency key: SHA-256(session_id + sorted items).

    Sorting ensures {"keyboard x2, mouse x1"} and {"mouse x1, keyboard x2"}
    produce the same key — the request is idempotent across item ordering.
    """
    payload = {
        "session_id": session_id,
        "items": sorted(
            [{"name": ln.product_name.lower().strip(), "qty": ln.quantity} for ln in lines],
            key=lambda x: x["name"],
        ),
    }
    return hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()[:64]


async def _find_product_by_name(
    session: AsyncSession, name: str, *, for_update: bool = False
) -> Product:
    """
    Case-insensitive fuzzy match on product name.

    Uses ILIKE '%name%' — matches partials ("blue sneakers" → "Blue Sports Sneakers").
    Returns the closest match by name length (prefer exact over broad match).
    Raises ProductNotFound if nothing matches.
    """
    from sqlalchemy import func as sqlfunc
    stmt = (
        select(Product)
        .where(Product.name.ilike(f"%{name}%"))  # type: ignore[attr-defined]
        .order_by(sqlfunc.length(Product.name))  # type: ignore[arg-type]
        .limit(1)
    )
    if for_update:
        stmt = stmt.with_for_update(nowait=True)
    result = await session.execute(stmt)
    product = result.scalar_one_or_none()
    if not product:
        raise ProductNotFound(name)
    return product


async def check_and_quote(
    session: AsyncSession,
    lines: list[OrderLineInput],
) -> list[dict]:
    """
    Check stock availability and return a price quote — NO stock deduction.

    Used by the voice agent to present pricing to the customer before
    asking for their confirmation.

    Returns:
        List of dicts with product_name, quantity, unit_price, subtotal,
        available_stock.

    Raises:
        ProductNotFound, InsufficientStock
    """
    quote = []
    for line in lines:
        product = await _find_product_by_name(session, line.product_name)
        if product.stock_quantity < line.quantity:
            raise InsufficientStock(product.name, line.quantity, product.stock_quantity)
        quote.append({
            "product_name": product.name,
            "product_id": str(product.id),
            "quantity": line.quantity,
            "unit_price": product.price,
            "subtotal": round(product.price * line.quantity, 2),
            "available_stock": product.stock_quantity,
        })
    return quote


async def create_order(
    session: AsyncSession,
    customer_id: str,
    lines: list[OrderLineInput],
    idempotency_key: str,
) -> OrderResult:
    """
    Create an order atomically with row-level stock locking.

    Flow:
      1. For each line: resolve product by fuzzy name, lock the row with
         SELECT FOR UPDATE NOWAIT.
      2. Validate stock for all items before touching anything.
      3. Deduct stock for each product.
      4. Insert Order + OrderItems in a single transaction.
      5. Handle idempotency collision (return existing order).

    Raises:
        ProductNotFound, InsufficientStock, OrderError (on lock contention)
    """
    try:
        order_items_data = []
        total = 0.0

        # ── Phase 1: lock all product rows ──────────────────────────
        for line in lines:
            # Lock row: FOR UPDATE NOWAIT (fail immediately on contention)
            product = await _find_product_by_name(session, line.product_name, for_update=True)
            if product.stock_quantity < line.quantity:
                raise InsufficientStock(product.name, line.quantity, product.stock_quantity)

            subtotal = round(product.price * line.quantity, 2)
            total += subtotal
            order_items_data.append({
                "product": product,
                "quantity": line.quantity,
                "unit_price": product.price,
                "subtotal": subtotal,
            })

        # ── Phase 2: create order ────────────────────────────────────
        customer_uuid = uuid.UUID(customer_id)
        # Get-or-create: this app has no signup flow, so the frontend
        # always sends a fixed demo customer_id. That row was never
        # seeded, so every order insert was failing with
        # "orders_customer_id_fkey" (500). Guard against any customer_id
        # that doesn't exist yet rather than relying on seed data alone.
        existing_customer = await session.get(Customer, customer_uuid)
        if existing_customer is None:
            session.add(
                Customer(
                    id=customer_uuid,
                    email=f"demo-{customer_uuid}@voicesell.local",
                    name="Demo Customer",
                )
            )
            await session.flush()

        order = Order(
            id=uuid.uuid4(),
            customer_id=customer_uuid,
            status=OrderStatus.CONFIRMED,   # auto-confirm for demo
            total_amount=round(total, 2),
            idempotency_key=idempotency_key,
        )
        session.add(order)
        # flush so we get order.id for FK in order_items
        await session.flush()

        # ── Phase 3: create items + deduct stock ─────────────────────
        for item_data in order_items_data:
            product = item_data["product"]

            # Deduct stock
            product.stock_quantity -= item_data["quantity"]
            session.add(product)

            oi = OrderItem(
                id=uuid.uuid4(),
                order_id=order.id,
                product_id=product.id,
                quantity=item_data["quantity"],
                unit_price=item_data["unit_price"],
                subtotal=item_data["subtotal"],
            )
            session.add(oi)

        await session.commit()
        logger.info(
            "Order created: id=%s customer=%s total=%.2f",
            order.id, customer_id, order.total_amount,
        )

        return OrderResult(
            order_id=str(order.id),
            status=order.status,
            total_amount=order.total_amount,
            items=[
                OrderItemResult(
                    product_name=d["product"].name,
                    quantity=d["quantity"],
                    unit_price=d["unit_price"],
                    subtotal=d["subtotal"],
                )
                for d in order_items_data
            ],
            created_at=order.created_at.isoformat() if order.created_at else "",
        )

    except IntegrityError:
        # Idempotency collision — fetch and return the existing order
        await session.rollback()
        logger.info("Idempotency hit for key=%s — returning existing order", idempotency_key)
        return await _fetch_order_by_idem_key(session, idempotency_key)

    except (ProductNotFound, InsufficientStock):
        await session.rollback()
        raise

    except Exception as exc:
        await session.rollback()
        logger.exception("Unexpected error creating order: %s", exc)
        raise OrderError(f"Order creation failed: {exc}") from exc


async def _fetch_order_by_idem_key(session: AsyncSession, key: str) -> OrderResult:
    stmt = select(Order).where(Order.idempotency_key == key)
    result = await session.execute(stmt)
    order = result.scalar_one_or_none()
    if not order:
        raise OrderError("Idempotency collision but original order not found.")
    await session.refresh(order, ["items"])
    return _to_result(order)


async def get_order(session: AsyncSession, order_id: str, customer_id: str) -> OrderResult:
    """
    Fetch order details, scoped to customer_id for security.

    Raises: OrderNotFound
    """
    stmt = (
        select(Order)
        .where(
            Order.id == uuid.UUID(order_id),  # type: ignore[arg-type]
            Order.customer_id == uuid.UUID(customer_id),  # type: ignore[arg-type]
        )
    )
    result = await session.execute(stmt)
    order = result.scalar_one_or_none()
    if not order:
        raise OrderNotFound(order_id)
    await session.refresh(order, ["items"])
    return _to_result(order)


async def get_order_response(
    session: AsyncSession, order_id: str, customer_id: str
) -> OrderResponse:
    """
    Fetch an order and serialize it for the REST API — the single serializer
    for the API-facing shape. Scoped to customer_id for security.

    Raises: OrderNotFound
    """
    stmt = (
        select(Order)
        .where(
            Order.id == uuid.UUID(order_id),  # type: ignore[arg-type]
            Order.customer_id == uuid.UUID(customer_id),  # type: ignore[arg-type]
        )
    )
    result = await session.execute(stmt)
    order = result.scalar_one_or_none()
    if not order:
        raise OrderNotFound(order_id)
    await session.refresh(order, ["items"])
    return OrderResponse.model_validate(order)


async def cancel_order(
    session: AsyncSession,
    order_id: str,
    customer_id: str,
) -> OrderResult:
    """
    Soft-cancel an order and restore stock.

    Only PENDING and CONFIRMED orders can be cancelled.
    Stock is restored atomically with the status change.

    Raises: OrderNotFound, OrderNotCancellable
    """
    stmt = (
        select(Order)
        .where(
            Order.id == uuid.UUID(order_id),  # type: ignore[arg-type]
            Order.customer_id == uuid.UUID(customer_id),  # type: ignore[arg-type]
        )
        .with_for_update(nowait=True)
    )
    result = await session.execute(stmt)
    order = result.scalar_one_or_none()
    if not order:
        raise OrderNotFound(order_id)

    if order.status not in (OrderStatus.PENDING, OrderStatus.CONFIRMED):
        raise OrderNotCancellable(order.status)

    # Load items so we can restore stock
    await session.refresh(order, ["items"])

    try:
        # Restore stock for each item
        for item in order.items:
            stmt_upd = (
                update(Product)
                .where(Product.id == item.product_id)
                .values(stock_quantity=Product.stock_quantity + item.quantity)
            )
            await session.execute(stmt_upd)

        order.status = OrderStatus.CANCELLED
        session.add(order)
        await session.commit()
        logger.info("Order cancelled: id=%s customer=%s", order_id, customer_id)
        return _to_result(order)

    except Exception as exc:
        await session.rollback()
        logger.exception("Cancel order failed for id=%s: %s", order_id, exc)
        raise OrderError(f"Cancel failed: {exc}") from exc


async def list_customer_orders(
    session: AsyncSession,
    customer_id: str,
    limit: int = 5,
) -> list[OrderResult]:
    """Return the most recent `limit` orders for a customer."""
    stmt = (
        select(Order)
        .where(Order.customer_id == uuid.UUID(customer_id))  # type: ignore[arg-type]
        .order_by(Order.created_at.desc())  # type: ignore[attr-defined]
        .limit(limit)
    )
    result = await session.execute(stmt)
    orders = result.scalars().all()
    results = []
    for order in orders:
        await session.refresh(order, ["items"])
        results.append(_to_result(order))
    return results


def _to_result(order: Order) -> OrderResult:
    return OrderResult(
        order_id=str(order.id),
        status=str(order.status),
        total_amount=order.total_amount,
        items=[
            OrderItemResult(
                product_name=item.product.name if item.product else str(item.product_id),
                quantity=item.quantity,
                unit_price=item.unit_price,
                subtotal=item.subtotal,
            )
            for item in (order.items or [])
        ],
        created_at=order.created_at.isoformat() if order.created_at else "",
    )
