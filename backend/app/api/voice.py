"""
Voice session management API endpoints.

Endpoints:
  POST /api/v1/voice/connect   — provision a Daily room and start a Pipecat
                                  pipeline for this session.
  DELETE /api/v1/voice/connect/{session_id}  — end a session early.
  GET  /api/v1/voice/sessions  — internal debug: list active sessions.

Design decisions:

  1. Pipeline runs as a background asyncio.Task (not a thread).
     - We create it with asyncio.create_task() so the HTTP response is sent
       immediately while the pipeline initialises in the background.
     - The customer's browser connects to the Daily room and can start speaking
       within ~50ms of the POST returning.

  2. Session registry keeps track of all running tasks.
     - On shutdown (FastAPI lifespan) all tasks are cancelled gracefully.
     - This prevents zombie Daily rooms that eat into our free-tier quota.

  3. Guard rails so the server does not explode:
     - MAX_CONCURRENT_SESSIONS cap (default 20 for a free-tier server).
     - Precondition check: Daily + Deepgram + Cartesia keys must be present.
     - If any Daily API call fails we return 503 before starting anything.

  4. Task failure callback:
     - A done_callback on the asyncio.Task removes the session from the
       registry even if the pipeline crashes.  The registry never leaks.
"""

from __future__ import annotations

import asyncio
import logging
import uuid

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings, get_settings
from app.db.session import get_db
from app.voice import daily_client, session_registry
from app.voice.pipeline import build_and_run_pipeline
from app.voice.session_registry import VoiceSession

logger = logging.getLogger(__name__)

# Hard cap to keep free-tier resources sane.
# Render free plan has ~512MB RAM — each voice session holds a DB connection
# + Redis + Pipecat buffers (~25MB each), so 20 is a safe upper bound.
MAX_CONCURRENT_SESSIONS = 20

router = APIRouter(prefix="/api/v1/voice", tags=["voice"])


# ── Request / Response schemas ────────────────────────────────────────

class ConnectResponse(BaseModel):
    """Returned to the browser so it can join the Daily room."""
    session_id: str
    room_url: str
    # Note: no bot token here — the browser joins as participant (no token needed
    # for public Daily rooms in development).  For production, generate a
    # separate participant token from your Daily account.


class SessionInfo(BaseModel):
    session_id: str
    room_name: str
    age_seconds: float
    turn_count: int


class SessionEvent(BaseModel):
    ts: str
    session_id: str
    event: str
    detail: str = ""


# ── Helpers ───────────────────────────────────────────────────────────

def _require_voice_keys(settings: Settings) -> None:
    """Raise 503 if any voice service key is missing."""
    missing = []
    if not settings.daily_api_key:
        missing.append("DAILY_API_KEY")
    if not settings.groq_api_key:
        missing.append("GROQ_API_KEY")
    # Groq handles both STT (Whisper) and TTS (Orpheus) — Deepgram is no
    # longer used by the voice pipeline.
    if missing:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=(
                f"Voice services not configured. Missing env vars: {', '.join(missing)}. "
                "See README § API Keys for setup instructions."
            ),
        )


def _make_task_cleanup(session_id: str):
    """Return a done_callback that auto-removes the session from the registry."""

    def _cleanup(task: asyncio.Task) -> None:
        if task.cancelled():
            session_registry.record_event(session_id, "cancelled")
            logger.info("Pipeline task for session=%s was cancelled.", session_id)
        elif exc := task.exception():
            detail = f"{type(exc).__name__}: {exc}"
            session_registry.record_event(session_id, "crashed", detail)
            logger.error("Pipeline task for session=%s crashed: %s", session_id, exc)
        else:
            session_registry.record_event(session_id, "finished")
            logger.info("Pipeline task for session=%s finished cleanly.", session_id)
        session_registry.unregister(session_id)

    return _cleanup


# ── Endpoints ─────────────────────────────────────────────────────────

@router.post(
    "/connect",
    response_model=ConnectResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Start a new voice session",
)
async def create_voice_session(
    db: AsyncSession = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> ConnectResponse:
    """
    Provision a Daily room and launch the Pipecat pipeline as a background task.

    The browser receives `room_url` and can immediately call
    `Daily.createCallObject({ url: room_url }).join()`.

    Errors:
      503 — voice API keys not configured.
      429 — server is at max concurrent sessions.
      502 — Daily API call failed (upstream error).
    """
    _require_voice_keys(settings)

    # Concurrency cap
    active = session_registry.active_count()
    if active >= MAX_CONCURRENT_SESSIONS:
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail=f"Server at capacity ({active} active sessions). Try again shortly.",
        )

    session_id = str(uuid.uuid4())

    # 1) Provision Daily room
    try:
        room = await daily_client.create_room(settings.daily_api_key, ttl_seconds=3600)
    except Exception as exc:
        logger.error("Daily create_room failed: %s", exc)
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="Failed to provision a voice room. Please retry.",
        ) from exc

    # 2) Generate bot token
    try:
        bot_token = await daily_client.create_bot_token(settings.daily_api_key, room.name)
    except Exception as exc:
        logger.error("Daily create_bot_token failed: %s", exc)
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="Failed to generate a voice token. Please retry.",
        ) from exc

    # 3) Launch Pipecat pipeline as a background task
    # IMPORTANT: We pass `db` into the pipeline so it reuses the connection pool.
    # The pipeline may outlive this HTTP request — that is intentional.
    pipeline_coro = build_and_run_pipeline(
        room_url=room.url,
        bot_token=bot_token.token,
        session_id=session_id,
        db_session=db,
    )
    task = asyncio.create_task(pipeline_coro, name=f"voice-{session_id[:8]}")
    task.add_done_callback(_make_task_cleanup(session_id))

    # 4) Register session
    voice_session = VoiceSession(
        session_id=session_id,
        room_name=room.name,
        room_url=room.url,
        task=task,
    )
    session_registry.register(voice_session)
    session_registry.record_event(session_id, "started", room.name)

    logger.info(
        "Voice session started: id=%s room=%s active_sessions=%d",
        session_id, room.name, session_registry.active_count(),
    )

    return ConnectResponse(session_id=session_id, room_url=room.url)


