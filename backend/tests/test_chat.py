"""
Tests for the /api/v1/chat endpoint.

The LangGraph agent and Redis are monkeypatched so tests run without an LLM,
vector DB, or network.
"""

from __future__ import annotations

import pytest
from httpx import ASGITransport, AsyncClient

from app.main import app

SAMPLE_PRODUCT_ID = "11111111-1111-1111-1111-111111111111"


class FakeGraph:
    """Minimal stand-in for a compiled LangGraph."""

    def __init__(self, reply: str, chunks: list | None = None) -> None:
        self.reply = reply
        self.chunks = chunks or []

    async def ainvoke(self, state: dict) -> dict:
        state["reply"] = self.reply
        state["chunks"] = self.chunks
        return state


@pytest.fixture(autouse=True)
def _disable_external(monkeypatch):
    """Disable Redis + LLM/agent external calls for all chat tests."""
    import app.services.cache as cache_mod
    from app.api import chat as chat_mod

    monkeypatch.setattr(cache_mod, "get_redis", lambda: None)
    monkeypatch.setattr(chat_mod, "build_agent_graph", lambda db, session_id="": FakeGraph("I found some products."))


@pytest.mark.asyncio
async def test_chat_returns_reply_and_sources(monkeypatch):
    import app.api.chat as chat_mod

    monkeypatch.setattr(
        chat_mod,
        "build_agent_graph",
        lambda db, session_id="": FakeGraph(
            "The computer accessories are in stock.",
            [
                {
                    "product_id": SAMPLE_PRODUCT_ID,
                    "name": "Computer Accessories Item abc",
                    "price": 25.0,
                    "score": 0.81,
                }
            ],
        ),
    )

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.post(
            "/api/v1/chat",
            json={"message": "What computer accessories do you have?", "session_id": "test-1"},
        )

    assert resp.status_code == 200
    data = resp.json()
    assert data["reply"] == "The computer accessories are in stock."
    assert len(data["sources"]) == 1
    assert data["sources"][0]["product_id"] == SAMPLE_PRODUCT_ID
    assert data["cached"] is False


@pytest.mark.asyncio
async def test_chat_returns_400_for_empty_message():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.post("/api/v1/chat", json={"message": "", "session_id": "test-1"})
    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_chat_missing_session_id():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.post("/api/v1/chat", json={"message": "hello"})
    assert resp.status_code == 422
