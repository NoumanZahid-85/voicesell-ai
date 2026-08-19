"""
Phase 5 — Hybrid Recommendation Engine.

Strategy:
  1. Association rule lookup  (confidence-ranked, from product_associations table)
  2. Vector similarity fallback (Qdrant recommend API) for products without rules
  3. Merge, deduplicate, weight: association × 0.7 + vector × 0.3
  4. Filter: exclude already-ordered products, out-of-stock items
  5. Return top-N with human-readable reasoning strings

Upsell guard:
  Maximum 1 upsell suggestion per conversation.  Callers must track this
  externally (agent.py stores upsell_done in AgentState).

Logging:
  Every suggestion is written to recommendation_logs regardless of acceptance.
  Call log_recommendation_outcome() once you know if the customer accepted.
"""

from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Product, ProductAssociation, RecommendationLog
from app.services.qdrant_client import PRODUCTS_COLLECTION, get_qdrant_client

logger = logging.getLogger(__name__)

# Weighting constants (Phase 5 spec)
_ASSOC_WEIGHT = 0.7
_VECTOR_WEIGHT = 0.3

# Minimum score threshold below which we make no suggestion
_MIN_SCORE = 0.3

# Qdrant vector similarity score below which we discard, a result
_VECTOR_SCORE_THRESHOLD = 0.50


@dataclass
class Recommendation:
    """A single product recommendation returned by the engine."""

    product_id: UUID
    product_name: str
    price: float
    stock_quantity: int
    source: str          # "association" | "vector"
    score: float         # 0.0 – 1.0 composite
    reasoning: str       # Human-readable text for the agent to say aloud


