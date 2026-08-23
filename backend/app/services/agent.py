"""
LangGraph agent — Phase 4: fully integrated order management.
                   Phase 5: upsell & recommendation engine.

Graph shape (updated):

    START ──▶ triage
               │
               ├──(product_question)──▶ rag ──▶ respond ──▶ END
               │
               ├──(order_action) ──────▶ order ──▶ respond ──▶ END
               │
               └──(general_chat) ──────────────▶ respond ──▶ END

Phase 5 additions:
  - After order_created the respond node fetches hybrid recommendations
    (association rules + vector similarity) and appends one upsell message.
  - A per-session upsell_done flag (AgentState + Redis) prevents repeat
    suggestions in the same conversation.
  - Customer's yes/no response to the upsell is detected in order_node and
    logged to recommendation_logs for future optimisation.
"""

from __future__ import annotations

import json
import logging
import re
import uuid
from typing import TypedDict

from langgraph.graph import END, START, StateGraph

from app.services.cache import (
    clear_pending_order,
    get_pending_order,
    set_pending_order,
)
from app.services.llm import llm_generate
from app.services.order_service import (
    InsufficientStock,
    OrderError,
    OrderLineInput,
    ProductNotFound,
    build_idempotency_key,
    cancel_order,
    check_and_quote,
    create_order,
    list_customer_orders,
)
from app.services.prompts import (
    GENERAL_SYSTEM_PROMPT,
    ORDER_EXTRACT_PROMPT,
    ORDER_SYSTEM_PROMPT,
    RAG_SYSTEM_PROMPT,
)
from app.services.rag import RAGService, extract_max_price
from app.services.recommendations import RecommendationService, build_upsell_message

logger = logging.getLogger(__name__)

# ── Intent classification ────────────────────────────────────────────

ORDER_KEYWORDS = (
    "order", "buy", "purchase", "checkout", "cart", "add to cart",
    "cancel", "cancel order", "pay for", "place an order", "i want to buy",
    "i'd like to order", "my orders", "show my orders", "order history",
    "how much is", "how much does", "what does it cost", "price of",
)
GREETING_KEYWORDS = (
    "hi", "hello", "hey", "thanks", "thank you", "how are you",
    "good morning", "good afternoon", "bye", "goodbye", "see you",
)

INTENT_PRODUCT = "product_question"
INTENT_ORDER   = "order_action"
INTENT_GENERAL = "general_chat"


def classify_intent(text: str) -> str:
    t = text.lower().strip()
    words = t.split()

    # Strong, unambiguous order signals (always order intent)
    if any(kw in t for kw in ORDER_KEYWORDS if kw != "buy"):
        return INTENT_ORDER

    # Bare "buy" is ambiguous: a product question like
    # "What can I buy for home decor?" asks about the catalog, while
    # "I want to buy 2 mice" or "Can I buy the mouse?" is an action.
    if "buy" in t:
        words = set(t.split())
        has_quantity = any(ch.isdigit() for ch in t) or bool(
            words & {"a", "an", "the", "one", "two", "three", "some", "few", "pair", "several", "dozen"}
        )
        is_catalog_question = t.startswith(("what", "which", "where", "do you", "is there", "are there", "any ", "tell me"))
        if not (is_catalog_question and not has_quantity):
            return INTENT_ORDER

    if any(kw in t for kw in GREETING_KEYWORDS) and len(words) <= 6:
        return INTENT_GENERAL
    return INTENT_PRODUCT


# ── State ─────────────────────────────────────────────────────────────

class AgentState(TypedDict):
    user_message: str
    session_id:   str         # voice session / Redis key
    history:      list[dict]
    intent:       str
    context:      str
    chunks:       list[dict]
    order_result: dict | None # filled by order node
    reply:        str
    # Phase 5 — upsell guard
    upsell_done:    bool          # True once a suggestion was made this session
    upsell_product: dict | None   # {product_id, product_name, price, source}


# ── Graph factory ─────────────────────────────────────────────────────

