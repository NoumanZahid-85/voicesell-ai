"""
Redis cache + conversation memory (Upstash, rediss://).

Two responsibilities:
  1. FAQ cache — hash the normalized query; on a hit, skip the LLM entirely.
  2. Session memory — sliding-window history (last N turns) per session_id.

Everything degrades gracefully: if Redis is unreachable we log a warning and
fall through to the LLM rather than failing the request.
"""

from __future__ import annotations

import hashlib
import json
import logging
import re
from contextlib import asynccontextmanager
from typing import AsyncIterator

from app.core.config import get_settings

logger = logging.getLogger(__name__)

_client = None
_redis_enabled = True  # set to False if init fails

CACHE_TTL_SECONDS = 86400  # 24h
MEMORY_TTL_SECONDS = 7200  # 2h
PENDING_ORDER_TTL_SECONDS = 120  # 2 min window to confirm an order action


def get_redis():
    """Return the shared async Redis client, or None if unavailable."""
    global _client, _redis_enabled
    if _client is not None:
        return _client
    if not _redis_enabled:
        return None

    settings = get_settings()
    if not settings.redis_url:
        _redis_enabled = False
        return None

    try:
        import redis.asyncio as aioredis

        client_kwargs = dict(
            decode_responses=True,
            socket_connect_timeout=2,
            socket_timeout=2,
        )
        if settings.redis_url.startswith("rediss://"):
            # Only TLS connections use SSLConnection, which is the only
            # connection class that accepts ssl_cert_reqs. Passing it
            # unconditionally (even for plain redis://, e.g. Render's
            # internal Key Value URL) makes redis-py 8.x raise
            # "AbstractConnection.__init__() got an unexpected keyword
            # argument 'ssl_cert_reqs'" on first use — every request.
            client_kwargs["ssl_cert_reqs"] = None
        _client = aioredis.from_url(settings.redis_url, **client_kwargs)
    except Exception as exc:
        logger.warning("Redis init failed — cache/memory disabled: %s", exc)
        _redis_enabled = False
        _client = None
    return _client


@asynccontextmanager
async def safe_redis() -> AsyncIterator:
    """Yield the shared Redis client, or None when Redis is unavailable.

    The whole degrade-gracefully contract lives here: a missing client or a
    failed operation is logged and suppressed, so call sites only write the
    happy path. A getter that raises inside the block falls through and
    returns None; a writer that raises is silently swallowed.
    """
    client = get_redis()
    if client is None:
        yield None
        return
    try:
        yield client
    except Exception as exc:
        logger.warning("Redis operation failed: %s", exc)


# ── FAQ cache ────────────────────────────────────────────────────────


def normalize_query(query: str) -> str:
    """Canonical form: lowercase, strip punctuation, collapse whitespace."""
    stripped = re.sub(r"[^\w\s]", "", query.lower())
    return re.sub(r"\s+", " ", stripped).strip()


def _cache_key(query: str) -> str:
    digest = hashlib.sha256(normalize_query(query).encode("utf-8")).hexdigest()
    return f"faq:{digest}"


async def get_cached_answer(query: str) -> str | None:
    async with safe_redis() as r:
        if r is None:
            return None
        raw = await r.get(_cache_key(query))
        return str(raw) if raw is not None else None


async def set_cached_answer(query: str, answer: str, ttl_seconds: int | None = None) -> None:
    async with safe_redis() as r:
        if r is None:
            return
        ttl = ttl_seconds or get_settings().rag_cache_ttl_seconds
        await r.set(_cache_key(query), answer, ex=ttl)


# ── Conversation memory ─────────────────────────────────────────────


def _history_key(session_id: str) -> str:
    return f"session:{session_id}:history"


async def get_history(session_id: str) -> list[dict]:
    """Return recent turns as [{user, bot}, ...], oldest first."""
    async with safe_redis() as r:
        if r is None:
            return []
        raw = await r.lrange(_history_key(session_id), 0, -1)
        turns = [json.loads(item) for item in raw if item]
        return turns[-get_settings().rag_memory_turns :]
    # safe_redis() suppresses exceptions raised mid-block (logs a warning
    # and falls through here) — always return a list, never None, so
    # callers doing history[-8:] never crash the whole request.
    return []


async def add_turn(session_id: str, user_msg: str, bot_msg: str) -> None:
    """Append one turn and trim the list to the sliding window."""
    async with safe_redis() as r:
        if r is None:
            return
        key = _history_key(session_id)
        item = json.dumps({"user": user_msg, "bot": bot_msg})
        await r.rpush(key, item)
        await r.ltrim(key, -settings_window(), -1)
        await r.expire(key, MEMORY_TTL_SECONDS)


def settings_window() -> int:
    """Number of list entries to keep (2 per turn)."""
    return get_settings().rag_memory_turns * 2


# ── Pending order state (Confirmation Gate) ─────────────────────────


def pending_key(session_id: str) -> str:
    return f"pending_order:{session_id}"


async def set_pending_order(session_id: str, action: dict) -> None:
    """Stage an order action awaiting customer confirmation."""
    async with safe_redis() as r:
        if r is None:
            return
        await r.set(pending_key(session_id), json.dumps(action), ex=PENDING_ORDER_TTL_SECONDS)


async def get_pending_order(session_id: str) -> dict | None:
    async with safe_redis() as r:
        if r is None:
            return None
        raw = await r.get(pending_key(session_id))
        return json.loads(raw) if raw else None


async def clear_pending_order(session_id: str) -> None:
    async with safe_redis() as r:
        if r is not None:
            await r.delete(pending_key(session_id))
