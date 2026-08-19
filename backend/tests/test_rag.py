"""Unit tests for RAG building blocks (no external services required)."""

from __future__ import annotations

import uuid

import pytest
from pydantic import ValidationError

from app.schemas.chat import ChatRequest
from app.services.agent import classify_intent
from app.services.cache import _cache_key, normalize_query
from app.services.rag import RAGService, RetrievedChunk, _extract_keywords, extract_max_price


def make_chunk(**overrides) -> RetrievedChunk:
    defaults = {
        "product_id": uuid.uuid4(),
        "name": "Computer Accessories Item abc",
        "category": "Computer Accessories",
        "price": 25.0,
        "stock_quantity": 10,
        "description": "A computer accessories product.",
        "score": 0.81,
    }
    defaults.update(overrides)
    return RetrievedChunk(**defaults)


# ── Chunking ─────────────────────────────────────────────────────────


def test_chunk_short_text_stays_whole():
    assert RAGService.format_context([]) == ""
    from scripts.embed_products import chunk_text

    text = "Short description"
    assert chunk_text(text) == [text]


def test_chunk_long_text_overlaps():
    from scripts.embed_products import chunk_text

    text = "word " * 200  # 1000 chars
    chunks = chunk_text(text, chunk_size=300, overlap=50)
    assert len(chunks) > 1
    # each chunk <= chunk_size
    assert all(len(c) <= 300 for c in chunks)
    # consecutive chunks overlap by ~50 chars
    assert chunks[1].startswith(chunks[0][-50:])


def test_chunk_empty_text():
    from scripts.embed_products import chunk_text

    assert chunk_text("  ") == []


# ── Query normalization / cache key ──────────────────────────────────


def test_normalize_query():
    assert normalize_query("  What  ELECTRONICS?  ") == "what electronics"
    assert normalize_query("Under $50!!") == "under 50"


def test_cache_key_deterministic():
    assert _cache_key("What is the price?") == _cache_key("what is the price")
    assert _cache_key("a") != _cache_key("b")


def test_extract_keywords():
    kw = _extract_keywords("Do you sell computer accessories under $50?")
    assert "computer" in kw
    assert "accessories" in kw
    assert "do" not in kw  # stopword removed


def test_extract_max_price():
    assert extract_max_price("computer accessories under $50") == 50.0
    assert extract_max_price("items under fifty dollars") == 50.0
    assert extract_max_price("under one hundred dollars") == 100.0
    assert extract_max_price("below usd 30 please") == 30.0
    assert extract_max_price("what do you sell?") is None


# ── Intent classification ────────────────────────────────────────────


def test_intent_order():
    assert classify_intent("I want to order 5 keyboards") == "order_action"
    assert classify_intent("Can I order the mouse?") == "order_action"
    # plain 'buy' in a product question must NOT trigger order intent
    assert classify_intent("What can I buy for home decor?") == "product_question"


def test_intent_general_greeting():
    assert classify_intent("Hello there") == "general_chat"
    assert classify_intent("thank you so much") == "general_chat"


def test_intent_product_question():
    assert classify_intent("Do you sell telephony products?") == "product_question"


# ── Context formatting ───────────────────────────────────────────────


def test_format_context_lists_products():
    chunk = make_chunk()
    ctx = RAGService.format_context([chunk])
    assert "Computer Accessories Item abc" in ctx
    assert "$25.00" in ctx
    assert "in stock" in ctx


def test_format_context_marks_out_of_stock():
    chunk = make_chunk(stock_quantity=0)
    ctx = RAGService.format_context([chunk])
    assert "out of stock" in ctx


# ── Chat schema validation ───────────────────────────────────────────


def test_chat_request_requires_message_and_session():
    with pytest.raises(ValidationError):
        ChatRequest(message="", session_id="s1")
    with pytest.raises(ValidationError):
        ChatRequest(message="hi", session_id="")
    req = ChatRequest(message="What do you sell?", session_id="test-1")
    assert req.message == "What do you sell?"