def build_agent_graph(session, session_id: str = ""):
    """
    Compile the LangGraph agent bound to a DB session.

    Args:
        session:    SQLAlchemy AsyncSession.
        session_id: Voice/chat session ID — used for Redis pending actions.
                    Optional; if empty, order confirmation memory is disabled.
    """
    rag = RAGService(session)

    # ── Triage ───────────────────────────────────────────────────────

    async def triage_node(state: AgentState) -> AgentState:
        sid = state.get("session_id") or session_id
        # Confirmation Gate routing: classify_intent() has no concept of
        # conversation state, so a bare "yes"/"no" classifies as a product
        # question and RAG answers gibberish while the staged action sits in
        # Redis until its TTL expires. If something is actually pending (an
        # order awaiting confirmation, or an upsell offer), yes/no utterances
        # belong to the order node's gate — route them there.
        msg = state["user_message"].lower().strip()
        if sid and (_is_affirmation(msg) or _is_negation(msg)):
            pending = await get_pending_order(sid)
            # History fallback: if Redis lost the staged action (observed in
            # production), the conversation itself still proves a confirmation
            # is awaited — route to the gate instead of RAG gibberish.
            if pending or _history_awaits_confirmation(state.get("history") or []):
                state["intent"] = INTENT_ORDER
                return state
        state["intent"] = classify_intent(state["user_message"])
        return state

    # ── RAG ──────────────────────────────────────────────────────────

    async def rag_node(state: AgentState) -> AgentState:
        chunks = await rag.retrieve(
            state["user_message"],
            top_k=5,
            max_price=extract_max_price(state["user_message"]),
        )
        state["chunks"] = [c.to_dict() for c in chunks]
        state["context"] = rag.format_context(chunks)
        return state

    # ── Order ─────────────────────────────────────────────────────────

    async def order_node(state: AgentState) -> AgentState:
        """
        Handles all order-related intents in one node.

        Algorithm:
          1. Check Redis for a pending confirmation action.
          2. If found and user says yes/confirm → execute the pending action.
          3. Otherwise:
             a. Use LLM to extract structured action from natural language.
             b. For "create": quote price → set pending → ask for confirmation.
             c. For "cancel": confirm ownership → set pending → ask to confirm.
             d. For "list": fetch + format order history.
        """
        sid = state.get("session_id") or session_id
        msg = state["user_message"].lower().strip()
        state["order_result"] = None

        # ── Step 1: confirmation pending? ────────────────────────────
        if sid:
            pending = await get_pending_order(sid)
            last_bot = _last_bot_text(state.get("history") or [])
            low_bot = last_bot.lower()
            upsell_prompt_open = "would you like to add one" in low_bot

            # ── Upsell acceptance / rejection ────────────────────────
            if pending and pending.get("type") == "upsell":
                rec_svc = RecommendationService(session)
                if _is_affirmation(msg):
                    # Log acceptance
                    try:
                        import uuid as _uuid
                        await rec_svc.log_recommendation(
                            session_id=sid,
                            product_id=_uuid.UUID(pending["product_id"]),
                            source=pending.get("source", "unknown"),
                            was_accepted=True,
                        )
                    except Exception as exc:
                        logger.warning("Upsell log failed: %s", exc)
                    await clear_pending_order(sid)
                    # An accepted upsell must actually place the add-on
                    # order — saying "I've added it" without creating an
                    # order made the whole upsell loop cosmetic.
                    state["order_result"] = await _place_addon_order(
                        session, sid, str(pending.get("product_name", ""))
                    )
                    return state
                if _is_negation(msg):
                    # Log rejection
                    try:
                        import uuid as _uuid
                        await rec_svc.log_recommendation(
                            session_id=sid,
                            product_id=_uuid.UUID(pending["product_id"]),
                            source=pending.get("source", "unknown"),
                            was_accepted=False,
                        )
                    except Exception as exc:
                        logger.warning("Upsell log failed: %s", exc)
                    await clear_pending_order(sid)
                    state["order_result"] = {"status": "upsell_declined"}
                    return state

            # ── Order confirmation / cancellation ─────────────────────
            if pending and _is_affirmation(msg):
                try:
                    state["order_result"] = await _execute_pending(session, sid, pending)
                except OrderError as exc:
                    logger.warning(
                        "Order execution failed for session=%s: %s", sid, exc
                    )
                    state["order_result"] = {
                        "error": "order_failed",
                        "message": (
                            "Sorry, I couldn't complete that order just now. "
                            "Could you please say what you'd like to order again?"
                        ),
                    }
                return state
            if pending and _is_negation(msg):
                await clear_pending_order(sid)
                state["order_result"] = {"status": "cancelled_by_user"}
                return state

            # ── History fallback gate ──────────────────────────────────
            # Redis lost the staged action (production bug) — but the last
            # assistant turn proves what the customer is confirming.
            if not pending and (_is_affirmation(msg) or _is_negation(msg)):
                if upsell_prompt_open:
                    if _is_affirmation(msg):
                        extracted_upsell = _upsell_product_from_text(last_bot)
                        if extracted_upsell:
                            state["order_result"] = await _place_addon_order(
                                session, sid, extracted_upsell
                            )
                            return state
                        state["order_result"] = {"error": "parse_failed"}
                        return state
                    state["order_result"] = {"status": "upsell_declined"}
                    return state
                if _history_awaits_confirmation(state.get("history") or []):
                    if _is_affirmation(msg):
                        state["order_result"] = await _rebuild_and_execute(
                            session, sid, state.get("history") or []
                        )
                    else:
                        state["order_result"] = {"status": "cancelled_by_user"}
                    return state

        # ── Step 2: extract action via LLM ───────────────────────────
        extracted = await _extract_order_intent(state["user_message"])
        if not extracted:
            state["order_result"] = {"error": "parse_failed"}
            return state

        action = extracted.get("action")

        # ── Step 3: dispatch ──────────────────────────────────────────
        if action == "create":
            lines = [
                OrderLineInput(
                    product_name=item.get("name", ""),
                    quantity=int(item.get("quantity", 1)),
                )
                for item in extracted.get("items", [])
                if item.get("name")
            ]
            if not lines:
                state["order_result"] = {"error": "no_items"}
                return state

            try:
                quote = await check_and_quote(session, lines)
                total = round(sum(q["subtotal"] for q in quote), 2)
                idem = build_idempotency_key(sid or "anon", lines)
                # Store pending order action in Redis (TTL 120s = 2 voice turns)
                if sid:
                    await set_pending_order(sid, {
                        "action": "create",
                        "lines": [{"name": ln.product_name, "qty": ln.quantity} for ln in lines],
                        "idem_key": idem,
                        "total": total,
                    })
                state["order_result"] = {
                    "status": "awaiting_confirmation",
                    "quote": quote,
                    "total": total,
                }
            except (ProductNotFound, InsufficientStock, OrderError) as e:
                state["order_result"] = {"error": str(e)}

        elif action == "cancel":
            order_id = extracted.get("order_id", "")
            cust_id  = extracted.get("customer_id", "")
            if not order_id:
                state["order_result"] = {"error": "missing_order_id"}
                return state

            if sid:
                await set_pending_order(sid, {
                    "action": "cancel",
                    "order_id": order_id,
                    "customer_id": cust_id,
                })
            state["order_result"] = {
                "status": "awaiting_cancel_confirmation",
                "order_id": order_id,
            }

        elif action == "list":
            cust_id = extracted.get("customer_id", "")
            if not cust_id:
                state["order_result"] = {"error": "missing_customer_id"}
                return state
            try:
                orders = await list_customer_orders(session, cust_id, limit=5)
                state["order_result"] = {
                    "status": "orders_listed",
                    "orders": [
                        {
                            "order_id": o.order_id[:8],  # short ID for voice
                            "status": o.status,
                            "total": o.total_amount,
                        }
                        for o in orders
                    ],
                }
            except Exception as exc:
                state["order_result"] = {"error": str(exc)}

        else:
            state["order_result"] = {"error": "unknown_action"}

        return state

    # ── Respond ───────────────────────────────────────────────────────

    async def respond_node(state: AgentState) -> AgentState:
        state["reply"] = await _generate_reply(state, session)
        return state

    # ── Routing ───────────────────────────────────────────────────────

    def route_by_intent(state: AgentState) -> str:
        if state["intent"] == INTENT_PRODUCT:
            return "rag"
        if state["intent"] == INTENT_ORDER:
            return "order"
        return "respond"

    # ── Compile ───────────────────────────────────────────────────────

    builder = StateGraph(AgentState)
    builder.add_node("triage", triage_node)
    builder.add_node("rag",    rag_node)
    builder.add_node("order",  order_node)
    builder.add_node("respond", respond_node)

    builder.add_edge(START, "triage")
    builder.add_conditional_edges(
        "triage",
        route_by_intent,
        {"rag": "rag", "order": "order", "respond": "respond"},
    )
    builder.add_edge("rag",   "respond")
    builder.add_edge("order", "respond")
    builder.add_edge("respond", END)

    return builder.compile()


