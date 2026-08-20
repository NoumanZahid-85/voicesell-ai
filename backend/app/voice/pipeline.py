"""
Pipecat voice pipeline factory — Phase 3 final: Deepgram for both STT and TTS.

Why Deepgram for TTS:
  - Cartesia free tier is 500 chars/month — exhausted immediately in testing.
  - Deepgram's $200 free credit covers ~2.5 million chars of TTS (Aura model).
  - Single API key for both STT + TTS = simpler secrets management.
  - Deepgram Aura TTS latency (TTFA) is typically <200ms — well inside our
    500ms total budget.

Pipeline shape:
  DailyTransport.input()  ← WebRTC audio in from browser
      ↓ audio frames
  DeepgramSTTService       ← nova-3 streaming, endpointing=300ms
      ↓ TranscriptionFrame (final transcript)
  SileroVADAnalyzer        ← offline VAD fallback (no API key)
  LangGraphProcessor       ← our custom bridge: text → agent → text
      ↓ TextFrame(s) — one per sentence for early TTS start
  DeepgramTTSService       ← Aura model, zero-shot streaming audio
      ↓ audio frames
  DailyTransport.output()  ← WebRTC audio out to browser

Resilience notes:
  - Barge-in (allow_interruptions=True) is enabled — customer can speak
    mid-response and Pipecat cancels in-flight TTS immediately.
  - Silence timeout is handled by Deepgram endpointing (300ms), not our code.
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
    from pipecat.services.deepgram.stt import DeepgramSTTService
    from pipecat.services.deepgram.tts import DeepgramTTSService
    from pipecat.transports.daily.transport import DailyParams, DailyTransport
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
    class DeepgramSTTService: pass
    class DeepgramTTSService: pass
    class DailyParams: pass
    class DailyTransport: pass
    class LangGraphProcessor: pass

from app.core.config import get_settings

logger = logging.getLogger(__name__)

# Deepgram Aura voice — male, natural US English, clear diction.
# Alternatives (all on $200 credit): aura-asteria-en (female), aura-zeus-en (male).
_DEEPGRAM_VOICE = "aura-arcas-en"

_GREETING = (
    "Hello! I'm CALLIOPE, your AI shopping assistant. "
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
            transcription_enabled=False,   # Deepgram handles STT, not Daily
            vad_enabled=True,
            vad_analyzer=SileroVADAnalyzer(),  # offline VAD — no API cost
            vad_audio_passthrough=True,
        ),
    )

    # ── Deepgram STT — nova-3 streaming ─────────────────────────────
    # nova-3 is the fastest + most accurate model Deepgram offers.
    # endpointing=300 → 300ms silence triggers end-of-utterance.
    # interim_results=False → no partial transcripts hitting the agent.
    stt = DeepgramSTTService(
        api_key=settings.deepgram_api_key,
        settings=DeepgramSTTService.Settings(
            model="nova-3",
            language="en-US",
            smart_format=True,
            punctuate=True,
            endpointing=300,
            interim_results=False,
            utterance_end_ms=1000,
        ),
    )

    # ── LangGraph agent bridge ────────────────────────────────────────
    agent = LangGraphProcessor(
        db_session=db_session,
        session_id=session_id,
    )

    # ── Deepgram Aura TTS ─────────────────────────────────────────────
    # Aura-arcas-en: clear US male voice, ~$0.0135 / 1000 chars.
    # With $200 credit ≈ 14.8 million chars ≈ millions of turns.
    # sample_rate=24000 matches Daily's preferred output format.
    tts = DeepgramTTSService(
        api_key=settings.deepgram_api_key,
        voice=_DEEPGRAM_VOICE,
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
            allow_interruptions=True,    # barge-in enabled
            enable_metrics=True,
            enable_usage_metrics=True,
        ),
    )

    # ── Event: first participant joins ────────────────────────────────
    @transport.event_handler("on_first_participant_joined")
    async def on_joined(transport, participant):  # noqa: ARG001
        logger.info("Participant joined session=%s id=%s", session_id, participant.get("id"))
        # Push greeting directly to TTS — bypasses the agent for instant hello.
        await task.queue_frames([TextFrame(text=_GREETING)])

    # ── Event: participant leaves ─────────────────────────────────────
    @transport.event_handler("on_participant_left")
    async def on_left(transport, participant, reason):  # noqa: ARG001
        logger.info("Participant left session=%s reason=%s", session_id, reason)
        await task.cancel()

    @transport.event_handler("on_call_state_updated")
    async def on_state(transport, state):  # noqa: ARG001
        logger.debug("Daily call state=%s session=%s", state, session_id)

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
