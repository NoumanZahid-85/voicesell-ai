"""
CALLIOPE — FastAPI application entrypoint.

Initializes the application and registers all API routers. Startup/shutdown
wiring lives in app.bootstrap — this file is an assembly list only.
"""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from app.api.admin import router as admin_router
from app.api.categories import router as categories_router
from app.api.chat import router as chat_router
from app.api.health import router as health_router
from app.api.orders import router as orders_router
from app.api.products import router as products_router
from app.api.voice import router as voice_router
from app.bootstrap import auto_seed, ensure_schema, ensure_vector_store, shutdown, warm_embeddings
from app.core.config import get_settings

logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(levelname)-7s | %(name)s | %(message)s")
logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """
    Startup / shutdown lifecycle.

    - Creates all DB tables (dev convenience — Alembic handles prod migrations)
    - Ensures the Qdrant products collection exists
    - Pre-warms the embedding model off the event loop
    - Auto-seeds the catalog once, if empty (see bootstrap.auto_seed docstring —
      this exists because the free Render plan has no Shell/Job access)
    """
    settings = get_settings()
    logger.info("Starting %s (debug=%s)", settings.app_name, settings.debug)

    await ensure_schema()
    await ensure_vector_store()
    await warm_embeddings()
    await auto_seed()

    yield  # ← app runs here

    await shutdown()


def create_app() -> FastAPI:
    """Application factory."""
    settings = get_settings()

    app = FastAPI(
        title=settings.app_name,
        description="Voice-enabled AI sales chatbot with RAG-grounded product Q&A, order management, and upsell recommendations.",
        version="0.1.0",
        lifespan=lifespan,
    )

    # ── CORS ────────────────────────────────────────────────────────
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],  # tightened in Phase 8 for production
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    # ── Routers ─────────────────────────────────────────────────────
    app.include_router(health_router)
    app.include_router(admin_router)
    app.include_router(categories_router)
    app.include_router(chat_router)
    app.include_router(orders_router)
    app.include_router(voice_router)
    app.include_router(products_router)

    # ── Static test page (dev only) ──────────────────────────────────
    _test_html = Path(__file__).resolve().parent.parent / "test-voice.html"
    if settings.debug and _test_html.exists():
        app.mount("/test-voice.html", StaticFiles(directory=str(_test_html.parent), html=True), name="test-voice")

    return app


app = create_app()
