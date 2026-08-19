"""Tests for the Confirmation Gate pure decision core and the Redis accessor.

The gate core (decide_gate_action) is pure — no I/O — so these tests need
neither Redis nor an LLM. The safe_redis contract tests prove that a
missing client or a failing operation degrades to None instead of raising.
"""

import pytest

from app.services import cache
from app.services.agent import decide_gate_action


# ── Confirmation Gate core ───────────────────────────────────────────

class TestDecideGateAction:
    def test_nothing_staged_never_engages(self):
        assert decide_gate_action(None, "yes please") == "stall"

    def test_affirmation_confirms(self):
        pending = {"action": "create", "lines": [{"name": "Wireless Mouse", "qty": 2}]}
        for utterance in ("yes", "Yes, place it", "go ahead", "sure, do it", "confirm please"):
            assert decide_gate_action(pending, utterance) == "confirm", utterance

    def test_negation_denies(self):
        pending = {"action": "cancel", "order_id": "ord-1"}
        for utterance in ("no", "No, don't", "cancel it", "never mind", "forget it"):
            assert decide_gate_action(pending, utterance) == "deny", utterance

    def test_ambiguous_utterance_stalls(self):
        pending = {"action": "create"}
        for utterance in ("what products do you have?", "tell me more", "maybe", "hello"):
            assert decide_gate_action(pending, utterance) == "stall", utterance

    def test_affirmation_wins_over_negation_substring(self):
        # "no thanks, yes do it" — the actionable signal is the affirmation.
        pending = {"action": "create"}
        assert decide_gate_action(pending, "no thanks, yes do it") == "confirm"


# ── safe_redis degrade contract ──────────────────────────────────────

class TestSafeRedisDegrade:
    @pytest.mark.asyncio
    async def test_missing_client_yields_none(self, monkeypatch):
        monkeypatch.setattr(cache, "get_redis", lambda: None)
        assert await cache.get_cached_answer("hello") is None
        assert await cache.get_history("s-1") == []
        assert await cache.get_pending_order("s-1") is None
        await cache.set_cached_answer("hello", "world")
        await cache.add_turn("s-1", "u", "b")
        await cache.set_pending_order("s-1", {"action": "create"})
        await cache.clear_pending_order("s-1")

    @pytest.mark.asyncio
    async def test_failing_operation_is_suppressed(self, monkeypatch):
        class ExplodingClient:
            async def get(self, *a, **k):
                raise ConnectionError("redis down")
            async def set(self, *a, **k):
                raise ConnectionError("redis down")

        monkeypatch.setattr(cache, "get_redis", lambda: ExplodingClient())
        assert await cache.get_cached_answer("hello") is None
        await cache.set_cached_answer("hello", "world")  # must not raise

    @pytest.mark.asyncio
    async def test_happy_path_round_trip(self, monkeypatch):
        store: dict[str, str] = {}

        class FakeClient:
            async def get(self, key):
                return store.get(key)
            async def set(self, key, value, **kwargs):
                store[key] = value
            async def delete(self, key):
                store.pop(key, None)

        monkeypatch.setattr(cache, "get_redis", lambda: FakeClient())
        await cache.set_cached_answer("   Hello, World!   ", "answer")
        assert await cache.get_cached_answer("hello world") == "answer"
        await cache.set_pending_order("s-9", {"action": "create"})
        assert await cache.get_pending_order("s-9") == {"action": "create"}
        await cache.clear_pending_order("s-9")
        assert await cache.get_pending_order("s-9") is None
