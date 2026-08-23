"""
Pipecat voice pipeline factory — Groq for both STT and TTS.

Why Groq (switched from Deepgram):
  - The Deepgram account/dashboard became inaccessible (locked out, no
    support response) — can't rotate the key or manage billing, so we
    moved off it entirely rather than block on a support ticket.
  - Groq's Whisper (whisper-large-v3-turbo) speech-to-text free tier
    covers 2,000 requests/day — plenty for a demo/internship project.
  - Groq's Orpheus TTS (canopylabs/orpheus-v1-english) has a
    free-tier-friendly daily allowance and reuses the SAME GROQ_API_KEY
    already configured for the LLM — one fewer secret to manage.
  - IMPORTANT one-time setup: the Orpheus English model requires
    accepting its model terms once at
    https://console.groq.com/playground?model=canopylabs/orpheus-v1-english
    before the API key can use it (otherwise TTS calls 403).

Pipeline shape:
  DailyTransport.input()  ← WebRTC audio in from browser
      ↓ audio frames
  SileroVADAnalyzer        ← offline VAD — detects end-of-utterance,
                              required since Groq STT is segmented
                              (batch), not a live streaming socket
  GroqSTTService            ← whisper-large-v3-turbo, one HTTP call per
                              VAD-detected utterance
      ↓ TranscriptionFrame (final transcript)
  LangGraphProcessor        ← our custom bridge: text → agent → text
      ↓ TextFrame(s) — one per sentence for early TTS start
  GroqTTSService             ← Orpheus v1 English, voice "autumn"
      ↓ audio frames
  DailyTransport.output()  ← WebRTC audio out to browser

Resilience notes:
  - Barge-in (allow_interruptions=True) is enabled — customer can speak
    mid-response and Pipecat cancels in-flight TTS immediately.
  - Utterance boundaries are decided by Silero VAD (offline, no API
    cost) since Groq STT is request/response, not a live socket.
  - Pipeline exceptions are caught in run() — the exception percolates to the
    asyncio.Task which triggers the session registry cleanup callback.
  - DB session is passed in from the API layer to reuse the connection pool.
"""

from __future__ import annotations

import asyncio
import logging
import time

try:
    from pipecat.audio.vad.silero import SileroVADAnalyzer
    from pipecat.frames.frames import TextFrame
    from pipecat.pipeline.pipeline import Pipeline
    from pipecat.pipeline.runner import PipelineRunner
    from pipecat.pipeline.task import PipelineParams, PipelineTask
    from pipecat.services.groq.stt import GroqSTTService
    from pipecat.services.groq.tts import GroqTTSService
    from pipecat.transports.daily.transport import (
        DailyOutputTransportMessageFrame,
        DailyParams,
        DailyTransport,
    )
    from app.voice.processor import LangGraphProcessor
    VOICE_SUPPORTED = True
except ImportError as e:
    VOICE_SUPPORTED = False
    VOICE_IMPORT_ERROR = str(e)
    # Define simple dummy classes so uvicorn compiles
    class SileroVADAnalyzer: pass
    class TextFrame: pass
    class Pipeline: pass
    class PipelineRunner: pass
    class PipelineParams: pass
    class PipelineTask: pass
    class GroqSTTService: pass
    class GroqTTSService: pass
    class DailyOutputTransportMessageFrame: pass
    class DailyParams: pass
    class DailyTransport: pass
    class LangGraphProcessor: pass

from app.core.config import get_settings

logger = logging.getLogger(__name__)

# Primary TTS: Orpheus v1 English via Groq.
# playai-tts was DECOMMISSIONED by Groq (API now returns 400
# "model_decommissioned") — every synthesis failed, so the bot joined rooms
# but could never speak. Orpheus is Groq's only remaining English TTS.
# IMPORTANT one-time setup: this model requires accepting its terms once at
# https://console.groq.com/playground?model=canopylabs/orpheus-v1-english
# (org admin action) — otherwise the API returns 400 model_terms_required.
_GROQ_TTS_MODEL = "canopylabs/orpheus-v1-english"
_GROQ_TTS_VOICE = "autumn"

_GREETING = (
    "Hello! I'm CALLIOPE, your shopping assistant. "
    "Ask me anything about our products, prices, or to place an order."
)


