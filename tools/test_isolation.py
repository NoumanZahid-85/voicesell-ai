"""Isolation test: two identities place orders via the chat agent path,
then each lists its own orders. Guest A must not see Guest B's order."""
import asyncio
import sys
import uuid

import httpx

import os

BASE = os.environ.get("ISO_BASE_URL", "http://localhost:8077")
A = str(uuid.uuid4())  # guest in browser 1
B = str(uuid.uuid4())  # guest in incognito window
SA = f"iso-a-{uuid.uuid4().hex[:8]}"
SB = f"iso-b-{uuid.uuid4().hex[:8]}"


async def chat(c, sid, cid, msg):
    r = await c.post(
        f"{BASE}/api/v1/chat",
        json={"message": msg, "session_id": sid, "customer_id": cid},
        timeout=90,
    )
    r.raise_for_status()
    return r.json()


async def main():
    async with httpx.AsyncClient() as c:
        health = await c.get(f"{BASE}/health", timeout=10)
        print("health:", health.json()["status"])

        r = await chat(c, SA, A, "I want to buy one bluetooth speaker")
        print("A quote :", r["reply"][:80].replace("\n", " "))
        if "72.67" not in r["reply"]:
            print("FAIL: unexpected quote for A"); sys.exit(1)

        r = await chat(c, SA, A, "yes")
        print("A confirm:", r["reply"][:70].replace("\n", " "))
        if "placed" not in r["reply"].lower():
            print("FAIL: order not placed for A"); sys.exit(1)

        ra = await c.get(f"{BASE}/api/v1/orders", params={"customer_id": A}, timeout=30)
        rb = await c.get(f"{BASE}/api/v1/orders", params={"customer_id": B}, timeout=30)
        la, lb = ra.json()["orders"], rb.json()["orders"]

        a_sees = len(la) > 0
        b_clean = len(lb) == 0
        print(f"A sees {len(la)} order(s): {'PASS' if a_sees else 'FAIL'}")
        print(f"B sees {len(lb)} order(s): {'PASS' if b_clean else 'FAIL (leak!)'}")
        if a_sees and b_clean:
            print(f"\nISOLATION OK — ids were:\n  A={A}\n  B={B}")
        else:
            sys.exit(1)


asyncio.run(main())
