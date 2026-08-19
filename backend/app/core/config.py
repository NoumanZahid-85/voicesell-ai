"""
Centralized settings using pydantic-settings for environment variable management.
Validates env vars at startup — catches missing keys before runtime.
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

# Find .env — works whether you run from backend/ or project root
_backend_dir = Path(__file__).resolve().parent.parent.parent  # backend/
_project_root = _backend_dir.parent  # project root

# pydantic-settings accepts a tuple: first match wins
_env_files = tuple(str(p) for p in [_backend_dir / ".env", _project_root / ".env"] if p.exists()) or (".env",)


class Settings(BaseSettings):
    """Application configuration loaded from environment / .env file."""

    model_config = SettingsConfigDict(
        env_file=_env_files,
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    # ── Database (required for Phase 1) ─────────────────────────────
    database_url: str  # postgresql+asyncpg://...
    supabase_url: str = ""
    supabase_key: str = ""

    # ── Vector DB (required for Phase 1) ────────────────────────────
    qdrant_url: str = "http://localhost:6333"
    qdrant_api_key: str = ""

    # ── LLM Providers (optional until Phase 2) ─────────────────────
    groq_api_key: str = ""
    openai_api_key: str = ""
    openai_base_url: str = ""
    openai_model_name: str = ""
    gemini_api_key: str = ""

    # ── Voice Services (optional until Phase 3) ────────────────────
    daily_api_key: str = ""
    deepgram_api_key: str = ""
    cartesia_api_key: str = ""

    # ── Redis / Upstash (optional until Phase 7) ───────────────────
    redis_url: str = ""

    # ── Observability (optional until Phase 7) ─────────────────────
    langsmith_api_key: str = ""
    otel_exporter_endpoint: str = ""

    # ── RAG (Phase 2) ─────────────────────────────────────────────
    # Embedding model is now hardcoded in app/services/embeddings.py
    # (EMBEDDING_MODEL, EMBEDDING_DIMENSIONS) since it calls the OpenAI
    # API rather than loading a configurable local model.
    rag_top_k: int = 5
    rag_cache_ttl_seconds: int = 86400  # 24h FAQ cache
    rag_memory_turns: int = 10  # conversation history kept per session
    rag_vector_score_threshold: float = 0.45  # below this → keyword fallback

    # ── Application ────────────────────────────────────────────────
    app_name: str = "CALLIOPE"
    debug: bool = False
    log_level: str = "INFO"


@lru_cache
def get_settings() -> Settings:
    """Cached singleton — created once, reused for the process lifetime."""
    return Settings()  # type: ignore[call-arg]
