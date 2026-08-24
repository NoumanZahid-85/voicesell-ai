"""
WebSocket voice endpoint — the entire voice stack in one socket.

GET (ws) /api/v1/voice/ws?session_id=<uuid>
"""

from __future__ import annotations

import logging
import uuid

from fastapi import APIRouter, Query, WebSocket

from app.db.session import get_session_factory
from app.voice.ws_session import VoiceWSSession

logger = logging.getLogger(__name__)
router = APIRouter()


@router.websocket("/ws")
async def voice_ws(
    websocket: WebSocket,
    session_id: str = Query(default=""),
) -> None:
    await websocket.accept()
    sid = session_id or str(uuid.uuid4())

    # One DB session per voice connection — same pooling semantics as REST.
    factory = get_session_factory()
    async with factory() as db:
        session = VoiceWSSession(websocket, db, sid)
        try:
            await session.run()
        finally:
            try:
                await websocket.close()
            except Exception:  # noqa: BLE001 — already closing/closed
                pass