@router.get(
    "/selftest",
    summary="Server-side Groq STT/TTS round-trip using this deployment's keys",
)
async def voice_selftest(
    settings: Settings = Depends(get_settings),
) -> dict:
    """
    Diagnose voice silence WITHOUT needing a browser/WebRTC session.

    Synthesizes a phrase with Groq TTS (Orpheus), then transcribes the
    resulting WAV back with Groq STT (Whisper) — using the exact API key
    this deployment runs with. Reports per-stage status so a missing model
    terms acceptance or bad key is visible immediately.
    """
    import httpx

    report: dict = {
        "groq_key_present": bool(settings.groq_api_key),
        "daily_key_present": bool(settings.daily_api_key),
        "tts_model": "canopylabs/orpheus-v1-english",
        "stt_model": "whisper-large-v3-turbo",
        "tts": None,
        "stt": None,
    }

    if not settings.groq_api_key:
        report["error"] = "GROQ_API_KEY not configured on this deployment"
        return report

    headers = {"Authorization": f"Bearer {settings.groq_api_key}"}
    try:
        async with httpx.AsyncClient(timeout=45) as client:
            # ── Stage 1: TTS ──────────────────────────────────────────
            tts_resp = await client.post(
                "https://api.groq.com/openai/v1/audio/speech",
                headers=headers,
                json={
                    "model": "canopylabs/orpheus-v1-english",
                    "voice": "autumn",
                    "input": "Hello, this is CALLIOPE. Can you hear me clearly?",
                    "response_format": "wav",
                },
            )
            tts_ok = tts_resp.status_code == 200 and len(tts_resp.content) > 1000
            report["tts"] = {
                "ok": tts_ok,
                "status": tts_resp.status_code,
                "bytes": len(tts_resp.content),
                "content_type": tts_resp.headers.get("content-type", ""),
                "detail": "" if tts_ok else tts_resp.text[:300],
            }

            if not tts_ok:
                return report

            # ── Stage 2: STT (transcribe what we just synthesized) ────
            stt_resp = await client.post(
                "https://api.groq.com/openai/v1/audio/transcriptions",
                headers=headers,
                files={"file": ("selftest.wav", tts_resp.content, "audio/wav")},
                data={"model": "whisper-large-v3-turbo"},
            )
            stt_ok = stt_resp.status_code == 200
            report["stt"] = {
                "ok": stt_ok,
                "status": stt_resp.status_code,
                "transcript": stt_resp.json().get("text", "") if stt_ok else None,
                "detail": "" if stt_ok else stt_resp.text[:300],
            }
    except Exception as exc:  # noqa: BLE001
        report["error"] = f"{type(exc).__name__}: {exc}"

    report["verdict"] = (
        "voice services fully operational — silence is in the WebRTC/pipeline layer"
        if report["stt"] and report["stt"]["ok"]
        else "voice service failure detected — see tts/stt details above"
    )
    return report


@router.delete(
    "/connect/{session_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="End a voice session early",
)
async def end_voice_session(
    session_id: str,
    settings: Settings = Depends(get_settings),
) -> None:
    """
    Cancel the Pipecat pipeline for `session_id`.

    The Daily room will time out on its own (exp was set on creation), but
    explicit teardown is good practice to free resources faster.
    """
    cancelled = await session_registry.cancel_session(session_id)
    if not cancelled:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Session '{session_id}' not found or already ended.",
        )


@router.get(
    "/sessions",
    response_model=list[SessionInfo],
    summary="List active voice sessions (debug)",
)
async def list_sessions(
    settings: Settings = Depends(get_settings),
) -> list[SessionInfo]:
    """
    Returns all currently active voice sessions.

    Intended for internal monitoring / debugging — protect this endpoint
    with admin auth in production (Phase 8).
    """
    return [
        SessionInfo(
            session_id=s.session_id,
            room_name=s.room_name,
            age_seconds=s.age_seconds,
            turn_count=s.turn_count,
        )
        for s in session_registry.all_sessions()
    ]


@router.get(
    "/events",
    response_model=list[SessionEvent],
    summary="Recent voice pipeline lifecycle events, including crashes",
)
async def voice_events() -> list[SessionEvent]:
    """
    Rolling log of pipeline starts/crashes/cancels. Because Render's free
    plan hides logs, this is how a silent bot gets diagnosed externally:
    connect once, then read this — a crashed pipeline shows its exception.
    """
    return [SessionEvent(**e) for e in session_registry.recent_events()]
