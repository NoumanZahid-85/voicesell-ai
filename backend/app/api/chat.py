"""
Chat endpoint â€” text Q&A via the LangGraph RAG agent.

Flow:
  1. Normalize query â†’ check Redis FAQ cache â†’ return instantly on hit.
  2. Load conversation history (last N turns) from Redis.
  3. Run the LangGraph agent (triage â†’ rag â†’ respond) with LiteLLM.
  4. Cache the answer, append the turn to session memory.
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import get_db
from app.schemas.chat import ChatRequest, ChatResponse, ChatSource
from app.services import cache
from app.services.agent import (
    INTENT_ORDER,
    build_agent_graph,
    classify_intent,
    is_confirmation_utterance,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1", tags=["chat"])


@router.post("/chat", response_model=ChatResponse)
async def chat(
    req: ChatRequest,
    db: AsyncSession = Depends(get_db),
) -> ChatResponse:
    """Answer a customer question with RAG-grounded text."""

    # Transactional messages must never touch the FAQ cache:
    #   - yes/no answers to a staged action (their replies are conversation-
    #     specific; caching them replays stale answers for every future "yes")
    #   - order-intent messages (the reply is a price QUOTE and the act of
    #     producing it stages the order in Redis â€” serving a cached quote
    #     silently skips staging, so the following "yes" finds nothing and
    #     the whole confirmation flow dies. This was the production bug.)
    stateful = (
        is_confirmation_utterance(req.message)
        or classify_intent(req.message) == INTENT_ORDER
    )

    # 1) FAQ cache â€” skip LLM entirely on a hit
    if not stateful:
        cached_reply = await cache.get_cached_answer(req.message)
        if cached_reply:
            # Memory still needs this turn, or the conversation history
            # diverges from what the customer actually experienced.
            await cache.add_turn(req.session_id, req.message, cached_reply)
            return ChatResponse(reply=cached_reply, sources=[], cached=True)

    # 2) Conversation memory
    history = await cache.get_history(req.session_id)

    # TEMPORARY gate diagnostics (remove once the confirmation-gate prod issue is solved):
    # reports what the gate will see BEFORE the graph runs, so a staged-then-"yes"
    # probe reveals whether staging persisted and how triage routed.
    debug_info = None
    if req.debug:
        diag: dict = {}

        # Per-op-class probes: FAQ strings demonstrably work in prod while
        # pending keys and history lists do not â€” isolate exactly which
        # operation fails and surface its exception.
        try:
            client = cache.get_redis()
            diag["client_present"] = client is not None
            if client is not None:
                try:
                    await client.set("diag:string", "ok", ex=60)
                    diag["string_set_get"] = await client.get("diag:string")
                except Exception as exc:  # noqa: BLE001
                    diag["string_error"] = f"{type(exc).__name__}: {exc}"
                try:
                    await client.rpush("diag:list", "a")
                    await client.expire("diag:list", 60)
                    diag["list_rpush_lrange"] = await client.lrange("diag:list", 0, -1)
                except Exception as exc:  # noqa: BLE001
                    diag["list_error"] = f"{type(exc).__name__}: {exc}"
        except Exception as exc:  # noqa: BLE001
            diag["client_error"] = f"{type(exc).__name__}: {exc}"

        try:
            await cache.set_pending_order(req.session_id, {"probe": True})
            diag["pending_roundtrip"] = await cache.get_pending_order(req.session_id)
        except Exception as exc:  # noqa: BLE001
            diag["pending_error"] = f"{type(exc).__name__}: {exc}"

        try:
            diag["history_len"] = len(await cache.get_history(req.session_id))
        except Exception as exc:  # noqa: BLE001
            diag["history_error"] = f"{type(exc).__name__}: {exc}"

        debug_info = {
            "gate_utterance": stateful,
            "sid": req.session_id,
            "pending_before": await cache.get_pending_order(req.session_id),
            "redis_diag": diag,
        }

    # 3) LangGraph agent
    graph = build_agent_graph(db, session_id=req.session_id)
    try:
        state = await graph.ainvoke(
            {
                "user_message":   req.message,
                "session_id":     req.session_id,
                "customer_id":    req.customer_id or "",
                "history":        history,
                "intent":         "",
                "context":        "",
                "chunks":         [],
                "order_result":   None,
                "reply":          "",
                # Phase 5 upsell fields â€” must be present in state or LangGraph
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

    if debug_info is not None:
        debug_info["intent"] = state.get("intent")
        order_result = state.get("order_result") or {}
        debug_info["order_status"] = order_result.get("status")
        debug_info["order_error"] = order_result.get("error")
        debug_info["pending_after"] = await cache.get_pending_order(req.session_id)

    # 4) Cache + memory (best-effort) â€” never cache gate utterances
    if not stateful:
        await cache.set_cached_answer(req.message, reply)
    await cache.add_turn(req.session_id, req.message, reply)

    return ChatResponse(reply=reply, sources=sources, cached=False, debug=debug_info)
