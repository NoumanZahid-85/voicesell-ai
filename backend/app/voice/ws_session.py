"""
Pure-WebSocket voice pipeline — replaces the Daily/Pipecat transport stack.

Protocol
--------
Client → server : binary frames = raw PCM16 mono @16 kHz mic audio
                  text frames  = JSON control messages ({"type": "playback_done"})
Server → client : text frames  = JSON events
                    {"type": "speaking_start"}      bot audio begins this reply
                    {"type": "transcript", "text"}   user's words (Whisper)
                    {"type": "agent_caption", "text"} one TTS chunk captioned
                    {"type": "error", "message"}
                  binary frames = one complete WAV file per TTS chunk
                                  (Orpheus, 24 kHz mono)

Why half-duplex turn-taking
---------------------------
The recurring failure of the Daily/Pipecat stack was echo: the bot's own
voice leaked into the mic, VAD+STT transcribed it, and the agent answered
itself with generic prompts. Here the frontend stops streaming mic audio the
moment "speaking_start" arrives and resumes only after it reports
"playback_done" when its local audio queue drains. The server additionally
discards inbound audio while a reply is in flight — speaker leakage can
never be transcribed, by construction.

TTS chunking honours Orpheus' 200-character request limit: replies are split
on sentence boundaries, then any sentence approaching 180 chars is further
split at its last comma (falling back to spaces). Chunks are synthesised
concurrently and delivered strictly in order, so playback never gaps.
"""

from __future__ import annotations

import asyncio
import io
import json
import logging
import re
import time
import wave
from collections import deque

from groq import AsyncGroq

from app.core.config import get_settings
from app.services import cache as cache_svc
from app.services.agent import (
    INTENT_ORDER,
    build_agent_graph,
    classify_intent,
)
from app.voice import session_registry

logger = logging.getLogger(__name__)

try:  # confirmation gate lives next to the agent; keep import tolerant
    from app.services.agent import is_confirmation_utterance
except ImportError:  # pragma: no cover
    def is_confirmation_utterance(_t: str) -> bool:
        return False

# ── Audio / VAD constants ────────────────────────────────────────────
SAMPLE_RATE = 16000
VAD_WINDOW_BYTES = 1024          # 512 int16 samples @16 kHz = 32 ms (Silero window)
VAD_WINDOW_S = 0.032
START_CONFIDENCE = 0.5           # speech probability to open a turn
END_CONFIDENCE = 0.35            # below this counts as silence
MIN_SPEECH_WINDOWS = 3           # ~96 ms of speech before we commit to capture
SILENCE_END_S = 1.5              # user spec: 1.5 s of silence closes the turn
MAX_UTTERANCE_S = 20.0           # safety cap on a single utterance
PREROLL_WINDOWS = 18             # ~575 ms of audio kept before speech onset

# ── Model constants (Groq-only stack) ────────────────────────────────
STT_MODEL = "whisper-large-v3-turbo"
LLM_TIMEOUT_S = 15.0
TTS_MODEL = "canopylabs/orpheus-v1-english"
TTS_VOICE = "autumn"
TTS_CHAR_LIMIT = 180             # hard API limit is 200; stay safely under
TTS_CONCURRENCY = 4
STT_TIMEOUT_S = 12.0
TTS_TIMEOUT_S = 30.0

_GREETING = (
    "Hello! I'm CALLIOPE, your shopping assistant. "
    "Ask me about our products, prices, or say buy, followed by what you need."
)

_TAG_RE = re.compile(r"\[[a-z]{1,16}\]", re.IGNORECASE)


def _clean_caption(text: str) -> str:
    """Strip Orpheus vocal-direction tags ([cheerful] …) from display text."""
    return _TAG_RE.sub("", text).strip()


def _chunk_for_tts(text: str) -> list[str]:
    """
    Split a reply into TTS-safe chunks.

    1. Sentence boundaries (. ! ? keeping punctuation).
    2. Any sentence longer than TTS_CHAR_LIMIT is split at its last comma
       before the limit (then at any whitespace) and recursed.
    """
    sentences = [s.strip() for s in re.split(r"(?<=[.!?])\s+", text) if s.strip()]
    chunks: list[str] = []

    def _split_long(s: str) -> None:
        if len(s) <= TTS_CHAR_LIMIT:
            chunks.append(s)
            return
        cut = s.rfind(",", 0, TTS_CHAR_LIMIT)
        if cut < TTS_CHAR_LIMIT // 2:                       # comma too early → space
            cut = s.rfind(" ", 0, TTS_CHAR_LIMIT)
        if cut <= 0:                                        # no break point → hard cut
            cut = TTS_CHAR_LIMIT
        head, tail = s[: cut + 1].strip(), s[cut + 1:].strip()
        if head:
            chunks.append(head)
        if tail:
            _split_long(tail)

    for sentence in sentences:
        _split_long(sentence)
    return chunks


