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
    from pipecat.frames.frames import (
        ErrorFrame,
        TextFrame,
        TranscriptionFrame,
        UserStartedSpeakingFrame,
    )
    from pipecat.pipeline.pipeline import Pipeline
    from pipecat.pipeline.runner import PipelineRunner
    from pipecat.pipeline.task import PipelineParams, PipelineTask
    from pipecat.processors.frame_processor import FrameDirection, FrameProcessor
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
    class ErrorFrame: pass
    class TextFrame: pass
    class TranscriptionFrame: pass
    class UserStartedSpeakingFrame: pass
    class Pipeline: pass
    class PipelineRunner: pass
    class PipelineParams: pass
    class PipelineTask: pass
    class FrameProcessor: pass
    class FrameDirection:
        DOWNSTREAM = 1
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


class RobustGroqTTSService(GroqTTSService):
    """GroqTTSService with whole-response WAV parsing.

    pipecat 1.7.0's run_tts wraps EVERY chunk from response.iter_bytes()
    in wave.open() — but a WAV arrives as many arbitrary network chunks,
    so the second chunk (raw PCM, no RIFF header) raises and TTS dies
    silently mid-utterance. Symptom: the bot joined rooms, captions
    streamed, but not a single word was ever audible. Here we accumulate
    ALL chunks first and parse the WAV exactly once — using only APIs the
    upstream service itself proves exist (iter_bytes), so an SDK helper
    like response.read() can never be the silent failure point.
    """

    # Set by build_and_run_pipeline so TTS milestones land in /voice/events.
    _event_session: str | None = None

    async def run_tts(self, text: str, context_id):  # noqa: ANN001
        import io
        import wave as wavemod
        from pipecat.frames.frames import ErrorFrame, TTSAudioRawFrame

        from app.voice import session_registry

        sid = self._event_session
        try:
            response = await self._client.audio.speech.create(
                model=self._settings.model,
                voice=self._settings.voice,
                response_format="wav",
                speed=self._settings.speed,
                input=text,
            )
            buf = bytearray()
            async for chunk in response.iter_bytes():
                buf.extend(chunk)
            await self.stop_ttfb_metrics()
            try:
                with wavemod.open(io.BytesIO(bytes(buf))) as w:
                    pcm = w.readframes(w.getnframes())
                    rate = w.getframerate()
                    channels = w.getnchannels()
            except Exception:
                if sid:
                    session_registry.record_event(
                        sid,
                        "tts_bad_payload",
                        f"{len(buf)}b head={bytes(buf[:24])!r}",
                    )
                raise
            if sid:
                session_registry.record_event(
                    sid, "tts_audio", f"{len(pcm)}b {rate}Hz/{channels}ch"
                )
            yield TTSAudioRawFrame(pcm, rate, channels, context_id=context_id)
        except Exception as exc:  # noqa: BLE001
            if sid:
                session_registry.record_event(sid, "tts_error", str(exc)[:140])
            yield ErrorFrame(error=f"Groq TTS failed: {exc}")


class VoiceEventProbe(FrameProcessor):
    """Records audio-path milestones into session_registry.

    One instrumented test call then pinpoints the exact dead link:
      vad_user_speaking  → mic audio reached the bot AND Silero VAD fired
      transcript         → Groq STT produced text (input chain fully alive)
      agent_text         → LangGraph replied (agent bridge alive)
      tts_audio/tts_error→ synthesis result (output chain verdict)
      error_frame        → any pipecat ErrorFrame flowing downstream
    """

    def __init__(self, session_id: str) -> None:
        super().__init__()
        self._sid = session_id

    async def process_frame(self, frame, direction):  # noqa: ANN001
        await super().process_frame(frame, direction)
        try:
            from app.voice import session_registry

            if isinstance(frame, UserStartedSpeakingFrame):
                session_registry.record_event(self._sid, "vad_user_speaking")
            elif isinstance(frame, TranscriptionFrame):
                session_registry.record_event(self._sid, "transcript", frame.text[:70])
            elif isinstance(frame, TextFrame):
                session_registry.record_event(self._sid, "agent_text", frame.text[:70])
            elif isinstance(frame, ErrorFrame):
                session_registry.record_event(
                    self._sid, "error_frame", str(getattr(frame, "error", ""))[:140]
                )
        except Exception:  # noqa: BLE001 — probing must never kill the pipeline
            pass
        await self.push_frame(frame, direction)

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
    # RobustGroqTTSService replaces pipecat's chunk-by-chunk WAV parsing,
    # which crashed on multi-chunk responses and muted the bot entirely.
    tts = RobustGroqTTSService(
        api_key=settings.groq_api_key,
        settings=RobustGroqTTSService.Settings(
            model=_GROQ_TTS_MODEL,
            voice=_GROQ_TTS_VOICE,
        ),
    )
    tts._event_session = session_id  # noqa: SLF001 — TTS milestones → /voice/events

    probe = VoiceEventProbe(session_id)

    # ── Assemble pipeline ─────────────────────────────────────────────
    pipeline = Pipeline(
        [
            transport.input(),
            probe,
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

    # Build fingerprint — appears in /voice/events so we always know WHICH
    # build served a session ("started" alone doesn't tell us).
    from app.voice import session_registry as _reg0
    _reg0.record_event(session_id, "pipeline_ready", "robust-tts-v2+probe")

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