# ── LLM-powered intent extraction ────────────────────────────────────

async def _extract_order_intent(user_message: str) -> dict | None:
    """
    Use LLM to extract a structured order intent from a natural-language message.

    Returns a dict like:
      {"action": "create", "items": [{"name": "keyboard", "quantity": 2}]}
      {"action": "cancel", "order_id": "abc123", "customer_id": "..."}
      {"action": "list",   "customer_id": "..."}

    Returns None if the LLM response cannot be parsed.
    """
    prompt = ORDER_EXTRACT_PROMPT.format(message=user_message)
    try:
        raw = await llm_generate(
            [
                {"role": "system", "content": "You are a JSON extraction assistant. Return ONLY valid JSON."},
                {"role": "user", "content": prompt},
            ],
            temperature=0.0,
            max_tokens=256,
        )
        # Strip markdown code fences if present
        raw = raw.strip().lstrip("```json").lstrip("```").rstrip("```").strip()
        return json.loads(raw)
    except Exception as exc:
        logger.warning("Order intent extraction failed: %s | input=%r", exc, user_message[:80])
        return None


# ── Confirmation Gate (pure decision core) ────────────────────────────

def _is_affirmation(text: str) -> bool:
    AFFIRMATIONS = {"yes", "yeah", "yep", "sure", "confirm", "ok", "okay",
                    "go ahead", "do it", "place it", "proceed", "absolutely",
                    "definitely", "correct", "right", "please do"}
    return any(a in text for a in AFFIRMATIONS)


