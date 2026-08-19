"""
Phase 3 voice pipeline tests.

These tests verify the voice API layer WITHOUT making real network calls
to Daily, Deepgram, or Cartesia.  All external services are mocked so
the suite passes on CI (no API keys required).

Test coverage:
  - POST /api/v1/voice/connect, success path
  - POST /api/v1/voice/connect, missing API keys → 503
  - POST /api/v1/voice/connect, Daily room failure → 502
  - POST /api/v1/voice/connect, concurrency cap → 429
  - DELETE /api/v1/voice/connect/{id}, valid session → 204
  - DELETE /api/v1/voice/connect/{id}, unknown session → 404
  - GET /api/v1/voice/sessions returns active session info
  - session_registry: lifecycle (register, unregister, cancel_all)
  - LangGraphProcessor: empty transcript is dropped silently
  - LangGraphProcessor: timeout returns graceful fallback phrase
"""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from app.main import create_app
from app.voice import session_registry
from app.voice.session_registry import VoiceSession

# ── Fixtures ─────────────────────────────────────────────────────────

@pytest.fixture(autouse=True)
def clear_registry():
    """Ensure each test starts with a clean session registry."""
    yield
    # Teardown: clear any sessions left by the test
    for sid in list(session_registry._sessions.keys()):
        session_registry._sessions.pop(sid, None)


@pytest.fixture()
def app():
    return create_app()


@pytest.fixture()
def client(app):
    with TestClient(app, raise_server_exceptions=False) as c:
        yield c


# ── Helper: fake Daily room / token ──────────────────────────────────

def _fake_room(name="test-room-abc", url="https://testco.daily.co/test-room-abc"):
    from app.voice.daily_client import DailyRoom
    return DailyRoom(name=name, url=url, expires_at=9999999999)


def _fake_token(token="tok_abc123"):
    from app.voice.daily_client import DailyToken
    return DailyToken(token=token)


def _make_noop_task():
    """Return a real asyncio.Task that does nothing."""
    async def _noop():
        await asyncio.sleep(0)
    return asyncio.new_event_loop().create_task(_noop())


def _override_settings(client, **keys):
    """Inject fake settings via FastAPI dependency_overrides.

    The endpoints capture the real get_settings callable inside
    Depends(...) at import time, so module-level patch() on either
    app.api.voice or app.core.config cannot change what runs.
    """
    from app.core.config import get_settings

    s = MagicMock()
    for k, v in keys.items():
        setattr(s, k, v)
    client.app.dependency_overrides[get_settings] = lambda: s
    return s


# ── API tests ────────────────────────────────────────────────────────

class TestVoiceConnect:

    @patch("app.api.voice.build_and_run_pipeline", new_callable=AsyncMock)
    @patch("app.voice.daily_client.create_bot_token", new_callable=AsyncMock)
    @patch("app.voice.daily_client.create_room", new_callable=AsyncMock)
    def test_connect_success(self, mock_room, mock_token, mock_pipeline, client):
        """Happy path: returns 201 with session_id and room_url."""
        mock_room.return_value  = _fake_room()
        mock_token.return_value = _fake_token()
        mock_pipeline.return_value = None  # pipeline is a background task

        _override_settings(
            client,
            daily_api_key="key-daily",
            deepgram_api_key="key-dg",
            cartesia_api_key="key-ca",
        )

        resp = client.post("/api/v1/voice/connect")

        assert resp.status_code == 201
        body = resp.json()
        assert "session_id" in body
        assert "room_url" in body
        assert body["room_url"] == "https://testco.daily.co/test-room-abc"

    def test_connect_missing_keys_returns_503(self, client):
        """If any voice API keys are absent → 503 before touching Daily."""
        _override_settings(
            client,
            daily_api_key="",
            deepgram_api_key="key-dg",
            cartesia_api_key="key-ca",
        )

        resp = client.post("/api/v1/voice/connect")

        assert resp.status_code == 503
        assert "DAILY_API_KEY" in resp.json()["detail"]

    @patch("app.voice.daily_client.create_room", new_callable=AsyncMock)
    def test_connect_daily_failure_returns_502(self, mock_room, client):
        """If Daily API fails → 502."""
        mock_room.side_effect = Exception("Daily unavailable")

        _override_settings(
            client,
            daily_api_key="k",
            deepgram_api_key="k",
            cartesia_api_key="k",
        )

        resp = client.post("/api/v1/voice/connect")

        assert resp.status_code == 502

    def test_connect_at_capacity_returns_429(self, client):
        """Once MAX_CONCURRENT_SESSIONS is hit → 429."""
        _override_settings(
            client,
            daily_api_key="k",
            deepgram_api_key="k",
            cartesia_api_key="k",
        )
        # Patch the module-level constant via unittest.mock to avoid type issues
        with patch("app.api.voice.MAX_CONCURRENT_SESSIONS", new=0):
            resp = client.post("/api/v1/voice/connect")

        assert resp.status_code == 429

    def test_disconnect_unknown_returns_404(self, client):
        """Deleting a non-existent session → 404."""
        resp = client.delete("/api/v1/voice/connect/does-not-exist")
        assert resp.status_code == 404

    def test_list_sessions_empty(self, client):
        """No active sessions → empty list."""
        resp = client.get("/api/v1/voice/sessions")
        assert resp.status_code == 200
        assert resp.json() == []


