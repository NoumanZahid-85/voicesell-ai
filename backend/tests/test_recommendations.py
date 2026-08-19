"""
Phase 5 tests — Upsell & Recommendation Engine.

Tests are designed to run WITHOUT Docker or external services:
  - DB: aiosqlite in-memory (via conftest.py pattern)
  - Qdrant: mocked via unittest.mock so no network call
  - Association rule mining: pure unit-tests on the mlxtend logic

Run:
    uv run pytest tests/test_recommendations.py -v
"""

from __future__ import annotations

import uuid
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from sqlalchemy import event, insert, text
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.orm import sessionmaker

from app.db.models import (
    Base,
    Customer,
    Order,
    OrderItem,
    OrderStatus,
    Product,
    ProductAssociation,
    ProductCategory,
    RecommendationLog,
)
from app.services.recommendations import (
    Recommendation,
    RecommendationService,
    _ASSOC_WEIGHT,
    _VECTOR_WEIGHT,
    build_upsell_message,
)

# ── Test fixtures ─────────────────────────────────────────────────────

@pytest.fixture()
async def session():
    """In-memory aiosqlite session with all tables created.

    JSONB is a PostgreSQL type that doesn't exist in SQLite.  We temporarily
    replace the dimensions_json column's type with plain JSON before creating
    tables, then restore it after — so Product model stays intact for prod.
    """
    from sqlalchemy import JSON
    from sqlalchemy.dialects.postgresql import JSONB

    # Patch JSONB → JSON for SQLite compat
    _original_type = Product.__table__.c.dimensions_json.type
    Product.__table__.c.dimensions_json.type = JSON()

    engine = create_async_engine("sqlite+aiosqlite:///:memory:", echo=False)

    @event.listens_for(engine.sync_engine, "connect")
    def set_sqlite_pragma(dbapi_conn, _):
        cursor = dbapi_conn.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    async_session = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with async_session() as s:
        yield s

    await engine.dispose()
    # Restore original JSONB type
    Product.__table__.c.dimensions_json.type = _original_type


@pytest.fixture()
async def seed_data(session: AsyncSession):
    """Seed two products, one customer, one order, one association rule."""
    cat = ProductCategory(id=uuid.uuid4(), name="Electronics")
    session.add(cat)

    p1 = Product(
        id=uuid.uuid4(),
        name="Wireless Keyboard",
        description="Compact 75% layout keyboard",
        category_id=cat.id,
        price=49.99,
        stock_quantity=100,
    )
    p2 = Product(
        id=uuid.uuid4(),
        name="Wrist Rest",
        description="Memory foam wrist support",
        category_id=cat.id,
        price=12.99,
        stock_quantity=200,
    )
    p3 = Product(
        id=uuid.uuid4(),
        name="USB Hub",
        description="7-port USB 3.0 hub",
        category_id=cat.id,
        price=29.99,
        stock_quantity=0,  # out of stock — must be filtered out
    )
    session.add_all([p1, p2, p3])

    customer = Customer(
        id=uuid.UUID("00000000-0000-0000-0000-000000000001"),
        email="test@voicesell.ai",
        name="Test Customer",
    )
    session.add(customer)

    # Association rule: keyboard → wrist rest (strong rule)
    assoc = ProductAssociation(
        id=uuid.uuid4(),
        product_a_id=p1.id,
        product_b_id=p2.id,
        confidence=0.75,
        support=0.05,
        lift=2.5,
    )
    # Association rule: keyboard → USB hub (out-of-stock consequent)
    assoc_oos = ProductAssociation(
        id=uuid.uuid4(),
        product_a_id=p1.id,
        product_b_id=p3.id,
        confidence=0.60,
        support=0.03,
        lift=2.0,
    )
    session.add_all([assoc, assoc_oos])
    await session.commit()
    await session.refresh(p1)
    await session.refresh(p2)
    await session.refresh(p3)
    return {"p1": p1, "p2": p2, "p3": p3, "customer": customer, "assoc": assoc}


# ── Unit tests: build_upsell_message ─────────────────────────────────