def _is_negation(text: str) -> bool:
    NEGATIONS = {"no", "nope", "cancel it", "don't", "never mind",
                 "stop", "abort", "forget it", "not now", "negative"}
    return any(n in text for n in NEGATIONS)


def is_confirmation_utterance(text: str) -> bool:
    """True if the message reads like a yes/no answer to a staged action.

    Shared with the API layer so such messages bypass the FAQ cache — caching
    replies to bare "yes"/"no" poisons the cache across sessions (the next
    customer's "yes" would replay a previous conversation's answer).
    """
    t = text.lower().strip()
    return _is_affirmation(t) or _is_negation(t)


def decide_gate_action(pending: dict | None, utterance: str) -> str:
    """Decide what to do with an utterance while an action is staged.

    Pure function — no I/O. Returns one of:
      "confirm"  → execute the staged action
      "deny"     → abort the staged action
      "stall"    → keep waiting; the utterance is neither clear yes nor no
    With nothing staged, always "stall" (the gate is not engaged).
    """
    if not pending:
        return "stall"
    if _is_affirmation(utterance.lower()):
        return "confirm"
    if _is_negation(utterance.lower()):
        return "deny"
    return "stall"


async def _execute_pending(session, sid: str, pending: dict) -> dict:
    """Execute a previously pending order action after voice confirmation."""
    await clear_pending_order(sid)
    action = pending.get("action")

    if action == "create":
        lines = [
            OrderLineInput(product_name=i["name"], quantity=i["qty"])
            for i in pending.get("lines", [])
        ]
        result = await create_order(
            session,
            customer_id=pending.get("customer_id", _ANON_CUSTOMER_ID),
            lines=lines,
            idempotency_key=pending.get("idem_key", ""),
        )
        return {
            "status": "order_created",
            "order_id": result.order_id[:8],
            "total": result.total_amount,
            "items": [
                {
                    "name": i.product_name,
                    "qty": i.quantity,
                    "product_id": i.product_id,
                }
                for i in result.items
            ],
        }

    if action == "cancel":
        result = await cancel_order(
            session,
            order_id=pending["order_id"],
            customer_id=pending.get("customer_id", _ANON_CUSTOMER_ID),
        )
        return {"status": "order_cancelled", "order_id": result.order_id[:8]}

    return {"error": "unknown_pending_action"}


# ── History-fallback gate helpers ─────────────────────────────────────

# Phrases our own reply templates use when a confirmation is on the table.
_CONFIRM_MARKERS = (
    "shall i place this order",
    "say yes to confirm",
    "are you sure you want to cancel",
)
_UPSELL_MARKER = "would you like to add one"


def _last_bot_text(history: list[dict]) -> str:
    """Text of the most recent assistant turn ('' when history is empty)."""
    if not history:
        return ""
    last = history[-1] or {}
    bot = last.get("bot")
    return bot.strip() if isinstance(bot, str) else ""


def _history_awaits_confirmation(history: list[dict]) -> bool:
    low = _last_bot_text(history).lower()
    return any(m in low for m in _CONFIRM_MARKERS) or _UPSELL_MARKER in low


