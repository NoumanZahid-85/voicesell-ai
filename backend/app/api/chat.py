"""
Chat endpoint — text Q&A via the LangGraph RAG agent.

Flow:
  1. Normalize query → check Redis FAQ cache → return instantly on hit.
  2. Load conversation history (last N turns) from Redis.
  3. Run the LangGraph agent (triage → rag → respond) with LiteLLM.
  4. Cache the answer, append the turn to session memory.
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import get_db
from app.schemas.chat import ChatRequest, ChatResponse, ChatSource
from app.services import cache
from app.services.agent import build_agent_graph, is_confirmation_utterance

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1", tags=["chat"])


@router.post("/chat", response_model=ChatResponse)
async def chat(
    req: ChatRequest,
    db: AsyncSession = Depends(get_db),
) -> ChatResponse:
    """Answer a customer question with RAG-grounded text."""

    # Yes/no answers to a staged action must never touch the FAQ cache:
    # their replies are conversation-specific ("Your order has been placed…")
    # and caching them replays stale answers for every future "yes".
    gate_utterance = is_confirmation_utterance(req.message)

    # 1) FAQ cache — skip LLM entirely on a hit
    if not gate_utterance:
        cached_reply = await cache.get_cached_answer(req.message)
        if cached_reply:
            return ChatResponse(reply=cached_reply, sources=[], cached=True)

    # 2) Conversation memory
    history = await cache.get_history(req.session_id)

    # 3) LangGraph agent
    graph = build_agent_graph(db, session_id=req.session_id)
    try:
        state = await graph.ainvoke(
            {
                "user_message":   req.message,
                "session_id":     req.session_id,
                "history":        history,
                "intent":         "",
                "context":        "",
                "chunks":         [],
                "order_result":   None,
                "reply":          "",
                # Phase 5 upsell fields — must be present in state or LangGraph
                # raises a KeyError when the respond_node accesses them.
                "upsell_done":    False,
                "upsell_product": None,
            }
        )
    except Exception:
        # Any unhandled exception anywhere in the agent graph (RAG lookup,
        # order creation, LLM call, etc.) used to propagate as a raw 500,
        # which browsers surface as an opaque "Failed to fetch" with no
        # useful info. Log the real error server-side and answer the
        # customer gracefully instead of crashing their whole session.
        logger.exception(
            "Unhandled error in agent graph for session=%s message=%r",
            req.session_id, req.message,
        )
        return ChatResponse(
            reply=(
                "Sorry, I hit a snag processing that. Could you try again? "
                "If it was an order, please resend your request."
            ),
            sources=[],
            cached=False,
        )

    reply = state["reply"]
    sources = [
        ChatSource(
            product_id=chunk["product_id"],
            name=chunk["name"],
            price=chunk["price"],
            score=chunk["score"],
        )
        for chunk in state["chunks"]
    ]

    # 4) Cache + memory (best-effort) — never cache gate utterances
    if not gate_utterance:
        await cache.set_cached_answer(req.message, reply)
    await cache.add_turn(req.session_id, req.message, reply)

    return ChatResponse(reply=reply, sources=sources, cached=False)