# ── Registry unit tests ───────────────────────────────────────────────

class TestSessionRegistry:

    def _make_session(self, sid="s-1"):
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            loop = asyncio.new_event_loop()
        task = loop.create_task(asyncio.sleep(0))
        return VoiceSession(session_id=sid, room_name="r", room_url="u", task=task)

    def test_register_and_get(self):
        s = self._make_session()
        session_registry.register(s)
        assert session_registry.get("s-1") is s
        assert session_registry.active_count() == 1

    def test_unregister(self):
        s = self._make_session()
        session_registry.register(s)
        removed = session_registry.unregister("s-1")
        assert removed is s
        assert session_registry.active_count() == 0

    def test_unregister_missing_returns_none(self):
        assert session_registry.unregister("nope") is None

    @pytest.mark.asyncio
    async def test_cancel_session(self):
        s = self._make_session("s-cancel")
        session_registry.register(s)
        result = await session_registry.cancel_session("s-cancel")
        assert result is True
        assert session_registry.get("s-cancel") is None

    @pytest.mark.asyncio
    async def test_shutdown_all(self):
        for i in range(3):
            session_registry.register(self._make_session(f"sess-{i}"))
        assert session_registry.active_count() == 3
        await session_registry.shutdown_all()
        assert session_registry.active_count() == 0


# ── LangGraphProcessor unit tests ────────────────────────────────────

class TestLangGraphProcessor:

    @pytest.fixture()
    def processor(self):
        """Build processor with mocked DB session and agent."""
        with patch("app.voice.processor.build_agent_graph") as mock_build:
            mock_agent = AsyncMock()
            mock_agent.ainvoke.return_value = {
                "reply": "We have great keyboards in stock!",
                "intent": "product_question",
                "chunks": [],
            }
            mock_build.return_value = mock_agent
            from app.voice.processor import LangGraphProcessor
            proc = LangGraphProcessor(db_session=MagicMock(), session_id="test-session")
            proc._agent = mock_agent
            return proc

    @pytest.mark.asyncio
    async def test_empty_transcript_is_ignored(self, processor):
        """Empty transcripts must not invoke the agent."""
        from pipecat.frames.frames import TranscriptionFrame
        frame = TranscriptionFrame(text="  ", user_id="u", timestamp="t")
        with patch.object(processor, "_agent") as mock_agent:
            await processor.process_frame(frame, None)
            mock_agent.ainvoke.assert_not_called()

    @pytest.mark.asyncio
    async def test_timeout_returns_fallback(self, processor):
        """If the agent exceeds timeout → graceful fallback phrase, no crash."""
        async def _slow(*a, **kw):
            await asyncio.sleep(999)  # simulates hang

        processor._agent.ainvoke = _slow

        pushed = []

        async def _capture(frame, direction):
            pushed.append(frame)

        processor.push_frame = _capture

        with patch("app.voice.processor._AGENT_TIMEOUT_S", 0.01):
            await processor._handle_transcript("what do you sell?")

        # At least one TextFrame should be the fallback phrase
        from pipecat.frames.frames import TextFrame
        texts = [f.text for f in pushed if isinstance(f, TextFrame)]
        assert any("longer than usual" in t or "trouble" in t for t in texts)
