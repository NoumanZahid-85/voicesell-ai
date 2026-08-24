"""
Custom Pipecat FrameProcessor that bridges STT transcripts → LangGraph agent → TTS.

Architecture decisions:
  - Subclasses FrameProcessor (Pipecat's unit-of-work abstraction).
  - Receives TranscriptionFrame from Deepgram STT when end-of-turn fires.
  - Invokes the stateful LangGraph agent (same logic as /api/v1/chat) via
    a shared AsyncSession — obtained from FastAPI's dependency factory.
  - Streams each sentence of the agent's reply down-pipeline as individual
    TextFrame objects.  Cartesia can start synthesizing the first sentence
    while the LLM generates the rest.
  - Latency tracing: emits structured log lines for each pipeline stage so
    we can build Grafana dashboards on them later (Phase 7).

Error strategy ("never breaks at 2AM"):
  - ANY exception in process_frame is caught, logged, and a safe fallback
    phrase is pushed downstream.  A single bad customer message must NOT
    crash the asyncio event loop.
  - Long-running agent invocations are bounded by an asyncio.wait_for()
    timeout (default 8s).  Timeout returns a graceful apology.
  - All async-safety: we never await inside a sync context — the method is
    fully async from frame receipt to reply dispatch.
"""

from __future__ import annotations

import asyncio
import logging
import time

try:
    from pipecat.frames.frames import (
        BotStartedSpeakingFrame,
        BotStoppedSpeakingFrame,
        EndFrame,
        Frame,
        LLMMessagesUpdateFrame,
        TextFrame,
        TranscriptionFrame,
        UserStoppedSpeakingFrame,
    )
    from pipecat.processors.frame_processor import FrameDirection, FrameProcessor
    from pipecat.transports.daily.transport import DailyOutputTransportMessageFrame
except ImportError:
    # Define simple dummy classes so uvicorn compiles on Windows without dependencies
    class FrameProcessor: pass
    class Frame: pass
    class TranscriptionFrame: pass
    class LLMMessagesUpdateFrame: pass
    class TextFrame: pass
    class BotStartedSpeakingFrame: pass
    class BotStoppedSpeakingFrame: pass
    class DailyOutputTransportMessageFrame: pass
    class FrameDirection:
        DOWNSTREAM = 1

from app.services import cache as cache_svc
from app.services.agent import (
    INTENT_ORDER,
    build_agent_graph,
    classify_intent,
    is_confirmation_utterance,
)

logger = logging.getLogger(__name__)

_FALLBACK_PHRASE = (
    "I'm sorry, I ran into a bit of trouble there. "
    "Could you say that again?"
)
# 25s: Groq free-tier LLM calls take up to 15s + RAG retrieval overhead.
# The old 8s value was shorter than Groq's own timeout, so any order
# would reliably fail in voice mode before the model even responded.
_AGENT_TIMEOUT_S = 25.0