async def build_and_run_pipeline(
    room_url: str,
    bot_token: str,
    session_id: str,
    db_session,
) -> None:
    if not VOICE_SUPPORTED:
        raise RuntimeError(
            "Voice pipeline failed to initialize — a required import is "
            f"missing or broken on the server. Details: {VOICE_IMPORT_ERROR}"
        )

    settings = get_settings()
    t_start = time.perf_counter()

    # ── Daily WebRTC transport ────────────────────────────────────────
    transport = DailyTransport(
        room_url=room_url,
        token=bot_token,
        bot_name="CALLIOPE",
        params=DailyParams(
            audio_in_enabled=True,
            audio_out_enabled=True,
            camera_out_enabled=False,
            transcription_enabled=False,   # Groq handles STT, not Daily
            vad_enabled=True,
            vad_analyzer=SileroVADAnalyzer(),  # offline VAD — segments audio for Groq STT
            vad_audio_passthrough=True,
        ),
    )

    # ── Groq STT — Whisper large-v3-turbo ────────────────────────────
    # Segmented (request/response), not a streaming socket — Silero VAD
    # above decides utterance boundaries and hands Groq one clip at a time.
    stt = GroqSTTService(
        api_key=settings.groq_api_key,
        settings=GroqSTTService.Settings(
            model="whisper-large-v3-turbo",
        ),
    )

    # ── LangGraph agent bridge ────────────────────────────────────────
    agent = LangGraphProcessor(
        db_session=db_session,
        session_id=session_id,
    )

    # ── Groq TTS (Orpheus v1 English) ─────────────────────────────────
    # Orpheus outputs a fixed 48 kHz WAV stream (same as the old PlayAI),
    # so audio_out_sample_rate=48000 below remains correct.
    tts = GroqTTSService(
        api_key=settings.groq_api_key,
        settings=GroqTTSService.Settings(
            model=_GROQ_TTS_MODEL,
            voice=_GROQ_TTS_VOICE,
        ),
    )

    # ── Assemble pipeline ─────────────────────────────────────────────
    pipeline = Pipeline(
        [
            transport.input(),
            stt,
            agent,
            tts,
            transport.output(),
        ]
    )

    task = PipelineTask(
        pipeline,
        params=PipelineParams(
            allow_interruptions=True,          # barge-in enabled
            enable_metrics=True,
            enable_usage_metrics=True,
            # Groq TTS (PlayAI / Orpheus) outputs a fixed 48 kHz stream.
            # Daily's default output is 16 kHz — the mismatch causes the
            # bot to join the room but produce no audible speech (frames
            # are resampled to silence or dropped entirely).
            audio_out_sample_rate=48000,
            # Silero VAD and Groq Whisper STT both work best at 16 kHz.
            audio_in_sample_rate=16000,
        ),
    )

    # ── Event: first participant joins ────────────────────────────────
    @transport.event_handler("on_first_participant_joined")
    async def on_joined(transport, participant):  # noqa: ARG001
        logger.info("Participant joined session=%s id=%s", session_id, participant.get("id"))
        from app.voice import session_registry
        session_registry.record_event(session_id, "participant_joined")
        # Caption the greeting so the customer SEES the hello even if the
        # audio output path ever fails — silence should never look like a
        # dead connection.
        await task.queue_frames(
            [
                DailyOutputTransportMessageFrame(
                    message={"role": "agent", "text": _GREETING}
                ),
                TextFrame(text=_GREETING),
            ]
        )

    # ── Event: participant leaves ─────────────────────────────────────
    @transport.event_handler("on_participant_left")
    async def on_left(transport, participant, reason):  # noqa: ARG001
        logger.info("Participant left session=%s reason=%s", session_id, reason)
        from app.voice import session_registry
        session_registry.record_event(session_id, "participant_left", str(reason))
        await task.cancel()

    @transport.event_handler("on_call_state_updated")
    async def on_state(transport, state):  # noqa: ARG001
        # INFO + event log: whether the BOT itself joined the room is the
        # single most important external visibility signal for a silent bot.
        logger.info("Daily call state=%s session=%s", state, session_id)
        from app.voice import session_registry as _reg
        _reg.record_event(session_id, f"call_{state}")

    # ── Run ───────────────────────────────────────────────────────────
    try:
        runner = PipelineRunner()
        await runner.run(task)
    except asyncio.CancelledError:
        logger.info("Pipeline cancelled cleanly session=%s", session_id)
    except Exception as exc:
        logger.exception(
            "Pipeline crashed session=%s after %.1fs: %s",
            session_id, time.perf_counter() - t_start, exc,
        )
        raise
    finally:
        logger.info(
            "Pipeline done session=%s duration=%.1fs",
            session_id, time.perf_counter() - t_start,
        )
