"""
Health check endpoint — verifies PostgreSQL and Qdrant connectivity.

This is the first endpoint to call after deployment to confirm all
infrastructure is reachable.
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, Depends
from qdrant_client import AsyncQdrantClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import get_db
from app.schemas.health import HealthResponse
from app.services.qdrant_client import get_qdrant_client

logger = logging.getLogger(__name__)

router = APIRouter(tags=["health"])


@router.get("/health", response_model=HealthResponse)
async def health_check(
    db: AsyncSession = Depends(get_db),
) -> HealthResponse:
    """Check connectivity to PostgreSQL and Qdrant."""

    # ── PostgreSQL ──────────────────────────────────────────────────
    db_status = "disconnected"
    try:
        await db.execute(text("SELECT 1"))
        db_status = "connected"
    except Exception as exc:
        logger.error("PostgreSQL health check failed: %s", exc)

    # ── Qdrant ──────────────────────────────────────────────────────
    qdrant_status = "disconnected"
    try:
        qdrant: AsyncQdrantClient = get_qdrant_client()
        # list collections is a lightweight health probe
        await qdrant.get_collections()
        qdrant_status = "connected"
    except Exception as exc:
        logger.error("Qdrant health check failed: %s", exc)

    overall = "ok" if db_status == "connected" and qdrant_status == "connected" else "degraded"

    return HealthResponse(status=overall, db=db_status, qdrant=qdrant_status)
