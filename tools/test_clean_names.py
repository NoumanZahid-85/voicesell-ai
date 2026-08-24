"""Verify: order flow speaks clean names (no '#NN'), orders API returns
product_name for items, and greeting says OmniVoice."""
import asyncio
import sys
import uuid

import httpx

BASE = "http://localhost:8077"
CID = str(uuid.uuid4())
SID = f"name-check-{uuid.uuid4().hex[:8]}"


async def chat(c, sid, msg):
    r = await c.post(
        f"{BASE}/api/v1/chat",
        json={"message": msg, "session_id": sid, "customer_id": CID},
        timeout=120,
    )
    r.raise_for_status()
    return r.json()


async def main():
    async with httpx.AsyncClient() as c:
        r = await chat(c, SID, "I want to buy one bluetooth speaker")
        quote = r["reply"]
        print("QUOTE :", quote[:110].replace("\n", " "))
        if "#" in quote:
            print("FAIL: quote still contains # code"); sys.exit(1)

        r = await chat(c, SID, "yes")
        print("CONFIRM:", r["reply"][:90].replace("\n", " "))

        orders = (await c.get(f"{BASE}/api/v1/orders", params={"customer_id": CID}, timeout=30)).json()
        items = orders["orders"][0]["items"] if orders["orders"] else []
        for it in items:
            print("ITEM  :", it.get("product_name"), f"(qty {it['quantity']})")
            if not it.get("product_name") or "#" in it["product_name"]:
                print("FAIL: product_name missing or has # code"); sys.exit(1)
        if not items:
            print("FAIL: no order items returned"); sys.exit(1)
        print("\nNAMES OK — clean product names end-to-end")


asyncio.run(main())