def _upsell_product_from_text(bot_text: str) -> str:
    """Extract the offered product name from our own upsell templates.

    Templates (build_upsell_message):
      "...picked up the {name} — it's ${price}. Would you like to add one?"
      "...like the {name} (${price}), which pairs well..."
    """
    m = re.search(r"picked up the (.+?) — it's \$[\d.]+", bot_text)
    if not m:
        m = re.search(r"also like the (.+?) \(\$[\d.]+\)", bot_text)
    return m.group(1).strip() if m else ""


def _is_pure_gate_utterance(text: str) -> bool:
    """True only for short bare confirmations ('yes', 'nope', 'go ahead').

    Used when scanning history so real requests that merely START with an
    acknowledgment ('ok, I want two lotions') are still processed as orders.
    """
    t = text.lower().strip()
    return len(t.split()) <= 3 and (_is_affirmation(t) or _is_negation(t))


async def _place_addon_order(session, sid: str, product_name: str) -> dict:
    """Create a 1-unit add-on order for an accepted upsell suggestion."""
    if not product_name:
        return {"error": "parse_failed"}
    lines = [OrderLineInput(product_name=product_name, quantity=1)]
    try:
        result = await create_order(
            session,
            customer_id=_ANON_CUSTOMER_ID,
            lines=lines,
            idempotency_key=build_idempotency_key(f"{sid}:upsell", lines),
        )
        return {
            "status": "upsell_accepted",
            "product_name": result.items[0].product_name if result.items else product_name,
            "order_id": result.order_id[:8],
            "total": result.total_amount,
        }
    except OrderError as exc:
        logger.warning("Add-on order failed for session=%s: %s", sid, exc)
        return {
            "error": "order_failed",
            "message": (
                "Sorry, I couldn't add that to your orders just now. "
                "You can ask me to order it separately in a moment."
            ),
        }


async def _rebuild_and_execute(session, sid: str, history: list[dict]) -> dict:
    """
    Re-derive and execute the action the customer already quoted-approved.

    Redis lost the staged action, so walk backwards through recent turns,
    find the last real request ("order two lotions" / "cancel order X"),
    and run it. Safe against double-execution: the idempotency key is
    deterministic from session + items, so a replay collapses onto the
    original order instead of duplicating it.
    """
    for turn in reversed(history[-6:]):
        user_text = ((turn or {}).get("user") or "").strip()
        if not user_text or _is_pure_gate_utterance(user_text):
            continue
        extracted = await _extract_order_intent(user_text)
        if not extracted:
            continue
        action = extracted.get("action")

        if action == "create":
            lines = [
                OrderLineInput(
                    product_name=item.get("name", ""),
                    quantity=int(item.get("quantity", 1)),
                )
                for item in extracted.get("items", [])
                if item.get("name")
            ]
            if not lines:
                continue
            try:
                result = await create_order(
                    session,
                    customer_id=_ANON_CUSTOMER_ID,
                    lines=lines,
                    idempotency_key=build_idempotency_key(sid or "anon", lines),
                )
                return {
                    "status": "order_created",
                    "order_id": result.order_id[:8],
                    "total": result.total_amount,
                    "items": [
                        {
                            "name": i.product_name,
                            "qty": i.quantity,
                            "product_id": i.product_id,
                        }
                        for i in result.items
                    ],
                }
            except OrderError as exc:
                logger.warning(
                    "Rebuilt order failed for session=%s: %s", sid, exc
                )
                return {
                    "error": "order_failed",
                    "message": (
                        "Sorry, I couldn't complete that order just now. "
                        "Could you please say what you'd like to order again?"
                    ),
                }

        if action == "cancel":
            oid = extracted.get("order_id", "")
            if not oid:
                continue
            try:
                result = await cancel_order(
                    session, order_id=oid, customer_id=_ANON_CUSTOMER_ID
                )
                return {"status": "order_cancelled", "order_id": result.order_id[:8]}
            except OrderError as exc:
                return {"error": str(exc)}

    logger.info("History fallback found no actionable request (session=%s)", sid)
    return {"error": "parse_failed"}


# Placeholder customer ID used when customer auth is not yet wired (Phase 6).
# In production this will come from the session's JWT.
_ANON_CUSTOMER_ID = "00000000-0000-0000-0000-000000000001"


# ── Reply generator ───────────────────────────────────────────────────