def _pcm_to_wav(pcm16: bytes, sample_rate: int = SAMPLE_RATE) -> bytes:
    """Wrap raw PCM16 mono bytes in a minimal WAV container for Whisper."""
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(sample_rate)
        w.writeframes(pcm16)
    return buf.getvalue()


class SileroGate:
    """Streaming wrapper over pipecat's bundled Silero ONNX model.

    Accepts arbitrary-size PCM16 byte fragments and yields at most one
    speech-probability score per complete 32 ms analysis window.
    """

    def __init__(self) -> None:
        from pipecat.audio.vad.silero import SileroVADAnalyzer

        self._analyzer = SileroVADAnalyzer(sample_rate=SAMPLE_RATE)
        self._buf = bytearray()

    def feed(self, pcm16: bytes) -> float | None:
        self._buf += pcm16
        confidences: list[float] = []
        while len(self._buf) >= VAD_WINDOW_BYTES:
            window = bytes(self._buf[:VAD_WINDOW_BYTES])
            del self._buf[:VAD_WINDOW_BYTES]
            try:
                confidences.append(self._analyzer.voice_confidence(window))
            except Exception:  # noqa: BLE001 — VAD hiccups must not kill the stream
                return None
        return max(confidences) if confidences else None


class VoiceWSSession:
    """One browser voice connection: VAD → Whisper → LangGraph → Orpheus."""

    def __init__(self, ws, db, session_id: str) -> None:
        self.ws = ws
        self.db = db
        self.session_id = session_id
        self.groq = AsyncGroq(api_key=get_settings().groq_api_key)
        self.vad = SileroGate()

        # Turn-taking gates — BOTH must be open for mic audio to be consumed.
        self.busy = False       # STT/agent/TTS synthesis in progress
        self.speaking = False   # reply audio sent, awaiting client playback_done

        # VAD state machine
        self._state = "idle"    # idle | capturing
        self._preroll: deque[bytes] = deque(maxlen=PREROLL_WINDOWS)
        self._utterance = bytearray()
        self._speech_windows = 0
        self._silence_s = 0.0
        self._captured_s = 0.0

    # ── Lifecycle ──────────────────────────────────────────────────────
    async def run(self) -> None:
        session_registry.record_event(self.session_id, "ws_connected")
        await self._send_json({"type": "ready", "session_id": self.session_id})
        try:
            await self._reply_with_audio(_GREETING)   # greeting without LLM
        except Exception:  # noqa: BLE001
            logger.exception("Greeting failed for session=%s", self.session_id)

        try:
            while True:
                message = await self.ws.receive()
                if message["type"] == "websocket.disconnect":
                    break
                if (data := message.get("bytes")) is not None:
                    self._feed_vad(data)
                elif (text := message.get("text")) is not None:
                    await self._on_control(text)
        except Exception as exc:  # noqa: BLE001
            logger.warning("WS loop ended for session=%s: %s", self.session_id, exc)
        finally:
            session_registry.record_event(self.session_id, "ws_disconnected")

    async def _on_control(self, text: str) -> None:
        try:
            payload = json.loads(text)
        except json.JSONDecodeError:
            return
        if payload.get("type") == "playback_done":
            self.speaking = False
            session_registry.record_event(self.session_id, "playback_done")

    # ── VAD state machine ─────────────────────────────────────────────
    def _feed_vad(self, pcm16: bytes) -> None:
        if self.busy or self.speaking:
            return  # half-duplex: bot has the floor, discard everything

        confidence = self.vad.feed(pcm16)
        if confidence is None:
            return

        if self._state == "idle":
            self._preroll.append(pcm16)
            self._speech_windows = (
                self._speech_windows + 1 if confidence >= START_CONFIDENCE else 0
            )
            if self._speech_windows >= MIN_SPEECH_WINDOWS:
                self._state = "capturing"
                self._utterance = bytearray(b"".join(self._preroll))
                self._silence_s = 0.0
                self._captured_s = len(self._utterance) / 2 / SAMPLE_RATE
                session_registry.record_event(self.session_id, "vad_start")
        else:
            self._utterance += pcm16
            self._captured_s += VAD_WINDOW_S
            if confidence >= END_CONFIDENCE:
                self._silence_s = 0.0
            else:
                self._silence_s += VAD_WINDOW_S

            if (
                self._silence_s >= SILENCE_END_S
                or self._captured_s >= MAX_UTTERANCE_S
            ):
                utterance = bytes(self._utterance)
                self._state = "idle"
                self._utterance.clear()
                self._speech_windows = 0
                if len(utterance) > SAMPLE_RATE * 2 * 0.4:   # ignore blips <0.4 s
                    self.busy = True
                    asyncio.create_task(self._handle_utterance(utterance))

    # ── STT → LLM → TTS turn ──────────────────────────────────────────
    async def _handle_utterance(self, pcm16: bytes) -> None:
        try:
            t0 = time.perf_counter()
            wav_bytes = _pcm_to_wav(pcm16)

            # 1) Speech-to-text
            transcript_obj = await asyncio.wait_for(
                self.groq.audio.transcriptions.create(
                    model=STT_MODEL,
                    file=("audio.wav", wav_bytes, "audio/wav"),
                ),
                timeout=STT_TIMEOUT_S,
            )
            user_text = (transcript_obj.text or "").strip()
            session_registry.record_event(self.session_id, "stt", user_text[:70])
            if not user_text:
                return
            await self._send_json({"type": "transcript", "text": user_text})

            # 2) FAQ fast-path (skipped for transactional turns), then agent
            stateful = (
                is_confirmation_utterance(user_text)
                or classify_intent(user_text) == INTENT_ORDER
            )
            if not stateful:
                cached = await cache_svc.get_cached_answer(user_text)
                if cached:
                    logger.info("Cache HIT for ws session=%s", self.session_id)
                    await self._reply_with_audio(cached)
                    return

            history = await cache_svc.get_history(self.session_id)
            state = await asyncio.wait_for(
                build_agent_graph(self.db, session_id=self.session_id).ainvoke(
                    {
                        "user_message": user_text,
                        "session_id": self.session_id,
                        "history": history,
                        "intent": "",
                        "context": "",
                        "chunks": [],
                        "order_result": None,
                        "reply": "",
                        "upsell_done": False,
                        "upsell_product": None,
                    }
                ),
                timeout=LLM_TIMEOUT_S,
            )
            reply = (state.get("reply") or "").strip() or (
                "Sorry, I didn't catch that. Could you repeat?"
            )

            if not stateful:
                asyncio.create_task(cache_svc.set_cached_answer(user_text, reply))
            asyncio.create_task(cache_svc.add_turn(self.session_id, user_text, reply))

            logger.info(
                "Voice turn done in %.0f ms | session=%s",
                (time.perf_counter() - t0) * 1000,
                self.session_id,
            )
            await self._reply_with_audio(reply)

        except asyncio.TimeoutError:
            await self._safe_reply_audio("I'm taking a little longer than usual. Could you repeat that?")
        except Exception as exc:  # noqa: BLE001 — one bad turn must not kill the socket
            logger.exception("Utterance handling failed session=%s", self.session_id)
            session_registry.record_event(self.session_id, "turn_error", str(exc)[:120])
            await self._safe_reply_audio("Something went wrong on my side. Please try again.")
        finally:
            # busy stays latched while speaking=True (client still playing);
            # cleared together once playback_done arrives.
            if not self.speaking:
                self.busy = False

    async def _safe_reply_audio(self, text: str) -> None:
        try:
            await self._reply_with_audio(text)
        except Exception:  # noqa: BLE001
            logger.exception("Fallback audio also failed session=%s", self.session_id)
            self.speaking = False
            self.busy = False

    # ── Ordered concurrent TTS delivery ───────────────────────────────
    async def _reply_with_audio(self, text: str) -> None:
        chunks = _chunk_for_tts(text)
        if not chunks:
            self.busy = False
            return

        sem = asyncio.Semaphore(TTS_CONCURRENCY)
        slots: list[asyncio.Future[bytes | None]] = [
            asyncio.get_running_loop().create_future() for _ in chunks
        ]

        async def synth(i: int, chunk: str) -> None:
            async with sem:
                try:
                    resp = await asyncio.wait_for(
                        self.groq.audio.speech.create(
                            model=TTS_MODEL,
                            voice=TTS_VOICE,
                            input=chunk,
                            response_format="wav",
                        ),
                        timeout=TTS_TIMEOUT_S,
                    )
                    slots[i].set_result(await resp.aread())
                    session_registry.record_event(self.session_id, "tts_chunk", f"{i}:{len(chunk)}c")
                except Exception as exc:  # noqa: BLE001
                    logger.error("TTS chunk %s failed: %s", i, exc)
                    slots[i].set_result(None)

        for i, chunk in enumerate(chunks):
            asyncio.create_task(synth(i, chunk))

        self.speaking = True
        await self._send_json({"type": "speaking_start"})

        for i, chunk in enumerate(chunks):
            wav = await slots[i]
            if wav is None:
                continue
            await self._send_json({"type": "agent_caption", "text": _clean_caption(chunk)})
            await self.ws.send_bytes(wav)
            session_registry.record_event(self.session_id, "audio_sent", str(i))

        # Failsafe: if the tab died before playback_done, unblock the mic.
        async def watchdog() -> None:
            await asyncio.sleep(240)
            if self.speaking:
                logger.warning("playback_done never arrived session=%s", self.session_id)
                self.speaking = False
                self.busy = False

        asyncio.create_task(watchdog())

    async def _send_json(self, payload: dict) -> None:
        await self.ws.send_text(json.dumps(payload))