def test_upsell_message_association():
    rec = Recommendation(
        product_id=uuid.uuid4(),
        product_name="Wrist Rest",
        price=12.99,
        stock_quantity=50,
        source="association",
        score=0.7,
        reasoning="",
    )
    msg = build_upsell_message(rec)
    assert "Wrist Rest" in msg
    assert "$12.99" in msg
    assert "customers who ordered" in msg.lower()


def test_upsell_message_vector():
    rec = Recommendation(
        product_id=uuid.uuid4(),
        product_name="Mouse Pad",
        price=9.99,
        stock_quantity=50,
        source="vector",
        score=0.55,
        reasoning="",
    )
    msg = build_upsell_message(rec)
    assert "Mouse Pad" in msg
    assert "$9.99" in msg
    assert "might also like" in msg.lower()


# ── Integration tests: RecommendationService ────────────────────────

@pytest.mark.asyncio
async def test_association_recs_returned(session: AsyncSession, seed_data):
    """Association rule lookup returns the in-stock product."""
    p1 = seed_data["p1"]
    p2 = seed_data["p2"]

    with patch.object(
        RecommendationService,
        "_vector_recs",
        new=AsyncMock(return_value=[]),  # disable vector branch
    ):
        svc = RecommendationService(session)
        recs = await svc.get_recommendations([p1.id])

    assert len(recs) >= 1
    assert any(r.product_name == p2.name for r in recs)


@pytest.mark.asyncio
async def test_out_of_stock_filtered(session: AsyncSession, seed_data):
    """Out-of-stock products are never returned as recommendations."""
    p1 = seed_data["p1"]
    p3 = seed_data["p3"]  # USB hub — out of stock

    with patch.object(RecommendationService, "_vector_recs", new=AsyncMock(return_value=[])):
        svc = RecommendationService(session)
        recs = await svc.get_recommendations([p1.id])

    # p3 has stock_quantity=0 → must not appear
    rec_ids = [str(r.product_id) for r in recs]
    assert str(p3.id) not in rec_ids


@pytest.mark.asyncio
async def test_exclusion_filter(session: AsyncSession, seed_data):
    """Products in exclude_ids are not recommended."""
    p1 = seed_data["p1"]
    p2 = seed_data["p2"]

    with patch.object(RecommendationService, "_vector_recs", new=AsyncMock(return_value=[])):
        svc = RecommendationService(session)
        # Exclude p2 (Wrist Rest) — the only strong recommendation
        recs = await svc.get_recommendations([p1.id], exclude_ids=[p2.id])

    assert not any(r.product_id == p2.id for r in recs)


@pytest.mark.asyncio
async def test_vector_fallback_used(session: AsyncSession, seed_data):
    """Vector similarity is used as fallback when no association rules exist."""
    p2 = seed_data["p2"]  # Wrist Rest — no association rules with other products
    p_new_id = uuid.uuid4()

    fake_vector_rec = Recommendation(
        product_id=p_new_id,
        product_name="Mouse Pad",
        price=9.99,
        stock_quantity=50,
        source="vector",
        score=1.0,   # after * _VECTOR_WEIGHT (0.3) = 0.3, exactly meets _MIN_SCORE
        reasoning="",
    )

    with patch.object(
        RecommendationService,
        "_vector_recs",
        new=AsyncMock(return_value=[fake_vector_rec]),
    ):
        svc = RecommendationService(session)
        # p2 has no association rules — only vector fallback
        recs = await svc.get_recommendations([p2.id])

    assert len(recs) >= 1
    assert recs[0].source == "vector"