async def _generate_reply(state: AgentState, session=None) -> str:  # noqa: ANN001
    intent = state["intent"]

    # ── Order replies (structured → no LLM needed for happy path) ────
    if intent == INTENT_ORDER:
        order_result = state.get("order_result") or {}
        status = order_result.get("status")
        error  = order_result.get("error")

        if status == "awaiting_confirmation":
            quote = order_result.get("quote", [])
            total = order_result.get("total", 0)
            items_str = "; ".join(
                f"{q['quantity']}x {q['product_name']} at ${q['unit_price']:.2f} each"
                for q in quote
            )
            return (
                f"I found the following: {items_str}. "
                f"Your total would be ${total:.2f}. "
                "Shall I place this order? Say yes to confirm."
            )

        if status == "awaiting_cancel_confirmation":
            oid = order_result.get("order_id", "")
            return (
                f"Are you sure you want to cancel order {oid}? "
                "This action cannot be undone. Say yes to confirm."
            )

        if status == "order_created":
            oid   = order_result.get("order_id", "")
            total = order_result.get("total", 0)
            base_reply = (
                f"Your order has been placed! Order number {oid}, total ${total:.2f}. " 
                "You'll receive a confirmation shortly."
            )

            # ── Phase 5: Upsell (one per conversation) ────────────────
            sid = state.get("session_id", "")
            already_upsold = state.get("upsell_done", False)

            if not already_upsold and session is not None and sid:
                try:
                    from uuid import UUID as _UUID
                    rec_svc = RecommendationService(session)
                    # Anchor on the products actually purchased — the gate
                    # execution path returns them in items (quote only
                    # exists on the pre-confirmation path).
                    item_list = order_result.get("items") or []
                    pid_strs = [
                        it.get("product_id")
                        for it in item_list
                        if isinstance(it, dict) and it.get("product_id")
                    ]
                    pids = [_UUID(p) for p in pid_strs if p]

                    if pids:
                        recs = await rec_svc.get_recommendations(pids, limit=1)
                        if recs:
                            rec = recs[0]
                            upsell_msg = build_upsell_message(rec)
                            # Store pending upsell in Redis so next yes/no is caught
                            await set_pending_order(sid, {
                                "type":         "upsell",
                                "product_id":   str(rec.product_id),
                                "product_name": rec.product_name,
                                "source":       rec.source,
                            })
                            return f"{base_reply} {upsell_msg}"
                except Exception as exc:
                    logger.warning("Upsell recommendation failed (non-fatal): %s", exc)

            return f"{base_reply} Is there anything else I can help you with?"

        if status == "order_cancelled":
            oid = order_result.get("order_id", "")
            return (
                f"Order {oid} has been cancelled successfully "
                "and your stock has been restored."
            )

        if status == "cancelled_by_user":
            return "No problem — I've cancelled that action. What else can I help you with?"

        if status == "upsell_accepted":
            pname = order_result.get("product_name", "that product")
            oid   = order_result.get("order_id", "")
            total = order_result.get("total")
            if oid:
                return (
                    f"Great! I've placed a separate order for the {pname} "
                    f"— order number {oid}, total ${total:.2f}. "
                    "Is there anything else I can help you with?"
                )
            return (
                f"Great! I've added {pname} to a new order for you. "
                "Is there anything else I can help you with?"
            )

        if status == "upsell_declined":
            return "No problem! Let me know if there's anything else I can help you with."

        if status == "orders_listed":
            orders = order_result.get("orders", [])
            if not orders:
                return "You don't have any orders yet. Would you like to place one?"
            items_str = "; ".join(
                f"Order {o['order_id']} ({o['status']}, ${o['total']:.2f})" for o in orders
            )
            return f"Your recent orders: {items_str}."

        if error == "parse_failed":
            return (
                "I'm not sure what order action you'd like. "
                "You can say things like: 'order 2 keyboards', "
                "'cancel order 12345', or 'show my orders'."
            )

        if error == "order_failed":
            return order_result.get(
                "message", "Sorry, I couldn't complete that order. Please try again."
            )

        if error:
            # Surface domain errors (stock, not found, etc.) in plain English
            return str(error)

    # ── Product replies: RAG-grounded ────────────────────────────────
    if intent == INTENT_PRODUCT and state.get("context"):
        system = RAG_SYSTEM_PROMPT.format(context=state["context"])
    elif intent == INTENT_ORDER:
        system = ORDER_SYSTEM_PROMPT
    else:
        system = GENERAL_SYSTEM_PROMPT

    messages: list[dict[str, str]] = [{"role": "system", "content": system}]
    for turn in (state.get("history") or [])[-8:]:
        messages.append({"role": "user",      "content": turn["user"]})
        messages.append({"role": "assistant", "content": turn["bot"]})
    messages.append({"role": "user", "content": state["user_message"]})

    return await llm_generate(messages)
