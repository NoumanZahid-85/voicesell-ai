"""
RAG retrieval service with hybrid search (vector + keyword).

Why hybrid: pure vector search misses exact product codes and specific
terminology. Production teams combine dense retrieval (Qdrant) with sparse
keyword matching (PostgreSQL ILIKE / full-text) for the best of both.
"""

from __future__ import annotations

import logging
import re
from dataclasses import asdict, dataclass
from uuid import UUID

from qdrant_client.models import FieldCondition, Filter, MatchValue, Range
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.services.embeddings import get_embedder
from app.services.qdrant_client import PRODUCTS_COLLECTION, get_qdrant_client

logger = logging.getLogger(__name__)

_STOPWORDS = {
    "a",
    "an",
    "the",
    "and",
    "or",
    "of",
    "for",
    "with",
    "you",
    "have",
    "do",
    "what",
    "which",
    "how",
    "much",
    "does",
    "can",
    "tell",
    "me",
    "about",
    "any",
    "your",
    "store",
    "i",
    "we",
    "in",
    "on",
    "at",
    "to",
    "is",
    "are",
}


@dataclass
class RetrievedChunk:
    """A single retrieved product with its retrieval score."""

    product_id: UUID
    name: str
    category: str
    price: float
    stock_quantity: int
    description: str
    score: float

    def to_dict(self) -> dict:
        return {**asdict(self), "product_id": str(self.product_id)}


def _extract_keywords(query: str) -> list[str]:
    """Lowercase, strip punctuation, drop stopwords/short tokens."""
    tokens = re.findall(r"[a-z0-9]+", query.lower())
    return [t for t in tokens if len(t) > 2 and t not in _STOPWORDS]


_NUMBER_WORDS: dict[str, float] = {
    "ten": 10.0,
    "twenty": 20.0,
    "thirty": 30.0,
    "forty": 40.0,
    "fifty": 50.0,
    "sixty": 60.0,
    "seventy": 70.0,
    "eighty": 80.0,
    "ninety": 90.0,
    "one hundred": 100.0,
    "hundred": 100.0,
}


def extract_max_price(query: str) -> float | None:
    """Parse a 'under $50 / below fifty dollars' style price cap from a query."""
    text = query.lower()
    digit = re.search(r"(?:under|below|less than)\s+(?:usd\s*)?\$?\s*(\d+)", text)
    if digit:
        return float(digit.group(1))

    word = re.search(r"(?:under|below|less than)\s+(?:usd\s*)?([a-z][a-z ]+?)dollars?", text)
    if word:
        phrase = word.group(1).strip()
        for key, value in sorted(_NUMBER_WORDS.items(), key=len, reverse=True):
            if key in phrase:
                return value
    return None


class RAGService:
    """Retrieval + context formatting backed by Qdrant and PostgreSQL."""

    def __init__(self, session: AsyncSession) -> None:
        self.session = session
        self.qdrant = get_qdrant_client()
        self.settings = get_settings()
        self.embedder = get_embedder()

    async def retrieve(
        self,
        query: str,
        top_k: int | None = None,
        category: str | None = None,
        max_price: float | None = None,
    ) -> list[RetrievedChunk]:
        """
        Retrieve top-k product chunks for a query.

        Strategy: dense vector search on Qdrant first. If the top vector score
        is below threshold, OR the result set is thin, merge in keyword matches
        from PostgreSQL so exact product codes/names aren't missed.
        """
        top_k = top_k or self.settings.rag_top_k

        # 1) Dense retrieval
        query_vector = self.embedder.embed_one(query)
        search_filter = self._build_filter(category, max_price)

        vector_hits = await self.qdrant.query_points(
            collection_name=PRODUCTS_COLLECTION,
            query=query_vector,
            limit=top_k * 2,
            query_filter=search_filter,
        )
        vector_hits = vector_hits.points

        chunks: dict[str, RetrievedChunk] = {}
        for hit in vector_hits:
            payload = hit.payload or {}
            product_id = payload.get("product_id")
            if not product_id:
                continue
            chunks[product_id] = RetrievedChunk(
                product_id=UUID(product_id),
                name=payload.get("name", ""),
                category=payload.get("category", ""),
                price=float(payload.get("price") or 0.0),
                stock_quantity=int(payload.get("stock_quantity") or 0),
                description=payload.get("description", ""),
                score=float(hit.score),
            )

        # 2) Keyword (BM25-style) fallback — catches exact names/codes
        best_score = vector_hits[0].score if vector_hits else 0.0
        if best_score < self.settings.rag_vector_score_threshold or len(chunks) < top_k:
            keyword_hits = await self._keyword_search(query, top_k, category, max_price)
            for chunk in keyword_hits:
                chunks.setdefault(str(chunk.product_id), chunk)

        ranked = sorted(chunks.values(), key=lambda c: c.score, reverse=True)
        return ranked[:top_k]

    def _build_filter(self, category: str | None, max_price: float | None) -> Filter | None:
        """Build a Qdrant metadata filter from optional constraints."""
        conditions = []
        if category:
            conditions.append(FieldCondition(key="category", match=MatchValue(value=category)))
        if max_price is not None:
            conditions.append(FieldCondition(key="price", range=Range(lte=max_price)))
        return Filter(must=conditions) if conditions else None

    async def _keyword_search(
        self,
        query: str,
        top_k: int,
        category: str | None,
        max_price: float | None,
    ) -> list[RetrievedChunk]:
        """Rank products by keyword overlap using PostgreSQL ILIKE."""
        keywords = _extract_keywords(query)
        if not keywords:
            return []

        patterns = [f"%{kw}%" for kw in keywords]
        category_clause = "AND pc.name = :category" if category else ""
        price_clause = "AND p.price <= :max_price" if max_price is not None else ""
        sql = text(
            f"""
            SELECT p.id, p.name, p.description, p.price, p.stock_quantity,
                   COALESCE(pc.name, '') AS category_name
            FROM products p
            LEFT JOIN product_categories pc ON p.category_id = pc.id
            WHERE p.name ILIKE ANY(:patterns) OR p.description ILIKE ANY(:patterns)
            {category_clause}
            {price_clause}
            LIMIT :limit
            """
        )
        params: dict = {"patterns": patterns, "limit": top_k * 5}
        if category:
            params["category"] = category
        if max_price is not None:
            params["max_price"] = max_price

        rows = (await self.session.execute(sql, params)).all()
        if not rows:
            return []

        hits: list[RetrievedChunk] = []
        for row in rows:
            name, description = row.name, row.description
            matched = sum(1 for kw in keywords if kw in name.lower() or kw in description.lower())
            ratio = matched / len(keywords)
            hits.append(
                RetrievedChunk(
                    product_id=row.id,
                    name=name,
                    category=row.category_name or "",
                    price=float(row.price),
                    stock_quantity=row.stock_quantity,
                    description=description,
                    score=min(0.9, 0.5 + 0.15 * ratio),  # synthetic score, sorts after solid vector hits
                )
            )

        hits.sort(key=lambda c: c.score, reverse=True)
        return hits[:top_k]

    @staticmethod
    def format_context(chunks: list[RetrievedChunk]) -> str:
        """Format retrieved chunks into a numbered context block for the LLM."""
        blocks = []
        for i, c in enumerate(chunks, start=1):
            stock = "in stock" if c.stock_quantity > 0 else "out of stock"
            blocks.append(f"[{i}] {c.name} — ${c.price:.2f} ({stock}). Category: {c.category}. {c.description}")
        return "\n\n".join(blocks)