@pytest.mark.asyncio
async def test_composite_score_weighting(session: AsyncSession, seed_data):
    """When both association and vector find the same product, scores are combined."""
    p1 = seed_data["p1"]
    p2 = seed_data["p2"]

    # Simulate vector also finding p2 with score 0.8
    fake_vector_rec = Recommendation(
        product_id=p2.id,
        product_name=p2.name,
        price=float(p2.price),
        stock_quantity=p2.stock_quantity,
        source="vector",
        score=0.8,
        reasoning="",
    )

    with patch.object(
        RecommendationService,
        "_vector_recs",
        new=AsyncMock(return_value=[fake_vector_rec]),
    ):
        svc = RecommendationService(session)
        recs = await svc.get_recommendations([p1.id])

    # p2 was found by association (confidence=0.75) and vector (score=0.8)
    # Combined = 0.75 * 0.7 + 0.8 * 0.3 = 0.525 + 0.24 = 0.765
    p2_rec = next((r for r in recs if r.product_id == p2.id), None)
    assert p2_rec is not None
    assert p2_rec.score == pytest.approx(0.75 * _ASSOC_WEIGHT + 0.8 * _VECTOR_WEIGHT, abs=0.01)


@pytest.mark.asyncio
async def test_recommendation_log_persisted(session: AsyncSession, seed_data):
    """log_recommendation() writes to recommendation_logs table."""
    p2 = seed_data["p2"]
    svc = RecommendationService(session)

    await svc.log_recommendation(
        session_id="test-session-123",
        product_id=p2.id,
        source="association",
        was_accepted=True,
    )

    # Verify the row exists
    from sqlalchemy import select
    rows = (await session.execute(select(RecommendationLog))).scalars().all()
    assert len(rows) == 1
    assert rows[0].session_id == "test-session-123"
    assert rows[0].recommended_product_id == p2.id
    assert rows[0].source == "association"
    assert rows[0].was_accepted == 1  # stored as int in SQLite


@pytest.mark.asyncio
async def test_limit_enforced(session: AsyncSession, seed_data):
    """get_recommendations always returns at most `limit` results."""
    p1 = seed_data["p1"]
    svc = RecommendationService(session)

    # Inject extra vector recs beyond the limit
    extras = [
        Recommendation(
            product_id=uuid.uuid4(),
            product_name=f"Extra Product {i}",
            price=9.99,
            stock_quantity=10,
            source="vector",
            score=0.6 - i * 0.05,
            reasoning="",
        )
        for i in range(5)
    ]
    with patch.object(RecommendationService, "_vector_recs", new=AsyncMock(return_value=extras)):
        recs = await svc.get_recommendations([p1.id], limit=2)

    assert len(recs) <= 2


# ── Association rule mining: unit test (no DB needed) ────────────────

def test_apriori_produces_rules():
    """Smoke-test the mlxtend pipeline on a tiny synthetic dataset."""
    try:
        import pandas as pd
        from mlxtend.frequent_patterns import apriori, association_rules
        from mlxtend.preprocessing import TransactionEncoder
    except ImportError:
        pytest.skip("mlxtend not installed — run `uv add mlxtend pandas`")

    # Synthetic baskets: {A, B} together in 8/10 orders, but C is rare.
    # P(A)=0.9, P(B)=0.9, P(A∩B)=0.8 → lift = 0.8/(0.9*0.9) ≈ 0.99 (not enough)
    # Better: make A and B both rare but always co-occur:
    # P(A)=0.5, P(B)=0.5, P(A∩B)=0.5 → lift = 0.5/(0.5*0.5) = 2.0
    baskets = [["A", "B"]] * 5 + [["C"]] * 5   # A&B co-occur, C is independent
    te = TransactionEncoder()
    matrix = te.fit(baskets).transform(baskets)
    df = pd.DataFrame(matrix, columns=te.columns_)

    frequent = apriori(df, min_support=0.4, use_colnames=True)
    assert not frequent.empty, "Apriori should find frequent itemsets"

    rules = association_rules(frequent, metric="confidence", min_threshold=0.5)
    assert not rules.empty, "Should extract at least one rule"

    # A→B: confidence=1.0, lift = 1.0 / P(B) = 1.0/0.5 = 2.0 > 1.5 ✓
    strong = rules[(rules["confidence"] >= 0.3) & (rules["lift"] >= 1.5)]
    assert not strong.empty, f"Expected strong rule, got:\n{rules[['antecedents','consequents','confidence','lift']]}"
