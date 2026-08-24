"""
Daily.co room factory — creates ephemeral WebRTC rooms and meeting tokens.

Design decisions:
  - Rooms are short-lived (exp = now + 1h) to stay inside Daily's free tier
    of 10,000 participant-minutes/month.  Zombie rooms accumulate fast if
    you forget the exp field.
  - Meeting tokens are bot-specific, owner=True so the bot can start the
    meeting.  The browser SDK joins without a token (or with a participant
    token) on the frontend.
  - All HTTP calls go through a shared httpx.AsyncClient with connection
    pooling (keep-alive) to avoid hammering Daily's API with fresh TCP
    connections on every voice request.
  - Returns typed dataclasses — no raw dict leaking into callers.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass

import httpx

logger = logging.getLogger(__name__)

DAILY_BASE = "https://api.daily.co/v1"
_shared_client: httpx.AsyncClient | None = None


def _get_client(api_key: str) -> httpx.AsyncClient:
    """Return a shared async HTTP client with auth headers pre-set."""
    global _shared_client
    if _shared_client is None or _shared_client.is_closed:
        _shared_client = httpx.AsyncClient(
            base_url=DAILY_BASE,
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
            },
            timeout=httpx.Timeout(10.0, connect=5.0),
            limits=httpx.Limits(max_connections=50, max_keepalive_connections=20),
        )
    return _shared_client


async def close_client() -> None:
    """Call on shutdown to flush pooled connections gracefully."""
    global _shared_client
    if _shared_client and not _shared_client.is_closed:
        await _shared_client.aclose()
        _shared_client = None


@dataclass(frozen=True)
class DailyRoom:
    name: str
    url: str
    expires_at: int  # unix timestamp


@dataclass(frozen=True)
class DailyToken:
    token: str


async def create_room(api_key: str, ttl_seconds: int = 3600) -> DailyRoom:
    """
    Provision a temporary Daily room.

    Args:
        api_key:     Daily API key from settings.
        ttl_seconds: Room lifetime in seconds (default 1h).  Daily's room
                     quota is per-month participant-minutes, so short TTLs
                     are important for free-tier hygiene.

    Returns:
        DailyRoom with name, url, and expiry timestamp.

    Raises:
        httpx.HTTPStatusError: if Daily returns a non-2xx response.
    """
    now = int(time.time())
    exp = now + ttl_seconds

    client = _get_client(api_key)
    resp = await client.post(
        "/rooms",
        json={
            "properties": {
                "exp": exp,
                "enable_chat": False,       # audio-only, reduces Daily bandwidth
                "enable_screenshare": False,
                "enable_recording": False,   # keep free tier tidy
                "max_participants": 2,       # customer + bot only
                "start_audio_off": False,
                "start_video_off": True,     # voice-only
            }
        },
    )
    resp.raise_for_status()
    body = resp.json()
    return DailyRoom(
        name=body["name"],
        url=body["url"],
        expires_at=exp,
    )


async def create_bot_token(api_key: str, room_name: str) -> DailyToken:
    """
    Generate a Daily meeting token for the Pipecat bot participant.

    The bot joins as owner (is_owner=True) so it can control the call.
    Tokens are room-scoped and short-lived (same TTL as the room).

    Args:
        api_key:   Daily API key.
        room_name: Room name from create_room().

    Returns:
        DailyToken with the opaque token string.
    """
    client = _get_client(api_key)
    resp = await client.post(
        "/meeting-tokens",
        json={
            "properties": {
                "room_name": room_name,
                "is_owner": True,
                "user_name": "OMNIVOICE-Bot",
                "enable_screenshare": False,
                "start_video_off": True,
            }
        },
    )
    resp.raise_for_status()
    body = resp.json()
    return DailyToken(token=body["token"])
