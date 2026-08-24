"""One-off: purge poisoned FAQ cache entries (apology fallbacks) from Redis."""
import asyncio
import os
import re
import sys

sys.path.insert(0, "backend")
from dotenv import load_dotenv

load_dotenv(".env")
import redis.asyncio as aioredis

APOLOGY = re.compile(r"(i'm sorry|i don't have|we don't have|don't have the product list)", re.I)


async def main() -> None:
    r = aioredis.from_url(
        os.environ["REDIS_URL"],
        decode_responses=True,
        ssl_cert_reqs=None,  # match app/cache.py — Upstash cert chain quirks
    )
    poisoned, total = [], 0
    async for key in r.scan_iter("faq:*", count=200):
        total += 1
        val = await r.get(key)
        if val and APOLOGY.search(val):
            poisoned.append(key)
    for k in poisoned:
        await r.delete(k)
    print(f"scanned {total} faq keys, deleted {len(poisoned)} poisoned")
    await r.aclose()


if __name__ == "__main__":
    asyncio.run(main())