class LangGraphProcessor(FrameProcessor):
    """
    Pipecat processor that runs the LangGraph RAG agent on each transcribed turn.

    Args:
        db_session: SQLAlchemy AsyncSession (request-scoped, provided by voice
                    endpoint via dependency injection).
        session_id: Unique ID for this conversation — used for Redis history.
    """

    def __init__(self, db_session, session_id: str) -> None:
        super().__init__()
        self.db_session = db_session
        self.session_id = session_id
        # Echo guard: while the bot's own voice is playing, the user's mic
        # (speaker leakage) re-captures it, VAD+STT transcribe it, and the
        # agent ends up answering itself with generic prompts. Transcripts
        # that arrive during bot speech are therefore discarded.
        self._bot_speaking = False
        # Build the LangGraph compiled graph once per voice session
        self._agent = build_agent_graph(db_session, session_id=session_id)

    async def process_frame(self, frame: Frame, direction: FrameDirection) -> None:
        """Route frames through the pipeline, intercepting transcripts."""
        await super().process_frame(frame, direction)

        if isinstance(frame, BotStartedSpeakingFrame):
            self._bot_speaking = True
        elif isinstance(frame, BotStoppedSpeakingFrame):
            self._bot_speaking = False
        elif isinstance(frame, TranscriptionFrame):
            if self._bot_speaking:
                logger.info(
                    "Echo guard: dropped transcript during bot speech | session=%s",
                    self.session_id,
                )
                return
            transcript = frame.text.strip()
            if not transcript:
                # Empty frame (noise) — do not invoke the agent
                return
            await self._send_caption("user", transcript)
            await self._handle_transcript(transcript)

        # Pass every other frame (and Bot* frames themselves) through
        await self.push_frame(frame, direction)

    async def _handle_transcript(self, text: str) -> None:
        """
        Invoke the LangGraph agent and stream reply sentences back as TextFrames.

        Flow:
          1. Check FAQ Redis cache — instant return on hits.
          2. Load conversation history from Redis.
          3. Run LangGraph with a timeout guard.
          4. Cache the answer + save turn to history.
          5. Split reply on sentence boundaries and push each sentence as a
             TextFrame so TTS starts synthesising the first sentence early.
        """
        t0 = time.perf_counter()
        logger.info("VoiceAgent received transcript: %r (session=%s)", text[:80], self.session_id)

        try:
            # 1) FAQ cache hit — fast path. Skipped for transactional
            # messages (yes/no gate answers AND order intents): their
            # replies are conversation-specific, and serving a cached quote
            # would skip staging the order entirely.
            stateful = (
                is_confirmation_utterance(text)
                or classify_intent(text) == INTENT_ORDER
            )
            if not stateful:
                cached = await cache_svc.get_cached_answer(text)
                if cached:
                    logger.info("Cache HIT for session=%s", self.session_id)
                    await self._push_reply(cached)
                    return

            # 2) Conversation memory
            history = await cache_svc.get_history(self.session_id)

            # 3) LangGraph agent — bounded by timeout
            state = await asyncio.wait_for(
                self._agent.ainvoke(
                    {
                        "user_message":   text,
                        "session_id":     self.session_id,
                        "history":        history,
                        "intent":         "",
                        "context":        "",
                        "chunks":         [],
                        "order_result":   None,
                        "reply":          "",
                        # Phase 5 upsell guard fields — required by AgentState
                        "upsell_done":    False,
                        "upsell_product": None,
                    }
                ),
                timeout=_AGENT_TIMEOUT_S,
            )
            reply: str = state.get("reply", "").strip() or _FALLBACK_PHRASE

            # 4) Persist cache + history (best-effort, non-blocking)
            if not stateful:
                asyncio.create_task(cache_svc.set_cached_answer(text, reply))
            asyncio.create_task(cache_svc.add_turn(self.session_id, text, reply))

            elapsed = (time.perf_counter() - t0) * 1000
            logger.info(
                "Agent reply ready in %.1f ms | session=%s | intent=%s",
                elapsed, self.session_id, state.get("intent", "?"),
            )

            # 5) Stream sentences to TTS
            await self._push_reply(reply)

        except asyncio.TimeoutError:
            logger.warning(
                "Agent timeout after %.1fs | session=%s",
                _AGENT_TIMEOUT_S, self.session_id,
            )
            await self._push_reply(
                "I'm taking a little longer than usual. Could you repeat that?"
            )
        except Exception as exc:  # noqa: BLE001
            logger.exception("Unexpected agent error for session=%s: %s", self.session_id, exc)
            await self._push_reply(_FALLBACK_PHRASE)

    async def _push_reply(self, text: str) -> None:
        """
        Break reply into sentences and push each as a TextFrame.

        Sentence-level streaming lets Deepgram Aura TTS start synthesis
        immediately while the LLM generates subsequent sentences.
        """
        sentences = _split_sentences(text)
        for sentence in sentences:
            if sentence:
                await self._send_caption("agent", sentence)
                await self.push_frame(TextFrame(text=sentence), FrameDirection.DOWNSTREAM)

        # Signal that we're done this turn — needed for barge-in reset state.
        # LLMMessagesUpdateFrame (pipecat ≥1.5) resets the context without
        # triggering a new LLM run.
        await self.push_frame(
            LLMMessagesUpdateFrame(messages=[], run_llm=False),
            FrameDirection.DOWNSTREAM,
        )

    async def _send_caption(self, role: str, text: str) -> None:
        """Broadcast a live-caption event to the browser over Daily's data channel.

        The frontend (a headless daily-js call object) listens for
        "app-message" and renders this as a real-time subtitle line — so the
        user sees their own words transcribed, and the agent's reply typed
        out, while audio is still playing/being captured.
        """
        try:
            await self.push_frame(
                DailyOutputTransportMessageFrame(message={"role": role, "text": text}),
                FrameDirection.DOWNSTREAM,
            )
        except Exception:  # noqa: BLE001 — captions are best-effort, never fatal
            logger.debug("Caption broadcast failed (non-fatal)", exc_info=True)


def _split_sentences(text: str) -> list[str]:
    """
    Naively split text on sentence boundaries.

    We prefer naive splits over NLTK/spacy to avoid heavy dependencies in
    the voice critical path.  The result is good enough for TTS chunking.
    """
    import re
    # Split on '. ', '! ', '? ' but keep the punctuation with the sentence.
    parts = re.split(r"(?<=[.!?])\s+", text)
    sentences = [p.strip() for p in parts if p.strip()]
    # Groq's Orpheus TTS rejects inputs over 200 characters with HTTP 400 —
    # an unguarded long sentence would silently drop that whole reply.
    # Further split oversized chunks at word boundaries.
    return [
        chunk
        for sentence in sentences
        for chunk in _split_long_chunk(sentence)
    ]


_MAX_TTS_CHARS = 190  # safety margin under Orpheus's 200-char limit


def _split_long_chunk(sentence: str) -> list[str]:
    """Split a single sentence into <=_MAX_TTS_CHARS pieces at word boundaries."""
    if len(sentence) <= _MAX_TTS_CHARS:
        return [sentence]
    chunks: list[str] = []
    remaining = sentence
    while len(remaining) > _MAX_TTS_CHARS:
        cut = remaining.rfind(" ", 0, _MAX_TTS_CHARS)
        if cut <= 0:
            cut = _MAX_TTS_CHARS  # pathological no-space case — hard split
        chunks.append(remaining[:cut].strip())
        remaining = remaining[cut:].strip()
    if remaining:
        chunks.append(remaining)
    return [c for c in chunks if c]
