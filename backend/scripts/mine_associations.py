"""
Phase 5 — Association rule mining script.

Reads order history from PostgreSQL, mines frequently-bought-together rules
using the Apriori algorithm (mlxtend), and stores the top rules in the
`product_associations` table for the upsell engine to consume.

Usage (from backend/ directory):
    uv run python scripts/mine_associations.py

Requirements:
    uv add mlxtend pandas   (added to pyproject.toml)

Plan spec thresholds:
    min_support    = 0.01   (1 % of orders must contain the pair)
    min_confidence = 0.30   (30 % of basket-A orders also contain basket-B)
    min_lift       = 1.50   (items are positively correlated — not just popular)
"""

from __future__ import annotations

import asyncio
import logging
import sys
import uuid
from pathlib import Path

# Allow `from app.*` imports when run from the backend/ directory
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)-7s | %(message)s",
)
logger = logging.getLogger(__name__)


async def main() -> None:
    # ── Lazy imports (keep CI light when mlxtend not installed) ──────
    try:
        import pandas as pd
        from mlxtend.frequent_patterns import apriori, association_rules
        from mlxtend.preprocessing import TransactionEncoder
    except ImportError:
        logger.error(
            "mlxtend and pandas are required. Run: uv add mlxtend pandas"
        )
        sys.exit(1)

    from sqlalchemy import text
    from sqlalchemy.dialects.postgresql import insert as pg_insert

    from app.db.models import ProductAssociation
    from app.db.session import get_engine

    engine = get_engine()

    # ── 1. Load order items ──────────────────────────────────────────
    logger.info("Loading order items from PostgreSQL …")
    async with engine.connect() as conn:
        rows = (await conn.execute(text(
            """
            SELECT o.id  AS order_id,
                   oi.product_id::text AS product_id
            FROM   orders o
            JOIN   order_items oi ON oi.order_id = o.id
            WHERE  o.status != 'cancelled'
            """
        ))).fetchall()

    if not rows:
        logger.warning("No order items found — have you run ingest_olist.py and placed test orders?")
        return

    df = pd.DataFrame(rows, columns=["order_id", "product_id"])
    logger.info("Loaded %d order-item rows from %d orders", len(df), df["order_id"].nunique())

    # ── 2. Filter to multi-item baskets ──────────────────────────────
    # Plan spec: single-item orders produce no associations
    basket_sizes = df.groupby("order_id")["product_id"].nunique()
    multi_order_ids = basket_sizes[basket_sizes >= 2].index
    df = df[df["order_id"].isin(multi_order_ids)]
    logger.info(
        "After filtering single-item orders: %d rows, %d multi-item orders",
        len(df), df["order_id"].nunique(),
    )

    if df.empty:
        logger.warning("No multi-item orders — cannot mine associations. Try placing multi-product test orders.")
        return

    # ── 3. Create transaction baskets ────────────────────────────────
    baskets: list[list[str]] = (
        df.groupby("order_id")["product_id"]
        .apply(list)
        .tolist()
    )
    logger.info("Created %d transaction baskets", len(baskets))

    # ── 4. One-hot encode ────────────────────────────────────────────
    te = TransactionEncoder()
    te_array = te.fit(baskets).transform(baskets)
    df_encoded = pd.DataFrame(te_array, columns=te.columns_)
    logger.info("Encoded %d unique products × %d baskets", len(te.columns_), len(baskets))

    # ── 5. Apriori ───────────────────────────────────────────────────
    min_support = 0.01
    logger.info("Running Apriori (min_support=%.2f) …", min_support)
    try:
        frequent = apriori(df_encoded, min_support=min_support, use_colnames=True)
    except Exception as exc:
        logger.error("Apriori failed: %s", exc)
        return

    if frequent.empty:
        # Relax support for small datasets
        min_support = 0.001
        logger.warning("No frequent itemsets at 0.01 — retrying with min_support=%.3f", min_support)
        frequent = apriori(df_encoded, min_support=min_support, use_colnames=True)

    if frequent.empty:
        logger.warning("Still no frequent itemsets — dataset may be too small for meaningful rules.")
        return

    logger.info("Found %d frequent itemsets", len(frequent))

    # ── 6. Extract association rules ─────────────────────────────────
    min_confidence = 0.30
    rules_df = association_rules(frequent, metric="confidence", min_threshold=min_confidence)
    rules_df = rules_df[rules_df["lift"] >= 1.5]   # positive correlation only

    logger.info(
        "Extracted %d rules (confidence >= %.2f, lift >= 1.5)",
        len(rules_df), min_confidence,
    )

    if rules_df.empty:
        logger.warning("No qualifying association rules — try lowering thresholds or adding more orders.")
        return

    # Only keep single-antecedent → single-consequent rules for simplicity
    rules_df = rules_df[
        (rules_df["antecedents"].apply(len) == 1) &
        (rules_df["consequents"].apply(len) == 1)
    ].copy()
    logger.info("Single-item rules: %d", len(rules_df))

    if rules_df.empty:
        logger.warning("No single-item rules — all rules involve multiple antecedents.")
        return

    # ── 7. Log top rule ──────────────────────────────────────────────
    top = rules_df.sort_values("confidence", ascending=False).iloc[0]
    top_a = next(iter(top["antecedents"]))
    top_b = next(iter(top["consequents"]))
    logger.info(
        "Top rule: %s → %s (confidence=%.3f, lift=%.3f, support=%.4f)",
        top_a, top_b,
        top["confidence"], top["lift"], top["support"],
    )

    # ── 8. Upsert into product_associations ─────────────────────────
    logger.info("Upserting rules into product_associations …")
    inserted = 0
    skipped = 0

    async with engine.begin() as conn:
        for _, row in rules_df.iterrows():
            a_id_str = next(iter(row["antecedents"]))
            b_id_str = next(iter(row["consequents"]))

            # Validate UUIDs (product_id column stores UUID strings)
            try:
                a_id = uuid.UUID(a_id_str)
                b_id = uuid.UUID(b_id_str)
            except ValueError:
                skipped += 1
                continue

            stmt = pg_insert(ProductAssociation).values(
                id=uuid.uuid4(),
                product_a_id=a_id,
                product_b_id=b_id,
                confidence=float(row["confidence"]),
                support=float(row["support"]),
                lift=float(row["lift"]),
            ).on_conflict_do_update(
                constraint="uq_product_association",
                set_={
                    "confidence": float(row["confidence"]),
                    "support":    float(row["support"]),
                    "lift":       float(row["lift"]),
                },
            )
            await conn.execute(stmt)
            inserted += 1

    logger.info(
        "Mined %d association rules from %d orders. Inserted/updated: %d, skipped (bad UUID): %d",
        len(rules_df),
        df["order_id"].nunique(),
        inserted,
        skipped,
    )
    logger.info(
        "Top rule: %s → %s (confidence: %.3f, lift: %.3f)",
        top_a, top_b, top["confidence"], top["lift"],
    )


if __name__ == "__main__":
    asyncio.run(main())