class RecommendationService:
    """
    Hybrid recommendation engine combining:
      - Frequently-bought-together association rules (from product_associations)
      - Qdrant vector similarity (embedding-based item-to-item)
    """

    def __init__(self, session: AsyncSession) -> None:
        self.session = session
        self.qdrant = get_qdrant_client()

    # ── Public API ────────────────────────────────────────────────────

    async def get_recommendations(
        self,
        product_ids: list[UUID],
        exclude_ids: list[UUID] | None = None,
        limit: int = 2,
    ) -> list[Recommendation]:
        """
        Return up to `limit` recommendations for the given product_ids.

        Args:
            product_ids:  Products the customer just ordered.
            exclude_ids:  Additional product IDs to exclude (e.g. already in cart).
            limit:        Max recommendations to return (plan spec: 2).

        Returns:
            List of Recommendation objects sorted by composite score DESC.
            Empty list if nothing passes the quality bar.
        """
        excluded: set[str] = {str(pid) for pid in (product_ids + (exclude_ids or []))}

        # ── Step 1: Association rules ─────────────────────────────────
        assoc_recs = await self._association_recs(product_ids, excluded)

        # ── Step 2: Vector similarity (fills gaps or replaces low-conf rules) ──
        vector_recs = await self._vector_recs(product_ids, excluded)

        # ── Step 3: Merge → deduplicate → weight ──────────────────────
        merged: dict[str, Recommendation] = {}

        for rec in assoc_recs:
            key = str(rec.product_id)
            merged[key] = Recommendation(
                product_id=rec.product_id,
                product_name=rec.product_name,
                price=rec.price,
                stock_quantity=rec.stock_quantity,
                source=rec.source,
                score=rec.score * _ASSOC_WEIGHT,
                reasoning=rec.reasoning,
            )

        for rec in vector_recs:
            key = str(rec.product_id)
            if key in merged:
                # Boost existing entry if vector also found it
                merged[key].score += rec.score * _VECTOR_WEIGHT
            else:
                merged[key] = Recommendation(
                    product_id=rec.product_id,
                    product_name=rec.product_name,
                    price=rec.price,
                    stock_quantity=rec.stock_quantity,
                    source=rec.source,
                    score=rec.score * _VECTOR_WEIGHT,
                    reasoning=rec.reasoning,
                )

        # ── Step 4: Filter out of stock + low quality ────────────────
        qualified = [
            r for r in merged.values()
            if r.stock_quantity > 0 and r.score >= _MIN_SCORE
        ]
        qualified.sort(key=lambda r: r.score, reverse=True)
        return qualified[:limit]

    async def log_recommendation(
        self,
        session_id: str,
        product_id: UUID,
        source: str,
        was_accepted: bool,
    ) -> None:
        """Persist a recommendation event for future optimisation analytics."""
        try:
            log = RecommendationLog(
                id=uuid.uuid4(),
                session_id=session_id,
                recommended_product_id=product_id,
                source=source,
                was_accepted=int(was_accepted),
            )
            self.session.add(log)
            await self.session.commit()
        except Exception as exc:  # noqa: BLE001
            await self.session.rollback()
            logger.warning("Failed to log recommendation event: %s", exc)

    # ── Internal helpers ──────────────────────────────────────────────

    async def _association_recs(
        self, product_ids: list[UUID], excluded: set[str]
    ) -> list[Recommendation]:
        """Query product_associations table for rule-based recommendations."""
        if not product_ids:
            return []

        stmt = (
            select(ProductAssociation, Product)
            .join(Product, Product.id == ProductAssociation.product_b_id)  # explicit onclause
            .where(
                ProductAssociation.product_a_id.in_(product_ids),  # type: ignore[attr-defined]
            )
            .where(
                ProductAssociation.confidence >= 0.3,  # type: ignore[operator]
            )
            .where(
                ProductAssociation.lift >= 1.5,  # type: ignore[operator]
            )
            .order_by(
                ProductAssociation.confidence.desc(),  # type: ignore[attr-defined]
                ProductAssociation.lift.desc(),         # type: ignore[attr-defined]
            )
            .limit(20)
        )

        try:
            rows = (await self.session.execute(stmt)).all()
        except Exception as exc:  # noqa: BLE001
            logger.warning("Association rule query failed: %s", exc)
            return []

        recs: list[Recommendation] = []
        for assoc, product in rows:
            key = str(product.id)
            if key in excluded:
                continue
            if product.stock_quantity == 0:
                continue
            recs.append(
                Recommendation(
                    product_id=product.id,
                    product_name=product.name,
                    price=float(product.price),
                    stock_quantity=product.stock_quantity,
                    source="association",
                    score=float(assoc.confidence),
                    reasoning=(
                        f"Customers who ordered this also bought {product.name}."
                    ),
                )
            )

        return recs

    async def _vector_recs(
        self, product_ids: list[UUID], excluded: set[str]
    ) -> list[Recommendation]:
        """Use Qdrant's recommend API to find embedding-similar products."""
        if not product_ids:
            return []

        recs: list[Recommendation] = []
        seen: set[str] = set()

        for product_id in product_ids[:3]:   # cap at 3 anchors to avoid latency spike
            try:
                results = await self.qdrant.query_points(
                    collection_name=PRODUCTS_COLLECTION,
                    # Qdrant v1.10+: passing a point-ID string as `query` instructs
                    # the server to look up that vector and use it as the search query.
                    query=str(product_id),
                    using=None,
                    limit=5,
                    score_threshold=_VECTOR_SCORE_THRESHOLD,
                )
                results = results.points
            except Exception as exc:  # noqa: BLE001
                logger.warning("Qdrant vector-rec failed for %s: %s", product_id, exc)
                continue

            for hit in results:
                payload = hit.payload or {}
                pid_str = payload.get("product_id", "")
                if not pid_str or pid_str in excluded or pid_str in seen:
                    continue

                stock = int(payload.get("stock_quantity") or 0)
                if stock == 0:
                    continue

                seen.add(pid_str)
                recs.append(
                    Recommendation(
                        product_id=UUID(pid_str),
                        product_name=payload.get("name", ""),
                        price=float(payload.get("price") or 0.0),
                        stock_quantity=stock,
                        source="vector",
                        score=float(hit.score),
                        reasoning=(
                            f"You might also like {payload.get('name', 'this product')}, "
                            "which is similar to what you ordered."
                        ),
                    )
                )

        recs.sort(key=lambda r: r.score, reverse=True)
        return recs


# ── Convenience builder ───────────────────────────────────────────────

def build_upsell_message(rec: Recommendation) -> str:
    """
    Build the spoken upsell line the agent will say.

    Follows the plan spec:
      "By the way, many customers who ordered X also added a Y — it's $Z.
       Would you like to add one?"
    """
    if rec.source == "association":
        return (
            f"By the way, many customers who ordered this also picked up "
            f"the {rec.product_name} — it's ${rec.price:.2f}. "
            "Would you like to add one to your order?"
        )
    return (
        f"You might also like the {rec.product_name} (${rec.price:.2f}), "
        "which pairs well with what you just ordered. Interested?"
    )
