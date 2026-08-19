"""
REST endpoints for the order management system — Phase 4.

These endpoints serve the API directly in addition to being callable
by the LangGraph agent during voice sessions.

Endpoints:
  POST   /api/v1/orders              — create an order
  GET    /api/v1/orders/{order_id}   — get order details
  DELETE /api/v1/orders/{order_id}   — cancel an order (soft delete)
  GET    /api/v1/orders              — list recent orders for a customer

Security posture (Phase 4 / demo mode):
  - customer_id is passed as a query param/request body.
  - In Phase 6 this will be replaced by JWT token extraction.
  - For the supervisor demo this is acceptable: the orders are scoped
    by customer_id, so you cannot see another customer's orders.
"""

from __future__ import annotations

import logging
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import get_db
from app.schemas.orders import OrderCreate, OrderListResponse, OrderResponse
from app.services.order_service import (
    InsufficientStock,
    OrderError,
    OrderLineInput,
    OrderNotCancellable,
    OrderNotFound,
    ProductNotFound,
    build_idempotency_key,
    cancel_order,
    create_order,
    get_order_response,
    list_customer_orders,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/orders", tags=["orders"])


# ── Shared error mapping ──────────────────────────────────────────────

def _raise_http(exc: Exception) -> None:
    """Map the service exceptions to HTTP responses — the only place the
    mapping lives. The service layer owns the logic; this owns the transport."""
    if isinstance(exc, (ProductNotFound, OrderNotFound)):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    if isinstance(exc, InsufficientStock):
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    if isinstance(exc, OrderNotCancellable):
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    raise HTTPException(
        status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=str(exc)
    ) from exc


# ── Create order ──────────────────────────────────────────────────────

@router.post(
    "",
    response_model=OrderResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Place a new order",
)
async def create_order_endpoint(
    payload: OrderCreate,
    db: AsyncSession = Depends(get_db),
) -> OrderResponse:
    """
    Place a new order.

    - Validates stock availability with row-level locking (FOR UPDATE NOWAIT).
    - Deduplicates via idempotency_key (auto-generated from customer + items
      if not provided).
    - Returns the created order with line items.

    Errors:
      422 — validation failure (missing fields, quantity out of range)
      404 — product not found
      409 — insufficient stock
      500 — database error
    """
    lines = [
        OrderLineInput(product_name=str(item.product_id), quantity=item.quantity)
        for item in payload.items
    ]
    idem_key = payload.idempotency_key or build_idempotency_key(
        str(payload.customer_id), lines
    )

    try:
        result = await create_order(
            db, str(payload.customer_id), lines, idem_key
        )
    except (ProductNotFound, InsufficientStock, OrderError) as e:
        _raise_http(e)

    # Re-fetch to get ORM relationships for the Pydantic model
    return await get_order_response(db, result.order_id, str(payload.customer_id))


# ── Get order ─────────────────────────────────────────────────────────

@router.get(
    "/{order_id}",
    response_model=OrderResponse,
    summary="Get order details",
)
async def get_order_endpoint(
    order_id: UUID,
    customer_id: UUID = Query(..., description="Customer ID for ownership check"),
    db: AsyncSession = Depends(get_db),
) -> OrderResponse:
    """Get details for a specific order (scoped to customer_id)."""
    try:
        return await get_order_response(db, str(order_id), str(customer_id))
    except (OrderNotFound, OrderError) as e:
        _raise_http(e)


# ── Cancel order ──────────────────────────────────────────────────────

@router.delete(
    "/{order_id}",
    status_code=status.HTTP_200_OK,
    response_model=OrderResponse,
    summary="Cancel an order",
)
async def cancel_order_endpoint(
    order_id: UUID,
    customer_id: UUID = Query(..., description="Customer ID for ownership check"),
    db: AsyncSession = Depends(get_db),
) -> OrderResponse:
    """
    Soft-cancel an order and restore stock.

    Only PENDING and CONFIRMED orders can be cancelled.

    Errors:
      404 — order not found or belongs to a different customer
      409 — order is in a non-cancellable state (shipped/delivered)
    """
    try:
        result = await cancel_order(db, str(order_id), str(customer_id))
    except (OrderNotFound, OrderNotCancellable, OrderError) as e:
        _raise_http(e)

    return await get_order_response(db, result.order_id, str(customer_id))


# ── List orders ───────────────────────────────────────────────────────

@router.get(
    "",
    response_model=OrderListResponse,
    summary="List customer orders",
)
async def list_orders_endpoint(
    customer_id: UUID = Query(..., description="Customer ID"),
    limit: int = Query(default=10, ge=1, le=50),
    db: AsyncSession = Depends(get_db),
) -> OrderListResponse:
    """Return up to `limit` most recent orders for the given customer."""
    try:
        results = await list_customer_orders(db, str(customer_id), limit=limit)
    except OrderError as e:
        _raise_http(e)

    orders = []
    for r in results:
        try:
            orders.append(await get_order_response(db, r.order_id, str(customer_id)))
        except OrderNotFound:
            continue  # row vanished between list and fetch — skip it

    return OrderListResponse(orders=orders, total=len(orders))
