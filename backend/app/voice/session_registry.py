"""
Active voice session registry — tracks all running Pipecat pipelines.

Why a standalone module:
  - FastAPI and Pipecat run in the same process but Pipecat owns its own
    asyncio tasks.  We need a safe way to find, cancel, and audit sessions
    from API layer or shutdown hooks without sharing mutable state in a
    global dict scattered across modules.
  - All mutations go through this module's functions — easy to unit-test,
    easy to replace with a Redis-backed registry later when scaling to
    multi-process workers.

Thread safety:
  - Python's GIL protects dict operations, but asyncio tasks may race on
    iteration.  We copy keys before iterating on shutdown.
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections import deque
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)

# ── Session record ──────────────────────────────────────────────────

@dataclass
class VoiceSession:
    session_id: str
    room_name: str
    room_url: str
    task: asyncio.Task          # the running PipelineTask wrapper
    started_at: float = field(default_factory=time.time)
    turn_count: int = 0         # incremented by the processor per turn

    @property
    def age_seconds(self) -> float:
        return time.time() - self.started_at


# ── Recent lifecycle events (crash visibility without log access) ────
#
# Render's free plan exposes no logs, so pipeline crashes used to be
# invisible. Every session lifecycle transition is appended here and
# served by GET /api/v1/voice/sessions so a silent bot can be diagnosed
# from the outside.

_MAX_EVENTS = 40

_events: deque[dict] = deque(maxlen=_MAX_EVENTS)


def record_event(session_id: str, event: str, detail: str = "") -> None:
    _events.append({
        "ts": time.strftime("%H:%M:%S"),
        "session_id": session_id[:8],
        "event": event,
        "detail": detail[:500],
    })


def recent_events() -> list[dict]:
    return list(_events)


# ── In-memory registry ──────────────────────────────────────────────

_sessions: dict[str, VoiceSession] = {}


def register(session: VoiceSession) -> None:
    """Add a session to the registry."""
    _sessions[session.session_id] = session
    logger.info(
        "Voice session registered: id=%s room=%s",
        session.session_id, session.room_name,
    )


def unregister(session_id: str) -> VoiceSession | None:
    """Remove a session.  Returns the removed session or None if absent."""
    session = _sessions.pop(session_id, None)
    if session:
        logger.info("Voice session unregistered: id=%s turns=%d", session_id, session.turn_count)
    return session


def get(session_id: str) -> VoiceSession | None:
    return _sessions.get(session_id)


def active_count() -> int:
    return len(_sessions)


def all_sessions() -> list[VoiceSession]:
    return list(_sessions.values())


async def cancel_session(session_id: str) -> bool:
    """
    Cancel and clean up a running voice session.

    Returns True if session was found and cancelled, False otherwise.
    """
    session = unregister(session_id)
    if not session:
        return False

    if not session.task.done():
        session.task.cancel()
        try:
            await asyncio.wait_for(asyncio.shield(session.task), timeout=2.0)
        except (asyncio.CancelledError, asyncio.TimeoutError):
            pass
    logger.info("Voice session cancelled: id=%s", session_id)
    return True


async def shutdown_all() -> None:
    """
    Cancel every active session — called from FastAPI lifespan shutdown.

    Uses a snapshot of keys so we don't mutate the dict while iterating.
    """
    session_ids = list(_sessions.keys())
    if not session_ids:
        return
    logger.info("Shutting down %d active voice sessions...", len(session_ids))
    for sid in session_ids:
        await cancel_session(sid)
    logger.info("All voice sessions shut down.")
