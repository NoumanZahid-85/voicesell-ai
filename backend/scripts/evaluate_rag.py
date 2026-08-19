"""
Golden-dataset RAG evaluation.

Measures:
  - Retrieval Recall@5: is the expected product category in the top-5 chunks?
  - Answer Faithfulness: for known questions the answer contains an expected
    keyword and does NOT say "I don't have information"; for unknown questions
    the answer DOES say it (no hallucination).

Usage:
    uv run python -m scripts.evaluate_rag              # recall + answers
    uv run python -m scripts.evaluate_rag --recall-only  # skip LLM calls

Pass/fail thresholds (Definition of Done):
    Retrieval Recall@5  > 85%
    Answer Faithfulness > 90%
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import sys
from pathlib import Path

# Add backend to path so we can import app modules
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.db.session import get_engine, get_session_factory  # noqa: E402
from app.services.agent import build_agent_graph  # noqa: E402
from app.services.rag import RAGService  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(levelname)-7s | %(message)s")
logger = logging.getLogger(__name__)

GOLDEN_PATH = Path(__file__).resolve().parent / "data" / "golden_questions.json"
FAILURES_PATH = Path(__file__).resolve().parent / "data" / "eval_failures.json"
RECALL_THRESHOLD = 0.85
FAITHFULNESS_THRESHOLD = 0.90


def load_golden() -> list[dict]:
    with open(GOLDEN_PATH, encoding="utf-8") as f:
        return json.load(f)


def check_recall(chunks: list, expected_category: str | None) -> bool:
    """Recall@5: expected category present among retrieved chunk categories."""
    if expected_category is None:
        return True  # unknown questions aren't scored on retrieval
    return any(c.category == expected_category for c in chunks)


def _denies(reply: str) -> bool:
    """Does the reply decline to answer (any common phrasing)?"""
    r = reply.lower()
    return "don't have" in r or "do not have" in r or "can't help" in r or "cannot help" in r


def check_faithfulness(reply: str, item: dict) -> bool:
    denied = _denies(reply)
    if item["is_unknown"]:
        return denied
    if denied:
        return False
    keywords = item.get("expected_keywords") or []
    return any(kw.lower() in reply.lower() for kw in keywords)


async def main() -> None:
    parser = argparse.ArgumentParser(description="Evaluate RAG against the golden dataset")
    parser.add_argument("--recall-only", action="store_true", help="skip answer generation")
    args = parser.parse_args()

    golden = load_golden()
    logger.info("Loaded %d golden questions", len(golden))

    factory = get_session_factory()
    async with factory() as session:
        rag = RAGService(session)

        recall_hits = 0
        faith_total = 0
        faith_hits = 0
        failures: list[str] = []

        for item in golden:
            q = item["question"]
            chunks = await rag.retrieve(q, top_k=5)

            if check_recall(chunks, item["expected_category"]):
                recall_hits += 1
            else:
                failures.append(f"RECALL miss ({item['expected_category']}): {q}")

            if not args.recall_only:
                graph = build_agent_graph(session)
                state = await graph.ainvoke(
                    {
                        "user_message": q,
                        "history": [],
                        "intent": "",
                        "context": "",
                        "chunks": [],
                        "reply": "",
                    }
                )
                faith_total += 1
                if check_faithfulness(state["reply"], item):
                    faith_hits += 1
                else:
                    failures.append(f"FAITH miss: {q} → {state['reply'][:120]!r}")

    recall_rate = recall_hits / len(golden)
    print("-" * 60)
    print(f"Retrieval Recall@5: {recall_rate:.0%} ({recall_hits}/{len(golden)})")

    if args.recall_only or faith_total == 0:
        print("Answer Faithfulness: skipped (run without --recall-only to evaluate)")
        faith_rate = None
    else:
        faith_rate = faith_hits / faith_total
        print(f"Answer Faithfulness: {faith_rate:.0%} ({faith_hits}/{faith_total})")

    # Failures to a UTF-8 file (console may be cp1252)
    if failures:
        with open(FAILURES_PATH, "w", encoding="utf-8") as f:
            json.dump(failures, f, ensure_ascii=False, indent=2)
        print(f"Failures written to {FAILURES_PATH.name} ({len(failures)} entries)")
        for f in failures[:10]:
            print("  -", f.replace("→", "->").replace("—", "-")[:140])

    ok = recall_rate >= RECALL_THRESHOLD
    if faith_rate is not None and not (faith_rate >= FAITHFULNESS_THRESHOLD):
        ok = False

    print("-" * 60)
    targets = f"recall>{RECALL_THRESHOLD:.0%}"
    if faith_rate is not None:
        targets += f", faithfulness>{FAITHFULNESS_THRESHOLD:.0%}"
    print(f"RESULT: {'PASS' if ok else 'FAIL'}  (targets: {targets})")

    await get_engine().dispose()
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    asyncio.run(main())
